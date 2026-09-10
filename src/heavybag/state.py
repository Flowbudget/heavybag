"""Local records: the jobs started from here, and the files each push put on a host.

jobs.jsonl holds one JSON object per line, so `attach`, `pull` and `kill` work
without arguments. The host is the source of truth for job status; this file
only remembers where a job came from and where its results belong.

pushed/<key> lists the files that pushes of one local directory to one host
directory have transferred, one relative path per line. A push deletes on the
host only files from this list that are gone locally, so whatever a job wrote
on the host stays there.

Both live in $HEAVYBAG_HOME (default ~/.heavybag).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class JobRecord:
    id: str
    host: str
    remote_dir: str
    local_dir: str
    command: str
    started: str
    pull: list[str]
    exclude: list[str]
    ssh_args: list[str]
    kind: str = "direct"


def state_dir() -> Path:
    override = os.environ.get("HEAVYBAG_HOME")
    return Path(override) if override else Path.home() / ".heavybag"


def _records_path() -> Path:
    return state_dir() / "jobs.jsonl"


def remember(record: JobRecord) -> None:
    path = _records_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record)) + "\n")


def records() -> list[JobRecord]:
    path = _records_path()
    if not path.is_file():
        return []
    out: list[JobRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            out.append(JobRecord(**data))
        except (ValueError, TypeError):
            continue  # a damaged line must not break every command
    return out


def find(job_id: str) -> JobRecord | None:
    """Exact id first, then a unique id ending with or containing the given text."""
    known = records()
    for record in reversed(known):
        if record.id == job_id:
            return record
    matches = {r.id: r for r in known if r.id.endswith(job_id) or job_id in r.id}
    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


def latest(local_dir: Path | None = None, host: str | None = None) -> JobRecord | None:
    """Most recent job, optionally only for one project directory or one host."""
    wanted = str(local_dir.resolve()) if local_dir else None
    for record in reversed(records()):
        if wanted and record.local_dir != wanted:
            continue
        if host and record.host != host:
            continue
        return record
    return None


def forget(job_id: str) -> None:
    path = _records_path()
    if not path.is_file():
        return
    keep = [r for r in records() if r.id != job_id]
    with path.open("w", encoding="utf-8") as handle:
        for record in keep:
            handle.write(json.dumps(asdict(record)) + "\n")


def _pushed_path(host: str, remote_dir: str, local_dir: str) -> Path:
    key = hashlib.sha256(f"{host}\n{remote_dir}\n{local_dir}".encode()).hexdigest()[:16]
    return state_dir() / "pushed" / key


def pushed_files(host: str, remote_dir: str, local_dir: str) -> set[str]:
    """Files that earlier pushes of local_dir have put into remote_dir on host."""
    path = _pushed_path(host, remote_dir, local_dir)
    if not path.is_file():
        return set()
    return {line for line in path.read_text(encoding="utf-8").split("\n") if line}


def save_pushed_files(host: str, remote_dir: str, local_dir: str, files: set[str]) -> None:
    path = _pushed_path(host, remote_dir, local_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(f + "\n" for f in sorted(files)), encoding="utf-8")
    os.replace(tmp, path)
