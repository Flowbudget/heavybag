"""POSIX sh scripts that run on the host.

Requirements on the host: sh, coreutils (tail, base64, date, kill, sleep)
and either setsid (util-linux, present on every Linux) or python3 (macOS).
bash is used for the job itself when present, as a login shell, so the PATH
from ~/.profile applies.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence

from .config import REMOTE_ROOT

JOBS_DIR = f"{REMOTE_ROOT}/jobs"
EXIT_SENTINEL = "__HEAVYBAG_EXIT__"
LOST = "lost"

CONDA_CANDIDATES = [
    "conda",
    "$HOME/miniforge3/bin/conda",
    "$HOME/mambaforge/bin/conda",
    "$HOME/miniconda3/bin/conda",
    "$HOME/anaconda3/bin/conda",
    "/opt/conda/bin/conda",
    "/opt/miniconda3/bin/conda",
    "/opt/homebrew/bin/conda",
]


def q(value: str) -> str:
    return shlex.quote(value)


def job_dir(job_id: str) -> str:
    return f"{JOBS_DIR}/{job_id}"


def join_command(argv: Sequence[str], shell: bool) -> str:
    """The command as the host shell will see it."""
    if shell:
        return argv[0]
    return " ".join(q(a) for a in argv)


def command_script(
    argv: Sequence[str],
    *,
    kind: str,
    workdir: str,
    job_id: str,
    shell: bool = False,
    setup: str | None = None,
    docker_image: str | None = None,
    docker_args: Sequence[str] = (),
    docker_root: bool = False,
    conda: str | None = None,
) -> str:
    """The script stored as <jobdir>/cmd and run by bash -l (or sh)."""
    lines = [f"cd {q(workdir)} || exit 1"]
    if setup:
        lines.append(setup)
    # With --shell the user's string goes through sh, so quoting stays theirs.
    inner = ["sh", "-c", argv[0]] if shell else list(argv)
    inner_q = " ".join(q(a) for a in inner)

    if kind == "docker":
        if not docker_image:
            raise ValueError("docker kind needs an image")
        user = "" if docker_root else ' --user "$(id -u):$(id -g)"'
        extra = "".join(" " + q(a) for a in docker_args)
        lines.append(
            f"exec docker run --rm --name {q('heavybag-' + job_id)}{user}"
            f' -v "$PWD:/work" -w /work{extra} {q(docker_image)} {inner_q}'
        )
    elif kind == "conda":
        if not conda:
            raise ValueError("conda kind needs an environment name")
        candidates = " ".join(f'"{c}"' if c.startswith("$") else c for c in CONDA_CANDIDATES)
        lines += [
            "HB_CONDA=",
            f"for c in {candidates}; do",
            '  if command -v "$c" >/dev/null 2>&1; then HB_CONDA=$c; break; fi',
            "done",
            'if [ -z "$HB_CONDA" ]; then',
            "  echo 'heavybag: conda not found on the host (looked in PATH, ~/miniforge3,"
            " ~/mambaforge, ~/miniconda3, ~/anaconda3, /opt/conda)' >&2",
            "  exit 127",
            "fi",
            f'exec "$HB_CONDA" run -n {q(conda)} --no-capture-output --live-stream {inner_q}',
        ]
    else:
        lines.append(f"exec {inner_q}")
    return "\n".join(lines) + "\n"


def start_script(job_id: str, workdir: str, cmd: str, display: str, kind: str) -> str:
    """Create the job directory and start the command in its own session.

    The wrapper writes its own pid (which is also the process group id), then
    runs the command with stdout and stderr going to the log, then writes the
    exit code. It traps TERM so that a `kill` of the process group still
    leaves an exit file behind.
    """
    j = q(job_dir(job_id))
    w = q(workdir)
    eof = "HB_CMD_" + job_id.replace("-", "_")
    if eof in cmd:
        raise ValueError("command contains the heredoc delimiter")
    return f"""set -e
J={j}
W={w}
mkdir -p "$J" "$W"
cat > "$J/cmd" <<'{eof}'
{cmd}{eof}
printf '%s\\n' {q(display)} > "$J/command"
printf '%s\\n' {q(kind)} > "$J/kind"
printf '%s\\n' "$W" > "$J/workdir"
date -u +%Y-%m-%dT%H:%M:%SZ > "$J/started"
if command -v bash >/dev/null 2>&1; then HB_SH="bash -l"; else HB_SH="sh"; fi
# hb_detach runs in a forked background subshell. It must exec, not call:
# dash keeps the original stdout on a spare fd while a redirection is active,
# and a subshell that waits for its child would hold that fd, and with it the
# ssh session, open until the job ends.
hb_detach() {{
  if command -v setsid >/dev/null 2>&1; then
    exec setsid "$@"
  elif command -v python3 >/dev/null 2>&1; then
    exec python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
  else
    exec "$@"
  fi
}}
hb_detach sh -c '
  trap : TERM INT HUP
  echo $$ > "$1/pid"
  $3 "$1/cmd" > "$1/log" 2>&1
  echo $? > "$1/exit"
' hb "$J" "$W" "$HB_SH" </dev/null >/dev/null 2>&1 &
i=0
while [ ! -f "$J/pid" ] || [ ! -f "$J/log" ]; do
  i=$((i+1))
  if [ $i -gt 100 ]; then echo "heavybag: job did not start" >&2; exit 1; fi
  sleep 0.1
