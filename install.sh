#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
TARGET="${BIN_DIR}/connectvpn"

have() {
  command -v "$1" >/dev/null 2>&1
}

run_as_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
    return
  fi

  if ! have sudo; then
    echo "sudo is required to install Python automatically." >&2
    exit 1
  fi

  sudo "$@"
}

detect_package_manager() {
  if have apt-get; then
    echo "apt"
  elif have dnf; then
    echo "dnf"
  elif have yum; then
    echo "yum"
  elif have pacman; then
    echo "pacman"
  elif have zypper; then
    echo "zypper"
  elif have apk; then
    echo "apk"
  elif have xbps-install; then
    echo "xbps"
  else
    echo "unknown"
  fi
}

install_python() {
  case "$(detect_package_manager)" in
    apt)
      run_as_root apt-get update
      run_as_root apt-get install -y python3
      ;;
    dnf)
      run_as_root dnf install -y python3
      ;;
    yum)
      run_as_root yum install -y python3
      ;;
    pacman)
      run_as_root pacman -Sy --needed python
      ;;
    zypper)
      run_as_root zypper install -y python3
      ;;
    apk)
      run_as_root apk add python3
      ;;
    xbps)
      run_as_root xbps-install -Sy python3
      ;;
    *)
      echo "Could not detect a supported package manager. Install Python 3.10+ manually." >&2
      exit 1
      ;;
  esac
}

python_is_supported() {
  have python3 && python3 - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

if ! python_is_supported; then
  echo "Python 3.10+ was not found. Attempting to install or upgrade Python automatically..."
  install_python
fi

python3 - <<'PY'
import sys

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required.")
PY

mkdir -p "${BIN_DIR}"
chmod +x "${ROOT_DIR}/connectvpn"
ln -sf "${ROOT_DIR}/connectvpn" "${TARGET}"

echo "connectvpn installed at ${TARGET}"
if ! command -v openvpn >/dev/null 2>&1; then
  echo "Warning: openvpn is not installed or is not in PATH."
fi

echo "If your shell cannot find it, add this to ~/.bashrc:"
echo 'export PATH="$HOME/.local/bin:$PATH"'
