#!/usr/bin/env bash
set -euo pipefail

APP_NAME="connectvpn"
DEFAULT_REPO="deepdevjose/vpn"
DEFAULT_REF="main"

CONNECTVPN_REPO="${CONNECTVPN_REPO:-${DEFAULT_REPO}}"
CONNECTVPN_REF="${CONNECTVPN_REF:-${DEFAULT_REF}}"
CONNECTVPN_INSTALL_DIR="${CONNECTVPN_INSTALL_DIR:-${HOME}/.local/share/connectvpn}"
CONNECTVPN_BIN_DIR="${CONNECTVPN_BIN_DIR:-${HOME}/.local/bin}"
CONNECTVPN_TARBALL_URL="${CONNECTVPN_TARBALL_URL:-}"
CONNECTVPN_SOURCE_DIR="${CONNECTVPN_SOURCE_DIR:-}"

INSTALL_DEPS=0
AUTO_INSTALL_PYTHON=1

usage() {
  cat <<EOF
connectvpn installer

Usage:
  bash scripts/install.sh [options]

Options:
  --no-install-python Do not auto-install Python when it is missing or too old.
  --install-deps      Install missing system dependencies with the detected package manager.
  --repo OWNER/REPO   GitHub repository to install from. Default: ${DEFAULT_REPO}
  --ref REF           Git branch, tag, or commit to install. Default: ${DEFAULT_REF}
  --source DIR        Install from a local source tree instead of GitHub.
  --prefix DIR        Install source files into DIR. Default: ~/.local/share/connectvpn
  --bin-dir DIR       Install the connectvpn command into DIR. Default: ~/.local/bin
  -h, --help          Show this help.

Environment:
  CONNECTVPN_REPO
  CONNECTVPN_REF
  CONNECTVPN_INSTALL_DIR
  CONNECTVPN_BIN_DIR
  CONNECTVPN_TARBALL_URL
  CONNECTVPN_SOURCE_DIR
EOF
}

say() {
  printf '%s\n' "$*"
}

warn() {
  printf 'Warning: %s\n' "$*" >&2
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-deps)
      INSTALL_DEPS=1
      shift
      ;;
    --no-install-python)
      AUTO_INSTALL_PYTHON=0
      shift
      ;;
    --repo)
      [[ $# -ge 2 ]] || die "--repo requires OWNER/REPO"
      CONNECTVPN_REPO="$2"
      shift 2
      ;;
    --ref)
      [[ $# -ge 2 ]] || die "--ref requires a branch, tag, or commit"
      CONNECTVPN_REF="$2"
      shift 2
      ;;
    --source)
      [[ $# -ge 2 ]] || die "--source requires a directory"
      CONNECTVPN_SOURCE_DIR="$2"
      shift 2
      ;;
    --prefix)
      [[ $# -ge 2 ]] || die "--prefix requires a directory"
      CONNECTVPN_INSTALL_DIR="$2"
      shift 2
      ;;
    --bin-dir)
      [[ $# -ge 2 ]] || die "--bin-dir requires a directory"
      CONNECTVPN_BIN_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

have() {
  command -v "$1" >/dev/null 2>&1
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

dependency_command_hint() {
  case "$(detect_package_manager)" in
    apt) echo "sudo apt-get update && sudo apt-get install -y python3 openvpn curl tar" ;;
    dnf) echo "sudo dnf install -y python3 openvpn curl tar" ;;
    yum) echo "sudo yum install -y python3 openvpn curl tar" ;;
    pacman) echo "sudo pacman -Sy --needed python openvpn curl tar" ;;
    zypper) echo "sudo zypper install -y python3 openvpn curl tar" ;;
    apk) echo "sudo apk add python3 openvpn curl tar" ;;
    xbps) echo "sudo xbps-install -Sy python3 openvpn curl tar" ;;
    *) echo "Install Python 3.10+, OpenVPN, curl or wget, and tar with your distro package manager." ;;
  esac
}

run_as_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
    return
  fi

  have sudo || die "sudo is required to install system packages. Install Python 3.10+ manually or rerun on a system with sudo."
  sudo "$@"
}

install_python_dependency() {
  local manager
  manager="$(detect_package_manager)"

  case "${manager}" in
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
      die "Could not detect a supported package manager. $(dependency_command_hint)"
      ;;
  esac
}

install_dependencies() {
  local manager
  manager="$(detect_package_manager)"

  case "${manager}" in
    apt)
      run_as_root apt-get update
      run_as_root apt-get install -y python3 openvpn curl tar
      ;;
    dnf)
      run_as_root dnf install -y python3 openvpn curl tar
      ;;
    yum)
      run_as_root yum install -y python3 openvpn curl tar
      ;;
    pacman)
      run_as_root pacman -Sy --needed python openvpn curl tar
      ;;
    zypper)
      run_as_root zypper install -y python3 openvpn curl tar
      ;;
    apk)
      run_as_root apk add python3 openvpn curl tar
      ;;
    xbps)
      run_as_root xbps-install -Sy python3 openvpn curl tar
      ;;
    *)
      die "Could not detect a supported package manager. $(dependency_command_hint)"
      ;;
  esac
}

