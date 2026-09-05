# connectvpn

Local TUI for managing OpenVPN profiles from the terminal.

> Publishing note: this repo includes `.gitignore` rules to keep personal
> `.ovpn` profiles, credentials, and logs out of Git.

## Install

Remote install after publishing:

```bash
curl -fsSL https://raw.githubusercontent.com/deepdevjose/vpn/main/scripts/install.sh | bash
```

The remote installer automatically tries to install or upgrade Python if
`python3` is missing or older than 3.10. Use `--install-deps` to let it also
install OpenVPN and other missing runtime dependencies on common distros.

Local install from a cloned repo:

```bash
./install.sh
```

Then open:

```bash
connectvpn
```

On first launch, if global credentials do not exist yet, the TUI asks for your
OpenVPN username and password once. After that, every imported server uses the
same credentials automatically.

To change them later, open `connectvpn` and choose `Modify global credentials`.

## Import A Profile

```bash
connectvpn --import ./server.ovpn --name "My Server"
```

You can also do this from the TUI with `Add a new .ovpn server` or
`Import .ovpn files from this folder`.

To load credentials from an existing `auth-user-pass` file:

```bash
connectvpn --set-credentials --auth-file ./authopenVPN.txt
```

## Paths

- Config JSON: `~/.config/connectvpn/config.json`
- Copied profiles: `~/.config/connectvpn/profiles/`
- Global credentials: `~/.config/connectvpn/authopenvpn.auth`
- State/logs: `~/.local/state/connectvpn/`

Credentials are stored outside the JSON with permission mode `600`. The JSON
stores paths and metadata only.

## Useful Commands

```bash
connectvpn --list
connectvpn --status
connectvpn --set-credentials
connectvpn --random
connectvpn --connect "My Server"
connectvpn --disconnect
connectvpn --uninstall
```

`connectvpn --uninstall` removes the installed command and app files, but keeps
config and credentials. Use `connectvpn --uninstall --purge` to remove local
config and credentials too.

## TUI Shortcuts

- `Enter`: choose a server and connect
- `r`: connect to a random server
- `a`: add a new `.ovpn` server
- `i`: import `.ovpn` files from the current folder
- `m`: modify global credentials
- `s`: show status/log
- `d`: disconnect
- `u`: uninstall / remove from system
- `q`: quit or go back

OpenVPN requires elevated privileges, so your system may ask for your `sudo`
password.
