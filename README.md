# heavybag

Punch your code over to the GPU box and watch it run.

[![CI](https://github.com/Flowbudget/heavybag/actions/workflows/ci.yml/badge.svg)](https://github.com/Flowbudget/heavybag/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

```bash
heavybag run --host gpu-box python train.py
```

That one line syncs your project to the host, starts the command there, streams
its output into your terminal, and copies the results back when it is done.
Close the laptop in the middle of it. The job keeps running.

## The problem

You write code on a laptop. The machine with the GPU is somewhere else: a Linux
box in the basement, a workstation at the institute, a rented server. Every run
is the same dance. `rsync` the code over. `ssh` in. `cd` to the right place.
Remember `nohup` or `tmux`, or the run dies with the WiFi. Tail the log.
`scp` the results back. Forget one step and you debug the wrong version.

Tools for this exist, but most of them assume a cloud account, a cluster
scheduler, a daemon on the host, or a config file before the first run.

## What heavybag does

- **Push**: `rsync` your project directory to the host. Respects `.gitignore`.
- **Run**: starts your command in its own session on the host, so it survives
  a dropped connection. Optionally inside a Docker image or a conda environment.
- **Watch**: streams the log into your terminal, live.
- **Pull**: brings results back with `rsync` when the job ends.
- **Control**: `attach` to a running job again, `ps` to list jobs, `kill` to
  stop one, `logs` to read what it printed.

No daemon on the host. No agent. Nothing to install there beyond `ssh`,
`rsync` and a POSIX shell. Nothing to install here beyond Python and the same
two tools, which macOS and Linux already ship.

## Try it without any configuration

You need an ssh host you can log into, ideally with a key. `gpu-box` below can
be an alias from `~/.ssh/config` or plain `user@192.168.1.20`.

```bash
pipx install heavybag        # or: pip install heavybag

cd my-project
heavybag run --host gpu-box python train.py
```

What you see:

```
heavybag: pushed 14 files to gpu-box:.heavybag/projects/my-project
heavybag: job 20260907-143201-7c1e started on gpu-box (direct)
epoch 1/10  loss 2.31
epoch 2/10  loss 1.87
...
epoch 10/10 loss 0.41
heavybag: job 20260907-143201-7c1e finished with exit 0
heavybag: pulled 3 files into /Users/you/my-project
```

The job's own output goes to stdout, heavybag's messages go to stderr, so
`heavybag run ... > train.log` does what you expect. The exit code of
`heavybag run` is the exit code of your command.

Press Ctrl-C and heavybag only detaches. The job keeps running:

```
heavybag: detached. The job keeps running on the host.
heavybag:   watch again:  heavybag attach 20260907-143201-7c1e
heavybag:   stop it:      heavybag kill 20260907-143201-7c1e
```

`heavybag attach` picks up the log from the beginning and pulls the results
when the job ends. Without a job id it takes the latest job you started from
this directory.

## Everyday use: heavybag.toml

Put a `heavybag.toml` into your project root so you can drop the flags.
Every key is optional. Flags on the command line win over the file.

```toml
host = "gpu-box"
remote_dir = "~/runs/my-project"   # default: ~/.heavybag/projects/<directory name>

# Never pushed, never pulled. Added to the built-in list
# (.git, __pycache__, .venv, node_modules, ...). .gitignore is honoured on push.
exclude = ["data/raw", "*.ckpt"]

# What to bring back after a run. Without this, heavybag pulls everything on
# the host that is newer than your local copy, but never deletes local files.
pull = ["outputs/", "checkpoints/best.pt"]

# A shell line that runs on the host before the command.
setup = ". .venv/bin/activate"

[docker]
image = "pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime"
args = ["--gpus", "all", "--shm-size", "8g"]
```

Then:

```bash
heavybag run python train.py --epochs 50
```

Everything after the command belongs to the command. Use `--` if your command
starts with a dash.

Port, user name and key are ssh's business. Put them into `~/.ssh/config`:

```
Host gpu-box
  HostName 192.168.1.20
  User you
  Port 2222
  IdentityFile ~/.ssh/id_ed25519
```

## Docker

```bash
heavybag run --docker pytorch/pytorch --docker-arg=--gpus=all python train.py
```

heavybag mounts the project directory as `/work`, makes it the working
directory, and runs the container as your user id so the output files belong
to you, not to root. Put `root = true` under `[docker]` if the image needs
root. Extra `docker run` flags go into `args` in the config or into
`--docker-arg`, one flag per use.

The container gets a name (`heavybag-<job id>`), so `heavybag kill` can stop
it cleanly.

## conda

```bash
heavybag run --conda ml python train.py
```

This runs `conda run -n ml --no-capture-output --live-stream python train.py`.
heavybag looks for `conda` in the PATH and in the usual install locations
(`~/miniforge3`, `~/miniconda3`, `~/anaconda3`, `/opt/conda`). Use `conda =
"ml"` in the config to make it stick.

## Virtualenv, modules, anything else

`setup` is one shell line that runs on the host right before your command,
in the same shell. Use it for `. .venv/bin/activate`, `module load cuda/12.4`,
`export HF_HOME=/scratch/hf`, or a combination joined with `&&`.

The command runs under `bash -l` when bash exists on the host, so the PATH
from your `~/.profile` applies. That is usually enough for `~/.local/bin`.

## Commands

| Command | What it does |
|---|---|
| `heavybag run [options] COMMAND...` | push, start, stream, pull |
| `heavybag run -d ...` | start and return at once |
| `heavybag attach [JOB]` | stream a job's log again, pull when it ends |
| `heavybag ps [-a]` | running jobs on the host; `-a` includes finished ones |
| `heavybag kill [JOB] [-t SEC]` | TERM the job's process group, KILL after SEC seconds (default 10) |
| `heavybag logs [JOB] [-f]` | print the log; `-f` keeps following |
| `heavybag pull [JOB]` | bring results back without running anything |
| `heavybag push` | sync the project to the host without running anything |
| `heavybag rm JOB...` | delete a finished job's directory on the host |

`JOB` can be the full id or any unique part of it, such as the last four
characters. Left out, it means the latest job started from the current
directory.

Useful `run` flags: `--host`, `--docker IMAGE`, `--conda ENV`, `--setup LINE`,
`--pull PATH` (repeatable), `--exclude PATTERN` (repeatable), `--no-pull`,
`--no-push`, `--shell` (the single argument is a shell command line, for
pipes and `&&`), `--remote-dir DIR`, `--ssh-arg ARG`.

## What happens on the host

Everything lives under `~/.heavybag` on the host:

```
~/.heavybag/
  projects/my-project/        the synced copy of your project
  jobs/20260907-143201-7c1e/
    cmd        the script that was run (cd, setup, exec your command)
    command    the command as you typed it
    log        stdout and stderr of your command
    pid        process group id
    exit       exit code, written when the command ends
    started    UTC timestamp
```

The job is started with `setsid`, so it belongs to no terminal and no ssh
session. A small wrapper writes the exit code when it ends. `heavybag attach`
is `tail -f` on the log until the process group is gone. `heavybag kill`
sends TERM to the process group, waits, then KILL. Nothing here needs a
daemon, and you can look at every file with plain `ssh`.

Because plain files are the source of truth, `heavybag ps` works from any
laptop that can reach the host.

## Requirements

On the laptop: Python 3.11 or newer, `ssh`, `rsync`. macOS ships both
(the built-in openrsync is fine). Linux has them or is one package away.
Windows works through WSL.

On the host: Linux or macOS with ssh access, `rsync`, `bash` or `sh`, and
GNU or BSD coreutils. `setsid` comes with util-linux on every Linux; on a
macOS host heavybag falls back to `python3`. Docker or conda only if you use
them.

heavybag uses one ssh connection for the whole run (`ControlMaster`), so a
password or key passphrase is asked once, not five times. Set
`HEAVYBAG_NO_CONTROL_MASTER=1` if that gets in the way.

## Questions

**Does the job really survive my laptop going to sleep?**
Yes. The job is not a child of the ssh session. Sleep the laptop, reconnect
later, run `heavybag attach`.

**Does `run` sync the whole directory every time?**
It runs `rsync`, so only changed files travel. Files that git ignores are not
pushed. Add `exclude` patterns for big data that git does not know about.

**Can it overwrite my local edits when it pulls?**
Not by default. Without a `pull` list heavybag runs `rsync --update`, which
skips files that are newer locally, and never deletes local files. With a
`pull` list only those paths are touched.

**Two projects with the same directory name?**
They would share `~/.heavybag/projects/<name>` on the host. Set `remote_dir`
in one of them.

**Can I use it from a script?**
`heavybag run` returns the job's exit code, so `heavybag run ... && next-step`
works. There is also a small Python API: `from heavybag import Settings,
Heavybag`. See the docstring in `heavybag/__init__.py`.

**Why not just ssh in and use tmux?**
You can, and for interactive work you should. heavybag is for the loop of
edit, run, look at numbers, edit again, where the sync and the bookkeeping
are the annoying part.

## Alternatives

- [remote-cli/remote](https://github.com/remote-cli/remote) is the closest
  relative: rsync, ssh, run, rsync back. It needs a config file before the
  first run, the job dies with the connection, and there is no `attach`,
  `ps` or `kill`.
- [pueue](https://github.com/Nukesor/pueue) is a fine job queue, but it runs
  as a daemon on the host and does not sync files.
- [SkyPilot](https://github.com/skypilot-org/skypilot) and
  [dstack](https://github.com/dstackai/dstack) manage fleets, clouds and
  reservations. Great when you have those, heavy when you have one box.
- [Fabric](https://github.com/fabric/fabric) is a library for scripting ssh,
  not a ready-made run command.
- VS Code Remote-SSH and JetBrains Gateway move your editor to the host. That
  works well until the connection drops or you want to work on the train.
- [mutagen](https://github.com/mutagen-io/mutagen) syncs files continuously
  and does that very well, but it does not run anything.

## Development

```bash
git clone https://github.com/Flowbudget/heavybag
cd heavybag
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The tests do not need a real host. A fake `ssh` runs every host-side script
locally in a temporary directory and a fake `rsync` rewrites the host path,
so the shell scripts, `setsid`, `tail` and the real `rsync` are exercised
without a network. Set `HEAVYBAG_TEST_HOST=localhost` to run the same suite
over real ssh; CI does that with a local sshd on Ubuntu.

## License

MIT. See [LICENSE](LICENSE).
