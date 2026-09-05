# Contributing

Thanks for improving `connectvpn`.

Before opening a pull request:

```bash
python -m unittest discover
python -m py_compile connectvpn src/connectvpn/app.py
./scripts/check_no_sensitive_files.sh
```

Do not commit:

- `.ovpn` files downloaded from your VPN account
- `authopenVPN.txt`
- `*.auth`
- logs or PID files

Keep changes small and focused. If you change credential handling, add or update
tests that prove credentials are not written into the JSON config.
