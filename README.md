# connectvpn workbench

Terminal UI and CLI for managing Proton VPN/OpenVPN profiles on Linux.

`connectvpn` keeps one shared OpenVPN credential file and rewrites imported
`.ovpn` profiles to use it automatically. It is designed for people who have
multiple OpenVPN profiles from the same provider and do not want to type their
OpenVPN username/password on every connection.

## Features

- Curses-based TUI with keyboard shortcuts.
- One shared credential file for all imported profiles.
- Imports one `.ovpn` file or every `.ovpn` file in the current directory.
- Connects to a chosen server or a random server.
- Starts OpenVPN as a daemon and tracks PID/log state.
- Keeps credentials outside the repository with strict file permissions.
- No third-party Python dependencies.

## Requirements

- Linux
- Python 3.10+
- `openvpn`
- `sudo`

Fedora example:

```bash
sudo dnf install openvpn python3
```

## Quick Install

After the repository is public, users can install with:

```bash
curl -fsSL https://raw.githubusercontent.com/deepdevjose/connectvpn-workbench/main/scripts/install.sh | bash
```

The installer automatically tries to install or upgrade Python when `python3`
is missing or older than 3.10.

To let the installer also try to install other missing system dependencies,
such as OpenVPN and `tar`, on common distros:

```bash
curl -fsSL https://raw.githubusercontent.com/deepdevjose/connectvpn-workbench/main/scripts/install.sh | bash -s -- --install-deps
```

Safer manual review flow:

```bash
curl -fsSL https://raw.githubusercontent.com/deepdevjose/connectvpn-workbench/main/scripts/install.sh -o install-connectvpn.sh
less install-connectvpn.sh
bash install-connectvpn.sh
```

If you publish under a different repository name, replace
`deepdevjose/connectvpn-workbench` in the URL or run:

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_USER/YOUR_REPO/main/scripts/install.sh | bash -s -- --repo YOUR_USER/YOUR_REPO
```

## Compatibility

The installer targets mainstream Linux distributions with Python 3.10+,
OpenVPN, `sudo`, `tar`, and either `curl` or `wget`.

Automatic dependency installation is supported for common package managers:

- `apt-get` for Debian/Ubuntu
- `dnf` for Fedora/RHEL-family systems
- `yum` for older RHEL-family systems
- `pacman` for Arch-family systems
- `zypper` for openSUSE/SUSE
- `apk` for Alpine
- `xbps-install` for Void Linux

It should work on most modern Linux distros once those dependencies are
available. It cannot honestly guarantee every distro, especially very old,
minimal, immutable, container-based, or non-systemd/non-sudo setups.

## Install From Source

```bash
git clone https://github.com/YOUR_USER/connectvpn-workbench.git
cd connectvpn-workbench
./install.sh
```

The installer creates a symlink at:

```bash
~/.local/bin/connectvpn
```

Make sure `~/.local/bin` is in your `PATH`.

## First Run

```bash
connectvpn
```

On first launch, the TUI asks for your OpenVPN username and password once. The
credentials are stored at:

```bash
~/.config/connectvpn/authopenvpn.auth
```

The file is written with permission mode `600`.

## Import Profiles

From the TUI, use:

```text
a  Add a new .ovpn profile
i  Import all .ovpn profiles in the current folder
```

Or use the CLI:

```bash
connectvpn --import ./server.ovpn --name "My Server"
connectvpn --import-current
```

## Connect

```bash
connectvpn --list
connectvpn --connect "My Server"
connectvpn --random
connectvpn --status
connectvpn --disconnect
```

## Uninstall

From the TUI, use:

```text
u  Uninstall / remove from system
```

By default, uninstall removes the installed command and app files while keeping
your local config, imported profiles, credentials, logs, and state.

CLI:

```bash
connectvpn --uninstall
```

To remove user config and credentials too:

```bash
connectvpn --uninstall --purge
```

## TUI Shortcuts

```text
Enter  choose server and connect
r      connect to a random server
a      add one .ovpn profile
i      import .ovpn profiles from the current folder
m      modify global credentials
s      show status and latest log lines
d      disconnect
u      uninstall / remove from system
q      quit or go back
```

## Security Notes

Do not commit personal `.ovpn` files, credential files, or logs. This repository
ships with a `.gitignore` that ignores:

```text
*.ovpn
*.auth
authopenVPN.txt
auth*.txt
*.log
*.pid
```

The config JSON does not store passwords. It stores metadata and paths only.

## Development

Run tests:

```bash
python -m unittest discover
```

Run all local checks:

```bash
make test
make lint
make security-check
```

Run the local source tree:

```bash
./connectvpn --help
```
