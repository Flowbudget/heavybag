"""The generated sh scripts must be valid sh and quote everything."""

import subprocess

import pytest

from heavybag import scripts


def sh_ok(script: str) -> None:
    subprocess.run(["sh", "-n"], input=script, text=True, check=True)


def test_direct_command_is_quoted() -> None:
    cmd = scripts.command_script(
        ["python", "train.py", "--name", "it's a test", "$HOME"],
        kind="direct",
        workdir="runs/my proj",
        job_id="j1",
    )
    assert cmd.splitlines()[0] == "cd 'runs/my proj' || exit 1"
    assert cmd.splitlines()[-1] == "exec python train.py --name 'it'\"'\"'s a test' '$HOME'"
    sh_ok(cmd)


def test_setup_line_runs_before_command() -> None:
    cmd = scripts.command_script(
        ["python", "x.py"], kind="direct", workdir="w", job_id="j", setup=". .venv/bin/activate"
    )
    assert cmd.splitlines()[1] == ". .venv/bin/activate"
    assert cmd.splitlines()[2] == "exec python x.py"


def test_shell_mode_passes_the_string_to_sh() -> None:
    cmd = scripts.command_script(
        ["python a.py && python b.py"], kind="direct", workdir="w", job_id="j", shell=True
    )
    assert cmd.splitlines()[-1] == "exec sh -c 'python a.py && python b.py'"
    sh_ok(cmd)


def test_docker_command() -> None:
    cmd = scripts.command_script(
        ["python", "train.py"],
        kind="docker",
        workdir="w",
        job_id="20260907-120000-ab12",
        docker_image="pytorch/pytorch:2.4",
        docker_args=["--gpus", "all", "--shm-size=8g"],
    )
    last = cmd.splitlines()[-1]
    assert last.startswith("exec docker run --rm --name heavybag-20260907-120000-ab12")
    assert '--user "$(id -u):$(id -g)"' in last
    assert '-v "$PWD:/work" -w /work --gpus all --shm-size=8g' in last
    assert last.endswith(" pytorch/pytorch:2.4 python train.py")
    sh_ok(cmd)


def test_docker_root_drops_user_flag() -> None:
    cmd = scripts.command_script(
        ["id"], kind="docker", workdir="w", job_id="j", docker_image="img", docker_root=True
    )
    assert "--user" not in cmd


def test_conda_command() -> None:
    cmd = scripts.command_script(
        ["python", "x.py"], kind="conda", workdir="w", job_id="j", conda="ml env"
    )
    assert cmd.splitlines()[-1] == (
        'exec "$HB_CONDA" run -n \'ml env\' --no-capture-output --live-stream python x.py'
    )
    assert "miniforge3" in cmd
    sh_ok(cmd)


def test_kind_without_its_argument_is_an_error() -> None:
    with pytest.raises(ValueError):
        scripts.command_script(["x"], kind="docker", workdir="w", job_id="j")
    with pytest.raises(ValueError):
        scripts.command_script(["x"], kind="conda", workdir="w", job_id="j")


def test_all_host_scripts_are_valid_sh() -> None:
    cmd = scripts.command_script(["echo", "hi"], kind="direct", workdir="w", job_id="j")
    sh_ok(scripts.start_script("20260907-120000-ab12", "w", cmd, "echo hi", "direct"))
    sh_ok(scripts.list_script())
    sh_ok(scripts.status_script("j"))
    sh_ok(scripts.follow_script("j"))
    sh_ok(scripts.logs_script("j"))
    sh_ok(scripts.exit_script("j"))
    sh_ok(scripts.kill_script("j", 2.5))
    sh_ok(scripts.remove_script("j"))
    sh_ok(scripts.prepare_script("runs/x y"))


def test_start_script_refuses_heredoc_collision() -> None:
    with pytest.raises(ValueError):
        scripts.start_script("j1", "w", "echo HB_CMD_j1\n", "x", "direct")


def test_join_command() -> None:
    assert scripts.join_command(["python", "a b.py"], shell=False) == "python 'a b.py'"
    assert scripts.join_command(["python a.py | tee x"], shell=True) == "python a.py | tee x"
