#!/usr/bin/env bash
set -euo pipefail

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not inside a git repository; skipping tracked-file check."
  exit 0
fi

blocked_regex='(^|/)(auth.*\.txt|.*\.auth|.*\.ovpn|.*\.log|.*\.pid)$'
tracked_sensitive="$(git ls-files | grep -E "${blocked_regex}" || true)"

if [[ -n "${tracked_sensitive}" ]]; then
  echo "Refusing to continue: sensitive local files are tracked by git:"
  echo "${tracked_sensitive}"
  echo
  echo "Remove them from git history/index before publishing."
  exit 1
fi

echo "No tracked sensitive OpenVPN files found."
