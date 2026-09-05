#!/usr/bin/env bash
set -euo pipefail

APP_NAME="connectvpn"
CONNECTVPN_INSTALL_DIR="${CONNECTVPN_INSTALL_DIR:-${HOME}/.local/share/connectvpn}"
CONNECTVPN_BIN_DIR="${CONNECTVPN_BIN_DIR:-${HOME}/.local/bin}"
CONNECTVPN_CONFIG_DIR="${CONNECTVPN_CONFIG_DIR:-${HOME}/.config/connectvpn}"
CONNECTVPN_STATE_DIR="${CONNECTVPN_STATE_DIR:-${HOME}/.local/state/connectvpn}"
KEEP_USER_DATA=0

usage() {
  cat <<EOF
connectvpn uninstaller

Usage:
  bash scripts/uninstall.sh [options]

Options:
  --keep-user-data  Keep config, credentials, imported profiles, logs, and state.
  -h, --help        Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-user-data)
      KEEP_USER_DATA=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

safe_remove_dir() {
  local target="$1"
  [[ -d "${target}" ]] || return 0

  case "${target}" in
    "${HOME}"|"${HOME}/.local"|"${HOME}/.local/share"|"${HOME}/.local/state"|"${HOME}/.config")
      echo "Refusing to remove unsafe path: ${target}" >&2
      exit 1
      ;;
  esac

  case "${target}" in
    "${HOME}"/*"${APP_NAME}"*) ;;
    *)
      echo "Refusing to remove path outside connectvpn scope: ${target}" >&2
      exit 1
      ;;
  esac

  rm -rf "${target}"
  echo "Removed directory: ${target}"
}

rm -f "${CONNECTVPN_BIN_DIR}/connectvpn"
echo "Removed command: ${CONNECTVPN_BIN_DIR}/connectvpn"

safe_remove_dir "${CONNECTVPN_INSTALL_DIR}"

if [[ "${KEEP_USER_DATA}" -eq 1 ]]; then
  echo "Kept user config and credentials: ${CONNECTVPN_CONFIG_DIR}"
  echo "Kept runtime state and logs: ${CONNECTVPN_STATE_DIR}"
else
  safe_remove_dir "${CONNECTVPN_CONFIG_DIR}"
  safe_remove_dir "${CONNECTVPN_STATE_DIR}"
fi

echo "connectvpn uninstalled."
