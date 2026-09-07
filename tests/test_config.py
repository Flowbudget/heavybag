from pathlib import Path

import pytest

from heavybag.config import (
    DEFAULT_EXCLUDES,
    ConfigError,
    Settings,
    find_config,
    load_settings,
    normalize_remote_path,
)


def test_defaults_without_config(tmp_path: Path) -> None:
    s = load_settings(cwd=tmp_path, host="gpu-box")
    assert s.host == "gpu-box"
    assert s.project_dir == tmp_path
    assert s.remote_workdir == f".heavybag/projects/{tmp_path.name}"
    assert s.kind == "direct"
    assert s.all_excludes == DEFAULT_EXCLUDES
    assert s.config_path is None


def test_host_is_required() -> None:
    with pytest.raises(ConfigError, match="no host given"):
        Settings().require_host()


def test_config_file_is_found_in_parent(tmp_path: Path) -> None:
    (tmp_path / "heavybag.toml").write_text(
        'host = "gpu-box"\nremote_dir = "~/runs/demo"\nexclude = ["data"]\npull = ["out/"]\n'
        'setup = ". .venv/bin/activate"\n'
        '[docker]\nimage = "python:3.12"\nargs = ["--gpus", "all"]\n'
    )
    sub = tmp_path / "src" / "deep"
    sub.mkdir(parents=True)
    assert find_config(sub) == tmp_path / "heavybag.toml"
    s = load_settings(cwd=sub)
    assert s.host == "gpu-box"
    assert s.project_dir == tmp_path
    assert s.remote_workdir == "runs/demo"
    assert s.exclude == ["data"]
    assert "data" in s.all_excludes
    assert s.pull == ["out/"]
    assert s.setup == ". .venv/bin/activate"
    assert s.kind == "docker"
    assert s.docker_args == ["--gpus", "all"]


def test_cli_overrides_and_extends(tmp_path: Path) -> None:
    (tmp_path / "heavybag.toml").write_text(
        'host = "a"\nexclude = ["x"]\npull = ["p"]\nconda = "ml"\n'
    )
    s = load_settings(cwd=tmp_path, host="b", exclude=["y"], pull=["q"], docker_image="img")
    assert s.host == "b"
    assert s.exclude == ["x", "y"]
    assert s.pull == ["p", "q"]
    assert s.kind == "docker"  # --docker wins over conda from the file
    assert s.conda is None


def test_conda_on_cli_wins_over_docker_in_file(tmp_path: Path) -> None:
    (tmp_path / "heavybag.toml").write_text('host = "a"\n[docker]\nimage = "img"\n')
    s = load_settings(cwd=tmp_path, conda="ml")
    assert s.kind == "conda"
    assert s.docker_image is None


def test_docker_and_conda_together_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "heavybag.toml").write_text('conda = "ml"\n[docker]\nimage = "img"\n')
    with pytest.raises(ConfigError, match="either docker or conda"):
        load_settings(cwd=tmp_path)


def test_unknown_key_is_reported(tmp_path: Path) -> None:
    (tmp_path / "heavybag.toml").write_text('hots = "gpu-box"\n')
    with pytest.raises(ConfigError, match="unknown key 'hots'"):
        load_settings(cwd=tmp_path)


def test_wrong_type_is_reported(tmp_path: Path) -> None:
    (tmp_path / "heavybag.toml").write_text('exclude = "data"\n')
    with pytest.raises(ConfigError, match="must be a list"):
        load_settings(cwd=tmp_path)


def test_bad_toml_is_reported(tmp_path: Path) -> None:
    (tmp_path / "heavybag.toml").write_text("host = \n")
    with pytest.raises(ConfigError, match="heavybag.toml"):
        load_settings(cwd=tmp_path)


def test_explicit_missing_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(cwd=tmp_path, config=tmp_path / "nope.toml")


def test_env_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEAVYBAG_HOST", "env-box")
    assert load_settings(cwd=tmp_path).host == "env-box"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("~/runs/x", "runs/x"),
        ("~", "."),
        ("/srv/x/", "/srv/x"),
        ("rel/dir", "rel/dir"),
        ("~/", "."),
    ],
)
def test_normalize_remote_path(given: str, expected: str) -> None:
    assert normalize_remote_path(given) == expected
