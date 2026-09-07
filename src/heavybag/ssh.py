"""Thin wrappers around the ssh and rsync binaries.

Everything that talks to the host goes through here. Scripts are sent to the
host base64-encoded, so they run unchanged under any login shell.
"""

from __future__ import annotations

import base64
import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path

KEEPALIVE_ARGS = ["-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3"]


def control_args() -> list[str]:
    """Share one ssh connection between the push, start, follow and pull steps.

    The socket lives in ~/.ssh so the path stays short (unix sockets have a
    104 byte limit). Set HEAVYBAG_NO_CONTROL_MASTER=1 to switch this off.
    """
    if os.environ.get("HEAVYBAG_NO_CONTROL_MASTER"):
        return []
    ssh_dir = Path.home() / ".ssh"
    if " " in str(ssh_dir):
        return []
    try:
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        return []
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={ssh_dir}/hb-%C",
        "-o",
        "ControlPersist=120",
    ]


def encode_script(script: str) -> str:
    """Wrap a POSIX sh script so it survives any remote login shell (bash, zsh, fish)."""
    payload = base64.b64encode(script.encode()).decode()
    return f"printf %s {payload} | base64 -d | sh"


class Ssh:
    def __init__(self, host: str, extra_args: Sequence[str] = ()):
        self.host = host
        self.extra_args = list(extra_args)

    def base_args(self) -> list[str]:
        return ["ssh", *control_args(), *KEEPALIVE_ARGS, *self.extra_args]

    def rsync_transport(self) -> str:
        """The -e value for rsync. rsync splits it on whitespace."""
        return " ".join(shlex.quote(a) for a in self.base_args())

    def command_args(self, remote_command: str) -> list[str]:
        return [*self.base_args(), self.host, remote_command]

    def run_script(
        self, script: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess:
        """Run a sh script on the host; stdout captured, stderr passed through."""
        return subprocess.run(
            self.command_args(encode_script(script)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )

    def stream_script(self, script: str) -> subprocess.Popen:
        """Start a sh script on the host and return the process; read its stdout line by line."""
        return subprocess.Popen(
            self.command_args(encode_script(script)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def rsync(self, args: Sequence[str]) -> subprocess.CompletedProcess:
        """rsync with this host's transport; stdout captured for the -i summary."""
        return subprocess.run(
            ["rsync", "-e", self.rsync_transport(), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
