"""The library behind the command line: push, start, follow, pull, and job control."""

from __future__ import annotations

import os
import secrets
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import scripts, state
from .config import Settings
from .ssh import Ssh

# Bytes of paths per ssh call when deleting on the host. Linux caps a single
# argument, and with it the whole remote command, at 128 KiB; base64 adds a third.
DELETE_BATCH_BYTES = 60_000


class ConnectionLost(Exception):
    """ssh went away while a job was still running. The job keeps running on the host."""


class JobNotFound(Exception):
    """No job with that id on the host."""


class SyncError(Exception):
    """rsync failed."""


class RemoteError(Exception):
    """A script on the host failed for a reason it explained on stderr."""


@dataclass
class SyncStats:
    files: int = 0
    deleted: int = 0

    def __str__(self) -> str:
        parts = [f"{self.files} file{'s' if self.files != 1 else ''}"]
        if self.deleted:
            parts.append(f"{self.deleted} deleted")
        return ", ".join(parts)


@dataclass
class JobInfo:
    id: str
    status: str  # running | finished | lost
    exit_code: int | None
    started: str
    kind: str
    workdir: str
    command: str

    @property
    def status_text(self) -> str:
        if self.status == "finished":
            return f"exit {self.exit_code}"
        return self.status


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _parse_status(line: str) -> JobInfo:
    fields = line.rstrip("\n").split("\t")
    fields += [""] * (6 - len(fields))
    job_id, raw, started, kind, workdir, command = fields[:6]
    if raw.startswith("exit:"):
        try:
            code: int | None = int(raw[5:])
        except ValueError:
            code = None
        status = "finished"
    elif raw == "running":
        status, code = "running", None
    else:
        status, code = "lost", None
    return JobInfo(job_id, status, code, started, kind or "direct", workdir, command.strip())


def _transferred(output: str) -> list[str]:
    """Paths of the regular files that rsync -i reports as transferred.

    GNU rsync writes an 11-character code, openrsync a 9-character one; both
    are followed by one space and the path.
    """
    paths: list[str] = []
    for line in output.splitlines():
        code, _, path = line.partition(" ")
        if len(code) > 2 and code[0] in "<>" and code[1] == "f" and path:
            paths.append(path)
    return paths


def _rsync_stats(output: str) -> SyncStats:
    return SyncStats(files=len(_transferred(output)))


def _inside(path: str) -> bool:
    """A relative path that stays inside the project directory."""
    parts = PurePosixPath(path).parts
    return bool(parts) and not path.startswith("/") and ".." not in parts


def _chunks(items: Sequence[str], limit: int = DELETE_BATCH_BYTES) -> Iterator[list[str]]:
    chunk: list[str] = []
    size = 0
    for item in items:
        length = len(item.encode()) + 3  # quotes and a space
        if chunk and size + length > limit:
            yield chunk
            chunk, size = [], 0
        chunk.append(item)
        size += length
    if chunk:
        yield chunk


