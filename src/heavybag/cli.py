"""The `heavybag` command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, state
from .client import ConnectionLost, Heavybag, JobNotFound, RemoteError, SyncError
from .config import ConfigError, Settings, load_settings

EXIT_INTERRUPTED = 130
EXIT_LOST = 75  # EX_TEMPFAIL: the job may still be running, attach later
EXIT_USAGE = 2


def say(message: str) -> None:
    """Tool messages go to stderr; stdout carries only the job's log."""
    print(f"heavybag: {message}", file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heavybag",
        description="Punch your code over to the GPU box and watch it run.",
        epilog="Options after the command belong to the command. "
        "Use -- before a command that starts with a dash.",
    )
    parser.add_argument("--version", action="version", version=f"heavybag {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        metavar="FILE",
        help="config file (default: heavybag.toml in this or a parent directory)",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    def add_host(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--host", metavar="HOST", help="ssh host or alias, e.g. gpu-box or user@10.0.0.5"
        )
        p.add_argument(
            "--ssh-arg",
            action="append",
            default=[],
            metavar="ARG",
            help="extra ssh argument, repeatable",
        )

    def add_sync(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--remote-dir",
            metavar="DIR",
            help="project directory on the host (default: ~/.heavybag/projects/<name>)",
        )
        p.add_argument(
            "--exclude",
            action="append",
            default=[],
            metavar="PATTERN",
            help="do not push or pull this pattern, repeatable",
        )

    def add_pull(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--pull",
            action="append",
            default=[],
            metavar="PATH",
            help="file or directory to pull after the run, repeatable",
        )

    run = sub.add_parser("run", help="push, run, stream the log, pull results")
    add_host(run)
    add_sync(run)
    add_pull(run)
    run.add_argument("--docker", metavar="IMAGE", help="run inside this Docker image")
    run.add_argument(
        "--docker-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra `docker run` argument, e.g. --docker-arg=--gpus=all, repeatable",
    )
    run.add_argument("--conda", metavar="ENV", help="run inside this conda environment")
    run.add_argument(
        "--setup",
        metavar="SHELL",
        help="shell line run before the command, e.g. '. .venv/bin/activate'",
    )
    run.add_argument(
        "--shell", action="store_true", help="the single argument is a shell command line"
    )
    run.add_argument("--no-pull", action="store_true", help="do not pull results afterwards")
    run.add_argument("--no-push", action="store_true", help="do not push first")
    run.add_argument("-d", "--detach", action="store_true", help="start the job and return at once")
    run.add_argument(
        "argv", nargs=argparse.REMAINDER, metavar="COMMAND", help="the command to run on the host"
    )

    attach = sub.add_parser("attach", help="stream the log of a running job, then pull")
    add_host(attach)
    attach.add_argument(
        "job", nargs="?", help="job id or a unique part of it (default: latest job of this project)"
    )
    attach.add_argument("--no-pull", action="store_true", help="do not pull results afterwards")

    ps = sub.add_parser("ps", help="list jobs on the host")
    add_host(ps)
    ps.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="show finished jobs too (default: only running and lost)",
    )

    kill = sub.add_parser("kill", help="stop a job (TERM, then KILL after a grace period)")
    add_host(kill)
    kill.add_argument(
        "job", nargs="?", help="job id or unique part (default: latest job of this project)"
    )
    kill.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=10.0,
        metavar="SEC",
        help="seconds between TERM and KILL (default 10)",
    )

    logs = sub.add_parser("logs", help="print a job's log")
    add_host(logs)
    logs.add_argument(
        "job", nargs="?", help="job id or unique part (default: latest job of this project)"
    )
    logs.add_argument(
        "-f", "--follow", action="store_true", help="keep following until the job ends"
    )

    pull = sub.add_parser("pull", help="bring results back without running anything")
    add_host(pull)
    add_sync(pull)
    add_pull(pull)
    pull.add_argument(
        "job", nargs="?", help="pull the results of this job into the directory it was started from"
    )

    push = sub.add_parser("push", help="sync the project to the host without running anything")
    add_host(push)
    add_sync(push)

    rm = sub.add_parser("rm", help="delete a finished job's directory on the host")
    add_host(rm)
    rm.add_argument("job", nargs="+", help="job id or unique part")

    return parser