done
cat "$J/pid"
"""


def _status_line(var: str) -> str:
    """Emit one tab-separated status line for the job directory in $var."""
    return f"""d={var}
if [ ! -f "$d/pid" ]; then exit 3; fi
pid=$(cat "$d/pid")
if [ -f "$d/exit" ]; then st="exit:$(cat "$d/exit")"
elif kill -0 "$pid" 2>/dev/null; then st=running
else st={LOST}
fi
printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' "$(basename "$d")" "$st" \\
  "$(cat "$d/started" 2>/dev/null)" "$(cat "$d/kind" 2>/dev/null)" \\
  "$(cat "$d/workdir" 2>/dev/null)" \\
  "$(head -c 300 "$d/command" 2>/dev/null | tr '\\n\\t' '  ')"
"""


def list_script() -> str:
    return f"""cd {q(JOBS_DIR)} 2>/dev/null || exit 0
for j in */; do
  j=${{j%/}}
  [ -f "$j/pid" ] || continue
  (
{_status_line('"$j"')}
  )
done
"""


def status_script(job_id: str) -> str:
    return _status_line(q(job_dir(job_id)))


def follow_script(job_id: str) -> str:
    """Print the log from the start, keep following until the job is gone, then the exit code."""
    j = q(job_dir(job_id))
    return f"""J={j}
if [ ! -f "$J/pid" ]; then echo "heavybag: no such job on this host: {job_id}" >&2; exit 3; fi
pid=$(cat "$J/pid")
tail -n +1 -f "$J/log" </dev/null &
t=$!
while kill -0 "$pid" 2>/dev/null; do sleep 0.5; done
sleep 0.5
kill "$t" 2>/dev/null
wait "$t" 2>/dev/null
printf '\\n{EXIT_SENTINEL} %s\\n' "$(cat "$J/exit" 2>/dev/null || echo {LOST})"
"""


def logs_script(job_id: str) -> str:
    j = q(job_dir(job_id))
    return f"""J={j}
if [ ! -f "$J/log" ]; then echo "heavybag: no such job on this host: {job_id}" >&2; exit 3; fi
cat "$J/log"
"""


def exit_script(job_id: str) -> str:
    j = q(job_dir(job_id))
    return f"""J={j}
if [ ! -f "$J/pid" ]; then exit 3; fi
cat "$J/exit" 2>/dev/null || echo {LOST}
"""


def kill_script(job_id: str, timeout: float) -> str:
    j = q(job_dir(job_id))
    ticks = max(1, int(timeout * 10))
    return f"""J={j}
if [ ! -f "$J/pid" ]; then echo "heavybag: no such job on this host: {job_id}" >&2; exit 3; fi
pid=$(cat "$J/pid")
if [ -f "$J/exit" ]; then echo "finished"; exit 0; fi
if ! kill -0 "$pid" 2>/dev/null; then echo "{LOST}"; exit 0; fi
if [ "$(cat "$J/kind" 2>/dev/null)" = docker ]; then
  docker kill {q("heavybag-" + job_id)} >/dev/null 2>&1 || :
fi
# No "--" before the group id: dash's builtin kill rejects it, while a
# negative pid straight after the signal works in dash, bash, zsh and ash.
kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || :
i=0
while kill -0 "$pid" 2>/dev/null && [ $i -lt {ticks} ]; do sleep 0.1; i=$((i+1)); done
if kill -0 "$pid" 2>/dev/null; then
  kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || :
  sleep 0.2
  [ -f "$J/exit" ] || echo 137 > "$J/exit"
  echo "killed"
else
  [ -f "$J/exit" ] || echo 143 > "$J/exit"
  echo "terminated"
fi
"""


def remove_script(job_id: str) -> str:
    j = q(job_dir(job_id))
    return f"""J={j}
if [ ! -d "$J" ]; then echo "heavybag: no such job on this host: {job_id}" >&2; exit 3; fi
if [ ! -f "$J/exit" ] && [ -f "$J/pid" ] && kill -0 "$(cat "$J/pid")" 2>/dev/null; then
  echo "heavybag: job is still running, kill it first" >&2; exit 4
fi
rm -rf "$J"
"""


def delete_script(workdir: str, files: Sequence[str] = (), dirs: Sequence[str] = ()) -> str:
    """Remove files that an earlier push put on the host, then directories left empty.

    rmdir only removes empty directories, so whatever a job wrote next to the
    deleted files stays. A file rm cannot remove is reported on stderr and does
    not stop the push.
    """
    lines = [f"cd {q(workdir)} || exit 1"]
    if files:
        lines.append("rm -f -- " + " ".join(q(f) for f in files))
    if dirs:
        lines.append("rmdir -- " + " ".join(q(d) for d in dirs) + " 2>/dev/null")
    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def prepare_script(workdir: str) -> str:
    """Make sure the directories exist and rsync is installed before the first push."""
    return f"""mkdir -p {q(workdir)} {q(JOBS_DIR)}
if ! command -v rsync >/dev/null 2>&1; then
  echo "heavybag: rsync is not installed on the host (try: sudo apt install rsync)" >&2
  exit 5
fi
"""