def _git(project_dir: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None


def gitignored_paths(project_dir: Path) -> list[str]:
    """Paths git ignores in this project, as anchored rsync exclude patterns.

    The project may be a subdirectory of a larger repository; git then reports
    the paths relative to the project. A project that the enclosing repository
    ignores as a whole, such as one below a dotfiles repository in the home
    directory with `*` in its .gitignore, is not part of that repository, so
    its rules do not apply.
    """
    ignored = _git(project_dir, "check-ignore", "-q", ".")
    if ignored is None or ignored.returncode != 1:
        return []  # 0: the whole project is ignored, 128: not inside a repository
    result = _git(
        project_dir, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"
    )
    if result is None or result.returncode != 0:
        return []
    out: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", "surrogateescape").rstrip("/")
        out.append("/" + path)
    return out


class Heavybag:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ssh = Ssh(settings.require_host(), settings.ssh_args)
        self._exit_cache: dict[str, int | None] = {}

    # -- sync ---------------------------------------------------------------

    @property
    def remote_dir(self) -> str:
        return self.settings.remote_workdir

    def _remote(self, path: str = "") -> str:
        base = f"{self.ssh.host}:{self.remote_dir}"
        return f"{base}/{path}" if path else base + "/"

    def prepare(self) -> None:
        result = self.ssh.run_script(scripts.prepare_script(self.remote_dir))
        if result.returncode != 0:
            raise RemoteError(f"could not prepare {self.ssh.host} (ssh exit {result.returncode})")

    def push(self) -> SyncStats:
        """rsync the project directory to the host.

        Deletes on the host only files that an earlier push put there and that
        are gone locally. Whatever a job wrote on the host stays, pulled or not.
        """
        self.prepare()
        local = self.settings.project_dir
        key = (self.ssh.host, self.remote_dir, str(local.resolve()))
        pushed = {p for p in state.pushed_files(*key) if _inside(p)}
        gone = sorted(p for p in pushed if not os.path.lexists(local / p))
        self._delete(gone)
        args = ["-az", "-i"]
        for pattern in self.settings.all_excludes:
            args.append(f"--exclude={pattern}")
        ignored = gitignored_paths(local) if self.settings.use_gitignore else []
        exclude_path: str | None = None
        try:
            if ignored:
                fd, exclude_path = tempfile.mkstemp(prefix="heavybag-", suffix=".exclude")
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(ignored) + "\n")
                args.append(f"--exclude-from={exclude_path}")
            args += [str(local) + "/", self._remote()]
            result = self.ssh.rsync(args)
        finally:
            if exclude_path is not None:
                os.unlink(exclude_path)
        if result.returncode != 0:
            raise SyncError(f"rsync to {self.ssh.host} failed (exit {result.returncode})")
        sent = _transferred(result.stdout)
        state.save_pushed_files(*key, (pushed - set(gone)) | {p for p in sent if _inside(p)})
        return SyncStats(files=len(sent), deleted=len(gone))

    def _delete(self, paths: Sequence[str]) -> None:
        """Remove files from the project directory on the host, then directories left empty."""
        if not paths:
            return
        local = self.settings.project_dir
        parents = {str(parent) for p in paths for parent in PurePosixPath(p).parents}
        parents.discard(".")
        dirs = sorted(
            (d for d in parents if not (local / d).is_dir()),
            key=lambda d: d.count("/"),
            reverse=True,  # children before their parents
        )
        work = [scripts.delete_script(self.remote_dir, files=c) for c in _chunks(paths)]
        work += [scripts.delete_script(self.remote_dir, dirs=c) for c in _chunks(dirs)]
        for script in work:
            result = self.ssh.run_script(script)
            if result.returncode != 0:
                raise RemoteError(
                    f"could not delete files on {self.ssh.host} (exit {result.returncode})"
                )

    def pull(self, paths: Sequence[str] | None = None) -> SyncStats:
        """Bring results back. Never deletes anything locally.

        With paths (or settings.pull): only those files or directories.
        Without: everything on the host that is newer than the local copy.
        """
        local = self.settings.project_dir
        wanted = list(paths) if paths is not None else list(self.settings.pull)
        total = SyncStats()
        if not wanted:
            args = ["-az", "-i", "--update"]
            for pattern in self.settings.all_excludes:
                args.append(f"--exclude={pattern}")
            args += [self._remote(), str(local) + "/"]
            result = self.ssh.rsync(args)
            if result.returncode != 0:
                raise SyncError(f"rsync from {self.ssh.host} failed (exit {result.returncode})")
            return _rsync_stats(result.stdout)
        for item in wanted:
            rel = item.strip().strip("/")
            if not rel or rel.startswith(".."):
                raise SyncError(f"refusing to pull '{item}': paths must be inside the project")
            parent = local / Path(rel).parent
            parent.mkdir(parents=True, exist_ok=True)
            result = self.ssh.rsync(["-az", "-i", self._remote(rel), str(parent) + "/"])
            if result.returncode == 23:
                continue  # partial transfer: the path does not exist on the host (yet)
            if result.returncode != 0:
                raise SyncError(
                    f"rsync of {rel} from {self.ssh.host} failed (exit {result.returncode})"
                )
            stats = _rsync_stats(result.stdout)
            total.files += stats.files
            total.deleted += stats.deleted
        return total

    # -- jobs ---------------------------------------------------------------

    def start(self, argv: Sequence[str], *, shell: bool = False) -> str:
        """Start argv on the host in its own session and return the job id."""
        if not argv:
            raise ValueError("no command given")
        s = self.settings
        job_id = new_job_id()
        kind = s.kind
        cmd = scripts.command_script(
            argv,
            kind=kind,
            workdir=self.remote_dir,
            job_id=job_id,
            shell=shell,
            setup=s.setup,
            docker_image=s.docker_image,
            docker_args=s.docker_args,
            docker_root=s.docker_root,
            conda=s.conda,
        )
        display = scripts.join_command(argv, shell)
        result = self.ssh.run_script(
            scripts.start_script(job_id, self.remote_dir, cmd, display, kind)
        )
        if result.returncode != 0:
            raise RemoteError(
                f"could not start the job on {self.ssh.host} (exit {result.returncode})"
            )
        state.remember(
            state.JobRecord(
                id=job_id,
                host=self.ssh.host,
                remote_dir=self.remote_dir,
                local_dir=str(s.project_dir.resolve()),
                command=display,
                started=time.strftime("%Y-%m-%dT%H:%M:%S"),
                pull=list(s.pull),
                exclude=list(s.exclude),
                ssh_args=list(s.ssh_args),
                kind=kind,
            )
        )
        return job_id

    def follow(self, job_id: str) -> Iterator[str]:
        """Yield log lines from the start until the job ends.

        Raises ConnectionLost if ssh drops before the job ends, JobNotFound if
        the host has no such job. Afterwards exit_code() answers without a
        round trip.
        """
        proc = self.ssh.stream_script(scripts.follow_script(job_id))
        assert proc.stdout is not None
        sentinel_seen = False
        pending: str | None = None  # the blank line printed before the sentinel
        try:
            for line in proc.stdout:
                if line.startswith(scripts.EXIT_SENTINEL):
                    sentinel_seen = True
                    value = line[len(scripts.EXIT_SENTINEL) :].strip()
                    self._exit_cache[job_id] = None if value == scripts.LOST else int(value)
                    pending = None
                    break
                if pending is not None:
                    yield pending
                    pending = None
                if line == "\n":
                    pending = line
                else:
                    yield line
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait()
        if sentinel_seen:
            return
        if proc.returncode == 3:
            raise JobNotFound(job_id)
        raise ConnectionLost(job_id)

    def exit_code(self, job_id: str) -> int | None:
        """The job's exit code, or None if the host lost it (reboot, kill -9 of the wrapper)."""
        if job_id in self._exit_cache:
            return self._exit_cache[job_id]
        result = self.ssh.run_script(scripts.exit_script(job_id))
        if result.returncode == 3:
            raise JobNotFound(job_id)
        if result.returncode != 0:
            raise ConnectionLost(job_id)
        value = result.stdout.strip()
        code = None if value == scripts.LOST or not value else int(value)
        self._exit_cache[job_id] = code
        return code

    def status(self, job_id: str) -> JobInfo:
        result = self.ssh.run_script(scripts.status_script(job_id))
        if result.returncode == 3:
            raise JobNotFound(job_id)
        if result.returncode != 0:
            raise ConnectionLost(job_id)
        return _parse_status(result.stdout)

    def jobs(self) -> list[JobInfo]:
        result = self.ssh.run_script(scripts.list_script())
        if result.returncode != 0:
            raise RemoteError(f"could not list jobs on {self.ssh.host} (exit {result.returncode})")
        return [_parse_status(line) for line in result.stdout.splitlines() if line.strip()]

    def resolve(self, token: str) -> str:
        """Turn a full or partial job id into the full id, using the host's job list."""
        jobs = self.jobs()
        for job in jobs:
            if job.id == token:
                return job.id
        matches = [job.id for job in jobs if job.id.endswith(token) or token in job.id]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise JobNotFound(token)
        raise JobNotFound(f"{token} is ambiguous: {', '.join(matches)}")

    def kill(self, job_id: str, timeout: float = 10.0) -> str:
        """TERM the job's process group, KILL after timeout. Returns what happened."""
        result = self.ssh.run_script(scripts.kill_script(job_id, timeout))
        if result.returncode == 3:
            raise JobNotFound(job_id)
        if result.returncode != 0:
            raise RemoteError(f"kill failed on {self.ssh.host} (exit {result.returncode})")
        self._exit_cache.pop(job_id, None)
        return result.stdout.strip()

    def logs(self, job_id: str) -> Iterator[str]:
        proc = self.ssh.stream_script(scripts.logs_script(job_id))
        assert proc.stdout is not None
        try:
            yield from proc.stdout
        finally:
            proc.wait()
        if proc.returncode == 3:
            raise JobNotFound(job_id)

    def remove(self, job_id: str) -> None:
        result = self.ssh.run_script(scripts.remove_script(job_id))
        if result.returncode == 3:
            raise JobNotFound(job_id)
        if result.returncode == 4:
            raise RemoteError("job is still running, kill it first")
        if result.returncode != 0:
            raise RemoteError(f"remove failed on {self.ssh.host} (exit {result.returncode})")
        state.forget(job_id)
