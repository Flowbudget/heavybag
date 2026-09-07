# Changelog

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