def _settings(args: argparse.Namespace, **extra: object) -> Settings:
    return load_settings(
        config=args.config,
        host=getattr(args, "host", None),
        remote_dir=getattr(args, "remote_dir", None),
        exclude=getattr(args, "exclude", None),
        pull=getattr(args, "pull", None),
        ssh_args=getattr(args, "ssh_arg", None),
        **extra,  # type: ignore[arg-type]
    )


def _settings_from_record(record: state.JobRecord, args: argparse.Namespace) -> Settings:
    return Settings(
        host=getattr(args, "host", None) or record.host,
        remote_dir=record.remote_dir,
        exclude=list(record.exclude),
        pull=list(record.pull),
        ssh_args=list(record.ssh_args) + list(getattr(args, "ssh_arg", []) or []),
        project_dir=Path(record.local_dir),
    )


def _pick_job(
    args: argparse.Namespace, hb: Heavybag | None = None
) -> tuple[str, state.JobRecord | None]:
    """Resolve the job argument: local records first, then the host's list."""
    token = getattr(args, "job", None)
    if token:
        record = state.find(token)
        if record:
            return record.id, record
        if hb is None:
            settings = _settings(args)
            hb = Heavybag(settings)
        return hb.resolve(token), None
    record = state.latest(Path.cwd())
    if record is None:
        record = state.latest()
    if record is None:
        raise ConfigError(
            "no job given and none remembered; run `heavybag ps` to see jobs on the host"
        )
    say(f"using latest job {record.id} ({record.command})")
    return record.id, record


def _client_for(args: argparse.Namespace) -> tuple[Heavybag, str, state.JobRecord | None]:
    """Client, job id and record for attach/kill/logs/rm style commands."""
    token = getattr(args, "job", None)
    record = state.find(token) if token else None
    if record is None and not token:
        job_id, record = _pick_job(args)
    if record is not None:
        settings = _settings_from_record(record, args)
        if getattr(args, "host", None) is None and settings.host != record.host:
            settings.host = record.host
        return Heavybag(settings), record.id, record
    settings = _settings(args)
    hb = Heavybag(settings)
    return hb, hb.resolve(token), None


def _stream(hb: Heavybag, job_id: str) -> int | None:
    """Print the log until the job ends. Returns the exit code, or None when lost."""
    for line in hb.follow(job_id):
        sys.stdout.write(line)
        sys.stdout.flush()
    return hb.exit_code(job_id)


def _finish(hb: Heavybag, job_id: str, code: int | None, do_pull: bool) -> int:
    if code is None:
        say(f"job {job_id} was lost on the host (reboot?); its log is still there:")
        say(f"  heavybag logs {job_id}")
        rc = 1
    else:
        say(f"job {job_id} finished with exit {code}")
        rc = code
    if do_pull:
        stats = hb.pull()
        say(f"pulled {stats} into {hb.settings.project_dir}")
    return rc


def _interrupted(job_id: str) -> int:
    say("detached. The job keeps running on the host.")
    say(f"  watch again:  heavybag attach {job_id}")
    say(f"  stop it:      heavybag kill {job_id}")
    return EXIT_INTERRUPTED


def _lost(job_id: str) -> int:
    say("connection lost. The job keeps running on the host.")
    say(f"  watch again:  heavybag attach {job_id}")
    return EXIT_LOST


