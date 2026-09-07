"""End-to-end tests through the command line, against the fake or a real host."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from conftest import REAL_HOST, Env, write

from heavybag import cli, state
from heavybag.client import ConnectionLost

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


def base(env: Env, *more: str) -> list[str]:
    return ["--host", env.host, "--remote-dir", env.remote_dir, *more]


def started_job(r) -> str:
    """The job id from the 'heavybag: job <id> started on <host>' message."""
    return next(line.split()[2] for line in r.err.splitlines() if " started on " in line)


def wait_for(path: Path, timeout: float = 10) -> None:
    deadline = time.time() + timeout
    while not path.exists():
        if time.time() > deadline:
            raise AssertionError(f"{path} did not appear")
        time.sleep(0.1)


def test_run_streams_log_and_pulls_results(env: Env) -> None:
    write(env.project / "train.py", "print('epoch 1'); open('out.txt', 'w').write('done')")
    write(env.project / ".git" / "HEAD", "ref: refs/heads/main")
    write(env.project / "__pycache__" / "x.pyc", "x")
    r = env.run("run", *base(env), "--", "python3", "train.py")
    assert r.code == 0, r.err
    assert r.out == "epoch 1\n"
    assert "pushed 1 file to" in r.err
    assert "finished with exit 0" in r.err
    assert "pulled 1 file into" in r.err
    assert (env.project / "out.txt").read_text() == "done"
    assert env.remote("train.py").exists()
    assert not env.remote(".git").exists()
    assert not env.remote("__pycache__").exists()


def test_run_uses_gitignore(env: Env) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=env.project, check=True)
    write(env.project / ".gitignore", "data/\n*.log\n")
    write(env.project / "data" / "big.bin", "x" * 10)
    write(env.project / "debug.log", "x")
    write(env.project / "keep.py", "print(1)")
    r = env.run("push", *base(env))
    assert r.code == 0, r.err
    assert env.remote("keep.py").exists()
    assert env.remote(".gitignore").exists()
    assert not env.remote("data").exists()
    assert not env.remote("debug.log").exists()


def test_exit_code_is_passed_through(env: Env) -> None:
    write(env.project / "fail.sh", "echo boom >&2\nexit 3\n")
    r = env.run("run", *base(env), "--", "sh", "fail.sh")
    assert r.code == 3
    assert "boom" in r.out  # stderr of the job lands in the log
    assert "finished with exit 3" in r.err


def test_shell_mode(env: Env) -> None:
    r = env.run("run", *base(env), "--shell", "--no-pull", "echo a && echo b | tr b c")
    assert r.code == 0, r.err
    assert r.out == "a\nc\n"


def test_setup_and_env(env: Env) -> None:
    r = env.run("run", *base(env), "--setup", "export GREETING=hello", "--no-pull", "--shell",
                'echo "$GREETING from $(pwd)"')
    assert r.code == 0, r.err
    assert r.out.startswith("hello from ")
    assert r.out.rstrip().endswith(Path(env.remote_dir).name)


def test_pull_list_only_brings_named_paths(env: Env) -> None:
    script = "mkdir -p out/sub; echo r > out/sub/res.txt; echo junk > junk.txt\n"
    write(env.project / "run.sh", script)
    r = env.run("run", *base(env), "--pull", "out/", "--", "sh", "run.sh")
    assert r.code == 0, r.err
    assert (env.project / "out" / "sub" / "res.txt").read_text() == "r\n"
    assert not (env.project / "junk.txt").exists()


def test_pull_never_overwrites_newer_local_files(env: Env) -> None:
    write(env.project / "notes.txt", "old")
    r = env.run("push", *base(env))
    assert r.code == 0, r.err
    write(env.project / "notes.txt", "local edit")
    now = time.time() + 100
    os.utime(env.project / "notes.txt", (now, now))
    r = env.run("pull", *base(env))
    assert r.code == 0, r.err
    assert (env.project / "notes.txt").read_text() == "local edit"


def test_detach_ps_attach_kill_logs_rm(env: Env) -> None:
    write(env.project / "slow.sh", "echo started; sleep 60; echo never")
    r = env.run("run", *base(env), "-d", "--", "sh", "slow.sh")
    assert r.code == 0, r.err
    job_id = started_job(r)
    assert job_id in env.job_ids()

    r = env.run("ps", "--host", env.host)
    assert job_id in r.out and "running" in r.out and "sh slow.sh" in r.out

    wait_for(env.remote_home / ".heavybag" / "jobs" / job_id / "log")
    r = env.run("logs", "--host", env.host, job_id[-4:])  # partial id
    assert r.code == 0 and "started" in r.out

    r = env.run("kill", "--host", env.host, job_id, "-t", "2")
    assert r.code == 0 and "stopped" in r.err

    r = env.run("ps", "--host", env.host)
    assert job_id not in r.out  # finished jobs are hidden by default
    r = env.run("ps", "--host", env.host, "-a")
    assert job_id in r.out and "exit 143" in r.out

    r = env.run("attach", "--host", env.host, job_id, "--no-pull")
    assert r.code == 143
    assert r.out.startswith("started\n") and "never" not in r.out

    r = env.run("rm", "--host", env.host, job_id)
    assert r.code == 0, r.err
    assert job_id not in env.job_ids()
    assert state.find(job_id) is None


def test_latest_job_is_the_default(env: Env) -> None:
    r = env.run("run", *base(env), "-d", "--shell", "echo one; sleep 30")
    assert r.code == 0, r.err
    r = env.run("kill")
    assert r.code == 0, r.err
    assert "using latest job" in r.err and "stopped" in r.err
    r = env.run("logs")
    assert r.code == 0 and r.out == "one\n"


def test_kill_escalates_to_sigkill(env: Env) -> None:
    write(env.project / "stubborn.sh", "trap '' TERM; echo up; sleep 60")
    r = env.run("run", *base(env), "-d", "--", "sh", "stubborn.sh")
    job_id = started_job(r)
    wait_for(env.remote_home / ".heavybag" / "jobs" / job_id / "log")
    started = time.time()
    r = env.run("kill", job_id, "-t", "1")
    assert r.code == 0 and "killed" in r.err
    assert time.time() - started < 8
    r = env.run("ps", "--host", env.host, "-a")
    assert "exit 137" in r.out


def test_rm_refuses_running_job(env: Env) -> None:
    r = env.run("run", *base(env), "-d", "--shell", "sleep 30")
    job_id = started_job(r)
    r = env.run("rm", job_id)
    assert r.code == 1 and "still running" in r.err
    env.run("kill", job_id, "-t", "1")


def test_missing_command_and_host(env: Env) -> None:
    r = env.run("run", "--host", env.host)
    assert r.code == 2 and "no command given" in r.err
    r = env.run("run", "--", "true")
    assert r.code == 2 and "no host given" in r.err


def test_unknown_job(env: Env) -> None:
    r = env.run("attach", "--host", env.host, "nope-1234")
    assert r.code == 1 and "no such job" in r.err


def test_interrupt_detaches_and_leaves_job_running(env: Env) -> None:
    def boom(self, job_id):
        raise KeyboardInterrupt
        yield  # pragma: no cover

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli.Heavybag, "follow", boom)
        r = env.run("run", *base(env), "--shell", "sleep 30")
    assert r.code == 130
    assert "detached" in r.err and "heavybag attach" in r.err
    job_id = started_job(r)
    r = env.run("ps", "--host", env.host)
    assert job_id in r.out and "running" in r.out
    env.run("kill", job_id, "-t", "1")


@pytest.mark.skipif(bool(REAL_HOST), reason="connection loss is only simulated with the fake ssh")
def test_connection_loss_then_attach(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    write(env.project / "job.sh", "echo start; sleep 3; echo end > result.txt; echo end")
    monkeypatch.setenv("HB_FAKE_SSH_DIE_AFTER", "1")
    r = env.run("run", *base(env), "--", "sh", "job.sh")
    assert r.code == cli.EXIT_LOST
    assert "connection lost" in r.err
    job_id = started_job(r)
    monkeypatch.delenv("HB_FAKE_SSH_DIE_AFTER")
    r = env.run("attach", job_id)
    assert r.code == 0, r.err
    assert r.out == "start\nend\n"
    assert (env.project / "result.txt").read_text() == "end\n"


@pytest.mark.skipif(bool(REAL_HOST), reason="needs the fake ssh")
def test_ssh_failure_is_reported(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HB_FAKE_SSH_FAIL", "1")
    r = env.run("ps", "--host", env.host)
    assert r.code == 1 and "could not list jobs" in r.err
    with pytest.raises(ConnectionLost):
        cli.Heavybag(cli.load_settings(cwd=env.project, host=env.host)).status("x")
