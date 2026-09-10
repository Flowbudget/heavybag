# Changelog

## 0.1.1 (2026-09-10)

- `push`, and with it `run`, no longer deletes files on the host that a job
  wrote there. Before, the next `run` removed results that had not been pulled
  yet. A push now deletes on the host only files that an earlier push put there
  and that are gone locally. Files pushed by 0.1.0 are not on that list, so
  deleting them locally leaves them on the host.
- `.gitignore` is honoured when the project is a subdirectory of a larger
  repository, not only at the repository root.
- Install with `pipx install git+https://github.com/Flowbudget/heavybag`.
  heavybag is not published on PyPI.

## 0.1.0 (2026-09-07)

First release.

- `heavybag run`: rsync the project to an ssh host, start the command in its
  own session, stream the log, pull results, return the command's exit code.
- Runs directly, in a Docker image (`--docker`) or in a conda environment
  (`--conda`); `setup` line for virtualenvs and modules.
- Jobs survive dropped connections and Ctrl-C. `attach`, `ps`, `kill`,
  `logs`, `pull`, `push`, `rm`.
- Optional `heavybag.toml` in the project root. Nothing else to configure.
- Works with macOS's built-in openrsync on the laptop side.
- Zero dependencies beyond the Python standard library (3.11+).
