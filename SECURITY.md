# Security Policy

`connectvpn` is a local helper around OpenVPN. It does not provide VPN service
itself and it does not replace OpenVPN or your VPN provider's security model.

## Credential Handling

- Credentials are stored outside the project directory by default:
  `~/.config/connectvpn/authopenvpn.auth`
- The credentials file is written with permission mode `600`.
- The config JSON stores metadata and file paths only, not passwords.
- Personal `.ovpn`, `.auth`, and `auth*.txt` files are ignored by `.gitignore`.

Before publishing or opening a pull request, run:

```bash
python -m unittest discover
git status --ignored --short
```

Check that personal files such as `authopenVPN.txt`, `*.ovpn`, and local logs
are ignored or absent from the commit.

## Reporting Security Issues

Please do not open public issues containing credentials, server-specific secrets,
or private logs. Open a private advisory or contact the maintainer directly.
