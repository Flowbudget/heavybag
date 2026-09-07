"""Settings: defaults, the optional heavybag.toml, and command-line overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME = "heavybag.toml"
REMOTE_ROOT = ".heavybag"  # relative to the remote home directory

# Never pushed, never pulled. Config and CLI excludes are added on top.
DEFAULT_EXCLUDES = [
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
    ".DS_Store",
    CONFIG_NAME,
]

TOP_LEVEL_KEYS = {
    "host": str,
    "remote_dir": str,
    "exclude": list,
    "pull": list,
    "setup": str,
    "conda": str,
    "use_gitignore": bool,
    "ssh_args": list,
    "docker": dict,
}
DOCKER_KEYS = {"image": str, "args": list, "root": bool}


class ConfigError(Exception):
    """A problem with heavybag.toml or with the combination of options."""


@dataclass
class Settings:
    host: str | None = None
    remote_dir: str | None = None
    exclude: list[str] = field(default_factory=list)
    pull: list[str] = field(default_factory=list)
    setup: str | None = None
    docker_image: str | None = None
    docker_args: list[str] = field(default_factory=list)
    docker_root: bool = False
    conda: str | None = None
    use_gitignore: bool = True
    ssh_args: list[str] = field(default_factory=list)
    project_dir: Path = field(default_factory=Path.cwd)
    config_path: Path | None = None

    @property
    def project_name(self) -> str:
        return self.project_dir.resolve().name or "project"

    @property
    def remote_workdir(self) -> str:
        """Remote project directory. Relative paths are relative to the remote home."""
        if self.remote_dir:
            return normalize_remote_path(self.remote_dir)
        return f"{REMOTE_ROOT}/projects/{self.project_name}"

    @property
    def all_excludes(self) -> list[str]:
        seen: list[str] = []
        for pattern in DEFAULT_EXCLUDES + list(self.exclude):
            if pattern not in seen:
                seen.append(pattern)
        return seen

    @property
    def kind(self) -> str:
        if self.docker_image and self.conda:
            raise ConfigError("choose either docker or conda, not both")
        if self.docker_image:
            return "docker"
        if self.conda:
            return "conda"
        return "direct"

    def require_host(self) -> str:
        if not self.host:
            raise ConfigError(
                f'no host given: use --host gpu-box or put host = "gpu-box" into {CONFIG_NAME}'
            )
        return self.host


def normalize_remote_path(path: str) -> str:
    """'~/x' and '~' become home-relative paths; absolute paths stay absolute."""
    path = path.strip()
    if path == "~":
        return "."
    if path.startswith("~/"):
        path = path[2:]
    return path.rstrip("/") or "."


def find_config(start: Path | None = None) -> Path | None:
    """Walk up from start (default: cwd) and return the first heavybag.toml."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def _check_keys(table: dict[str, Any], allowed: dict[str, type], where: str) -> None:
    for key, value in table.items():
        if key not in allowed:
            valid = ", ".join(sorted(allowed))
            raise ConfigError(f"unknown key '{key}' in {where}; valid keys: {valid}")
        expected = allowed[key]
        if not isinstance(value, expected):
            raise ConfigError(
                f"'{key}' in {where} must be a {expected.__name__}, got {type(value).__name__}"
            )
        if expected is list and not all(isinstance(item, str) for item in value):
            raise ConfigError(f"'{key}' in {where} must be a list of strings")


def read_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    _check_keys(data, TOP_LEVEL_KEYS, str(path))
    if "docker" in data:
        _check_keys(data["docker"], DOCKER_KEYS, f"[docker] of {path}")
    return data


def load_settings(
    *,
    config: Path | None = None,
    cwd: Path | None = None,
    host: str | None = None,
    remote_dir: str | None = None,
    exclude: list[str] | None = None,
    pull: list[str] | None = None,
    setup: str | None = None,
    docker_image: str | None = None,
    docker_args: list[str] | None = None,
    conda: str | None = None,
    ssh_args: list[str] | None = None,
) -> Settings:
    """Merge defaults, heavybag.toml and command-line overrides.

    Lists from the command line are added to the ones from the file.
    Scalars from the command line replace the ones from the file.
    --docker on the command line switches conda off and vice versa.
    """
    cwd = (cwd or Path.cwd()).resolve()
    if config is None:
        config = find_config(cwd)
    elif not config.is_file():
        raise ConfigError(f"config file not found: {config}")
    data = read_config(config) if config else {}
    docker = data.get("docker", {})

    settings = Settings(
        host=host or data.get("host") or os.environ.get("HEAVYBAG_HOST") or None,
        remote_dir=remote_dir or data.get("remote_dir"),
        exclude=list(data.get("exclude", [])) + list(exclude or []),
        pull=list(data.get("pull", [])) + list(pull or []),
        setup=setup if setup is not None else data.get("setup"),
        docker_image=docker.get("image"),
        docker_args=list(docker.get("args", [])),
        docker_root=bool(docker.get("root", False)),
        conda=data.get("conda"),
        use_gitignore=bool(data.get("use_gitignore", True)),
        ssh_args=list(data.get("ssh_args", [])) + list(ssh_args or []),
        project_dir=config.parent if config else cwd,
        config_path=config,
    )
    if docker_image:
        settings.docker_image = docker_image
        settings.conda = None
    if docker_args:
        settings.docker_args = settings.docker_args + list(docker_args)
    if conda:
        settings.conda = conda
        settings.docker_image = None
    _ = settings.kind  # validate the docker/conda combination early
    return settings
