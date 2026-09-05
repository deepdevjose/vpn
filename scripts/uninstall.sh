#!/usr/bin/env bash
set -euo pipefail

CONNECTVPN_INSTALL_DIR="${CONNECTVPN_INSTALL_DIR:-${HOME}/.local/share/connectvpn-workbench}"
CONNECTVPN_BIN_DIR="${CONNECTVPN_BIN_DIR:-${HOME}/.local/bin}"

echo "This removes the installed app, but keeps user config and credentials."
echo "Config remains at: ${HOME}/.config/connectvpn"
echo "State remains at:  ${HOME}/.local/state/connectvpn"

rm -f "${CONNECTVPN_BIN_DIR}/connectvpn"

if [[ -d "${CONNECTVPN_INSTALL_DIR}" ]]; then
  find "${CONNECTVPN_INSTALL_DIR}" -mindepth 1 -delete
  rmdir "${CONNECTVPN_INSTALL_DIR}" 2>/dev/null || true
fi

echo "connectvpn uninstalled."
