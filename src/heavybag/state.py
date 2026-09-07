"""Local record of started jobs, so `attach`, `pull` and `kill` work without arguments.

One JSON object per line in $HEAVYBAG_HOME/jobs.jsonl (default ~/.heavybag/jobs.jsonl).
The host is the source of truth for job status; this file only remembers where
a job came from and where its results belong.
"""

from __future__ import annotations

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
