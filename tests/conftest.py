"""Test setup.

By default every test talks to a fake host: tests/fakebin/ssh runs commands in
a temporary directory, tests/fakebin/rsync rewrites host:path to that directory
and calls the real rsync. The remote-side sh scripts, setsid, tail and rsync
therefore run for real, only the network is missing.

Set HEAVYBAG_TEST_HOST=localhost to run the same tests over real ssh. The host
must accept key-based logins for the current user without a passphrase prompt.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import pytest

from heavybag import cli

FAKEBIN = Path(__file__).parent / "fakebin"
REAL_HOST = os.environ.get("HEAVYBAG_TEST_HOST")


@dataclass
class Result:
    code: int
    out: str
    err: str


@dataclass
class Env:
    host: str
    remote_home: Path  # where host-relative paths land
    project: Path  # the local project directory (cwd during the test)
    remote_dir: str  # absolute remote_dir used for this test

    def run(self, *argv: str) -> Result:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return Result(code, out.getvalue(), err.getvalue())

    def remote(self, *parts: str) -> Path:
        return Path(self.remote_dir).joinpath(*parts)

    def job_ids(self) -> list[str]:
        jobs = self.remote_home / ".heavybag" / "jobs"
        return sorted(p.name for p in jobs.iterdir()) if jobs.is_dir() else []


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    project = tmp_path / "myproject"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HEAVYBAG_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("HEAVYBAG_HOST", raising=False)
    if REAL_HOST:
        remote_home = Path.home()
        remote_dir = str(remote_home / ".heavybag" / "test-projects" / tmp_path.name)
        host = REAL_HOST
    else:
        remote_home = tmp_path / "remote"
        remote_home.mkdir()
        remote_dir = str(remote_home / "work" / "myproject")
        host = "gpu-box"
        monkeypatch.setenv("HB_FAKE_REMOTE_HOME", str(remote_home))
        monkeypatch.setenv("HEAVYBAG_NO_CONTROL_MASTER", "1")
        monkeypatch.setenv("PATH", f"{FAKEBIN}{os.pathsep}{os.environ['PATH']}")
    e = Env(host=host, remote_home=remote_home, project=project, remote_dir=remote_dir)
    yield e
    # Leave nothing running behind and, on a real host, nothing on disk.
    # Only jobs this test started (listed in its own state file) are touched.
    records = tmp_path / "state" / "jobs.jsonl"
    own = records.read_text().split() if records.is_file() else []
    for job_id in e.job_ids():
        if job_id not in " ".join(own):
            continue
        job = e.remote_home / ".heavybag" / "jobs" / job_id
        if not (job / "exit").exists() and (job / "pid").exists():
            with contextlib.suppress(ProcessLookupError, PermissionError, ValueError):
                os.killpg(int((job / "pid").read_text()), 9)
        if REAL_HOST:
            subprocess.run(["rm", "-rf", str(job)], check=False)
    if REAL_HOST:
        subprocess.run(["rm", "-rf", remote_dir], check=False)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def python() -> str:
    """A python interpreter path that exists on the (fake or local) host."""
    return sys.executable