python_is_supported() {
  have python3 && python3 - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

python_has_tkinter() {
  have python3 && python3 - <<'PY' >/dev/null 2>&1
import tkinter  # noqa: F401
PY
}

file_picker_available() {
  have zenity || have kdialog || have yad || python_has_tkinter
}

download_file() {
  local url="$1"
  local output="$2"

  if have curl; then
    curl -fsSL "$url" -o "$output"
  elif have wget; then
    wget -qO "$output" "$url"
  else
    die "curl or wget is required. $(dependency_command_hint)"
  fi
}

copy_source_tree() {
  local source_dir="$1"
  local install_dir="$2"

  mkdir -p "${install_dir}"
  tar \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.ovpn' \
    --exclude='*.auth' \
    --exclude='auth*.txt' \
    --exclude='*.log' \
    --exclude='*.pid' \
    -C "${source_dir}" \
    -cf - . | tar -C "${install_dir}" -xf -
}

missing=()

if ! python_is_supported; then
  if [[ "${AUTO_INSTALL_PYTHON}" -eq 1 || "${INSTALL_DEPS}" -eq 1 ]]; then
    say "Python 3.10+ was not found. Attempting to install or upgrade Python automatically..."
    install_python_dependency
  fi
fi

python_is_supported || missing+=("Python 3.10+")
have openvpn || missing+=("openvpn")
have tar || missing+=("tar")

if [[ ${#missing[@]} -gt 0 ]]; then
  if [[ "${INSTALL_DEPS}" -eq 1 ]]; then
    say "Installing missing dependencies: ${missing[*]}"
    install_dependencies
  else
    warn "Missing dependencies: ${missing[*]}"
    warn "Install them manually:"
    warn "  $(dependency_command_hint)"
    warn "Or rerun this installer with --install-deps."
  fi
fi

python_is_supported || die "Python 3.10 or newer is required."
have tar || die "tar is required."

TMP_DIR="$(mktemp -d)"
cleanup() {
  if [[ -n "${TMP_DIR:-}" && -d "${TMP_DIR}" && "${TMP_DIR}" == /tmp/* ]]; then
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup EXIT

if [[ -n "${CONNECTVPN_SOURCE_DIR}" ]]; then
  SOURCE_DIR="$(cd "${CONNECTVPN_SOURCE_DIR}" && pwd)"
  say "Installing connectvpn from local source: ${SOURCE_DIR}"
else
  if [[ -z "${CONNECTVPN_TARBALL_URL}" ]]; then
    CONNECTVPN_TARBALL_URL="https://codeload.github.com/${CONNECTVPN_REPO}/tar.gz/${CONNECTVPN_REF}"
  fi

  ARCHIVE="${TMP_DIR}/connectvpn.tar.gz"
  say "Downloading connectvpn from ${CONNECTVPN_REPO}@${CONNECTVPN_REF}..."
  download_file "${CONNECTVPN_TARBALL_URL}" "${ARCHIVE}"

  tar -xzf "${ARCHIVE}" -C "${TMP_DIR}"
  SOURCE_DIR="$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
fi

[[ -n "${SOURCE_DIR}" && -f "${SOURCE_DIR}/connectvpn" && -d "${SOURCE_DIR}/src/connectvpn" ]] || die "Source tree does not look like connectvpn."

mkdir -p "${CONNECTVPN_INSTALL_DIR}" "${CONNECTVPN_BIN_DIR}"
copy_source_tree "${SOURCE_DIR}" "${CONNECTVPN_INSTALL_DIR}"
chmod +x "${CONNECTVPN_INSTALL_DIR}/connectvpn"
ln -sfn "${CONNECTVPN_INSTALL_DIR}/connectvpn" "${CONNECTVPN_BIN_DIR}/connectvpn"

python3 -m py_compile "${CONNECTVPN_INSTALL_DIR}/connectvpn" "${CONNECTVPN_INSTALL_DIR}/src/connectvpn/app.py"

say "connectvpn installed at ${CONNECTVPN_BIN_DIR}/connectvpn"
if ! have openvpn; then
  warn "openvpn is still missing. connectvpn can open, but it cannot connect until OpenVPN is installed."
fi

if ! file_picker_available; then
  warn "No graphical file picker helper was detected."
  warn "The TUI will still work and fall back to a path prompt."
  warn "For file picker support, install zenity, kdialog, yad, or Python tkinter."
fi

case ":${PATH}:" in
  *":${CONNECTVPN_BIN_DIR}:"*) ;;
  *)
    warn "${CONNECTVPN_BIN_DIR} is not in PATH."
    warn "Add this to your shell profile:"
    warn "  export PATH=\"${CONNECTVPN_BIN_DIR}:\$PATH\""
    ;;
esac

say "Run: connectvpn"