def cmd_run(args: argparse.Namespace) -> int:
    argv = list(args.argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise ConfigError("no command given, e.g. heavybag run --host gpu-box python train.py")
    if args.shell and len(argv) != 1:
        raise ConfigError("--shell takes exactly one argument: the command line as a string")
    settings = _settings(
        args,
        setup=args.setup,
        docker_image=args.docker,
        docker_args=args.docker_arg,
        conda=args.conda,
    )
    hb = Heavybag(settings)
    if not args.no_push:
        stats = hb.push()
        say(f"pushed {stats} to {hb.ssh.host}:{hb.remote_dir}")
    job_id = hb.start(argv, shell=args.shell)
    say(f"job {job_id} started on {hb.ssh.host} ({settings.kind})")
    if args.detach:
        say(f"  watch:  heavybag attach {job_id}")
        say(f"  stop:   heavybag kill {job_id}")
        return 0
    try:
        code = _stream(hb, job_id)
    except KeyboardInterrupt:
        return _interrupted(job_id)
    except ConnectionLost:
        return _lost(job_id)
    try:
        return _finish(hb, job_id, code, not args.no_pull)
    except KeyboardInterrupt:
        say(f"pull interrupted; run it again with: heavybag pull {job_id}")
        return EXIT_INTERRUPTED


def cmd_attach(args: argparse.Namespace) -> int:
    hb, job_id, _record = _client_for(args)
    try:
        code = _stream(hb, job_id)
    except KeyboardInterrupt:
        return _interrupted(job_id)
    except ConnectionLost:
        return _lost(job_id)
    try:
        return _finish(hb, job_id, code, not args.no_pull)
    except KeyboardInterrupt:
        say(f"pull interrupted; run it again with: heavybag pull {job_id}")
        return EXIT_INTERRUPTED


def cmd_ps(args: argparse.Namespace) -> int:
    hb = Heavybag(_settings(args))
    jobs = hb.jobs()
    if not args.all:
        jobs = [j for j in jobs if j.status != "finished"]
    if not jobs:
        hint = "" if args.all else " (use -a for finished ones)"
        say(f"no {'jobs' if args.all else 'running jobs'} on {hb.ssh.host}{hint}")
        return 0
    rows = [
        (j.id, j.status_text, j.started.replace("T", " ").rstrip("Z"), j.kind, j.workdir, j.command)
        for j in jobs
    ]
    header = ("JOB", "STATUS", "STARTED (UTC)", "KIND", "DIR", "COMMAND")
    widths = [max(len(r[i]) for r in (header, *rows)) for i in range(5)]
    for row in (header, *rows):
        cells = [row[i].ljust(widths[i]) for i in range(5)] + [row[5]]
        print("  ".join(cells))
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    hb, job_id, _record = _client_for(args)
    outcome = hb.kill(job_id, timeout=args.timeout)
    messages = {
        "terminated": f"job {job_id} stopped",
        "killed": f"job {job_id} did not stop on TERM, killed",
        "finished": f"job {job_id} had already finished",
        "lost": f"job {job_id} was already gone (lost)",
    }
    say(messages.get(outcome, outcome))
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    hb, job_id, _record = _client_for(args)
    if args.follow:
        try:
            code = _stream(hb, job_id)
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED
        except ConnectionLost:
            return _lost(job_id)
        if code is None:
            say(f"job {job_id} was lost on the host")
            return 1
        say(f"job {job_id} finished with exit {code}")
        return code
    for line in hb.logs(job_id):
        sys.stdout.write(line)
    sys.stdout.flush()
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    if args.job:
        record = state.find(args.job)
        if record is None:
            raise ConfigError(
                f"job {args.job} is not in the local records; pull without a job id instead"
            )
        settings = _settings_from_record(record, args)
        settings.exclude += list(args.exclude)
        settings.pull += list(args.pull)
    else:
        settings = _settings(args)
    hb = Heavybag(settings)
    stats = hb.pull()
    say(f"pulled {stats} from {hb.ssh.host}:{hb.remote_dir} into {settings.project_dir}")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    hb = Heavybag(_settings(args))
    stats = hb.push()
    say(f"pushed {stats} to {hb.ssh.host}:{hb.remote_dir}")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    rc = 0
    for token in args.job:
        record = state.find(token)
        if record is not None:
            hb = Heavybag(_settings_from_record(record, args))
            job_id = record.id
        else:
            hb = Heavybag(_settings(args))
            job_id = hb.resolve(token)
        try:
            hb.remove(job_id)
            say(f"removed job {job_id} from {hb.ssh.host}")
        except (JobNotFound, RemoteError) as exc:
            say(f"{job_id}: {exc}")
            rc = 1
    return rc


COMMANDS = {
    "run": cmd_run,
    "attach": cmd_attach,
    "ps": cmd_ps,
    "kill": cmd_kill,
    "logs": cmd_logs,
    "pull": cmd_pull,
    "push": cmd_push,
    "rm": cmd_rm,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except ConfigError as exc:
        say(str(exc))
        return EXIT_USAGE
    except JobNotFound as exc:
        say(f"no such job: {exc}")
        return 1
    except (SyncError, RemoteError) as exc:
        say(str(exc))
        return 1
    except ConnectionLost as exc:
        say(f"connection to the host lost while working on job {exc}")
        return EXIT_LOST
    except KeyboardInterrupt:
        say("interrupted")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
