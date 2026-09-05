# Architecture

`connectvpn` is intentionally small and dependency-free. The current codebase
uses a `src/` layout so it can be installed as a Python package while still
supporting local development through the root `./connectvpn` wrapper.

## Runtime Flow

1. The CLI/TUI loads config from `~/.config/connectvpn/config.json`.
2. Credentials are validated from `~/.config/connectvpn/authopenvpn.auth`.
3. Imported `.ovpn` files are copied into `~/.config/connectvpn/profiles/`.
4. Each managed profile is patched to use the shared auth file through
   `auth-user-pass /path/to/authopenvpn.auth`.
5. Connections are started with `sudo openvpn --daemon`.
6. Runtime state is tracked in `~/.local/state/connectvpn/`.

## Important Boundaries

- Project files live in the repository.
- User profiles, credentials, logs, and PID files live outside the repository.
- The JSON config stores metadata and paths only.
- OpenVPN remains the network/security engine; `connectvpn` only orchestrates it.

## Main Components

- `connectvpn`: executable wrapper for local source-tree usage.
- `src/connectvpn/app.py`: CLI, TUI, profile import, credential management,
  config/state persistence, and OpenVPN process orchestration.
- `tests/`: unit tests for safe profile patching, credential storage, and config
  behavior.
- `scripts/check_no_sensitive_files.sh`: pre-publish safety check.

As the project grows, the natural next split is:

- `config.py` for config/state persistence.
- `profiles.py` for `.ovpn` import and patching.
- `credentials.py` for auth-file handling.
- `openvpn.py` for process orchestration.
- `tui.py` for the curses interface.
