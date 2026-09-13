#!/usr/bin/env python3
"""
connectvpn: small OpenVPN profile manager with a terminal UI.

It stores profile metadata in JSON, keeps auth files separate with mode 600,
and starts OpenVPN as a daemon through sudo.
"""

from __future__ import annotations

import argparse
import curses
import getpass
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


APP_NAME = "connectvpn"
VERSION = "0.4.0"

CONFIG_HOME = Path(os.environ.get("CONNECTVPN_HOME", Path.home() / ".config" / APP_NAME))
STATE_HOME = Path(os.environ.get("CONNECTVPN_STATE", Path.home() / ".local" / "state" / APP_NAME))
CONFIG_PATH = CONFIG_HOME / "config.json"
PROFILES_DIR = CONFIG_HOME / "profiles"
CREDENTIALS_DIR = CONFIG_HOME / "credentials"
GLOBAL_AUTH_PATH = CONFIG_HOME / "authopenvpn.auth"
LOGS_DIR = STATE_HOME / "logs"
STATE_PATH = STATE_HOME / "state.json"
PID_PATH = STATE_HOME / "openvpn.pid"
DEFAULT_INSTALL_DIR = Path.home() / ".local" / "share" / APP_NAME
LEGACY_INSTALL_DIR = Path.home() / ".local" / "share" / "connectvpn-workbench"
INSTALL_DIR = Path(os.environ.get("CONNECTVPN_INSTALL_DIR", DEFAULT_INSTALL_DIR))
BIN_DIR = Path(os.environ.get("CONNECTVPN_BIN_DIR", Path.home() / ".local" / "bin"))
BIN_PATH = BIN_DIR / APP_NAME
LEGACY_UPDATE_RESOLV_CONF = Path("/etc/openvpn/update-resolv-conf")
DNS_UPDOWN_HELPER_CANDIDATES = (
    Path("/usr/libexec/openvpn/dns-updown"),
    Path("/usr/lib/openvpn/dns-updown"),
)

LOGO = [
    "  ____ ___  _   _ _   _ _____ ____ _____     __     ______  _   _ ",
    " / ___/ _ \\| \\ | | \\ | | ____/ ___|_   _|    \\ \\   / /  _ \\| \\ | |",
    "| |  | | | |  \\| |  \\| |  _|| |     | |       \\ \\ / /| |_) |  \\| |",
    "| |__| |_| | |\\  | |\\  | |__| |___  | |        \\ V / |  __/| |\\  |",
    " \\____\\___/|_| \\_|_| \\_|_____\\____| |_|         \\_/  |_|   |_| \\_|",
]


class ConnectVPNError(Exception):
    """Expected user-facing error."""


class FilePickerCanceled(Exception):
    """Raised when the user cancels a graphical file picker."""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def expand_path(value: str | Path) -> Path:
    return Path(str(value)).expanduser().resolve()


def chmod_quiet(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def ensure_dirs() -> None:
    for path in (CONFIG_HOME, PROFILES_DIR, CREDENTIALS_DIR, STATE_HOME, LOGS_DIR):
        path.mkdir(parents=True, exist_ok=True)
        chmod_quiet(path, 0o700)


def default_config() -> dict[str, Any]:
    return {
        "version": 2,
        "created_at": now_iso(),
        "credentials": {
            "mode": "global",
            "auth_path": str(GLOBAL_AUTH_PATH),
            "updated_at": None,
        },
        "services": [
            {
                "id": "default",
                "name": "Default service",
                "auth_path": str(GLOBAL_AUTH_PATH),
                "updated_at": None,
            }
        ],
        "servers": [],
    }


def normalize_config(cfg: dict[str, Any]) -> bool:
    changed = False
    if not isinstance(cfg.get("credentials"), dict):
        cfg["credentials"] = {}
        changed = True

    credentials = cfg["credentials"]
    defaults = {
        "mode": "global",
        "auth_path": str(GLOBAL_AUTH_PATH),
        "updated_at": None,
    }
    for key, value in defaults.items():
        if key not in credentials:
            credentials[key] = value
            changed = True

    if credentials.get("mode") != "global":
        credentials["mode"] = "global"
        changed = True

    services = cfg.get("services")
    if not isinstance(services, list):
        cfg["services"] = [
            {
                "id": "default",
                "name": "Default service",
                "auth_path": str(credentials.get("auth_path") or GLOBAL_AUTH_PATH),
                "updated_at": credentials.get("updated_at"),
            }
        ]
        services = cfg["services"]
        changed = True

    if not services:
        services.append(
            {
                "id": "default",
                "name": "Default service",
                "auth_path": str(GLOBAL_AUTH_PATH),
                "updated_at": None,
            }
        )
        changed = True

    service_ids = {service.get("id") for service in services if isinstance(service, dict)}
    if "default" not in service_ids:
        services.insert(
            0,
            {
                "id": "default",
                "name": "Default service",
                "auth_path": str(credentials.get("auth_path") or GLOBAL_AUTH_PATH),
                "updated_at": credentials.get("updated_at"),
            },
        )
        changed = True

    for server in cfg.get("servers", []):
        if not server.get("service_id"):
            server["service_id"] = "default"
            changed = True

    return changed


def credential_auth_path(cfg: dict[str, Any] | None = None) -> Path:
    if cfg is None:
        cfg = load_config()
    raw_path = cfg.get("credentials", {}).get("auth_path") or str(GLOBAL_AUTH_PATH)
    return expand_path(raw_path)


def service_by_id(cfg: dict[str, Any], service_id: str) -> dict[str, Any] | None:
    for service in cfg.get("services", []):
        if service.get("id") == service_id:
            return service
    return None


def service_auth_path(cfg: dict[str, Any], service_id: str = "default") -> Path:
    service = service_by_id(cfg, service_id) or service_by_id(cfg, "default")
    if service:
        return expand_path(service.get("auth_path") or credential_auth_path(cfg))
    return credential_auth_path(cfg)


def service_credentials_ready(cfg: dict[str, Any], service_id: str = "default") -> bool:
    return auth_file_has_minimum_shape(service_auth_path(cfg, service_id))


def make_service_id(name: str, cfg: dict[str, Any]) -> str:
    existing = {service.get("id") for service in cfg.get("services", [])}
    base = slugify(name)
    candidate = base
    while candidate in existing:
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
    return candidate


def add_service(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    service_name = name.strip()
    if not service_name:
        raise ConnectVPNError("Service name cannot be empty.")
    service_id = make_service_id(service_name, cfg)
    service = {
        "id": service_id,
        "name": service_name,
        "auth_path": str(CREDENTIALS_DIR / f"{service_id}.auth"),
        "updated_at": None,
    }
    cfg.setdefault("services", []).append(service)
    return service


def global_credentials_ready(cfg: dict[str, Any] | None = None) -> bool:
    return auth_file_has_minimum_shape(credential_auth_path(cfg))


def load_config() -> dict[str, Any]:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        cfg = default_config()
        save_config(cfg)
        return cfg

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConnectVPNError(f"Invalid config JSON: {CONFIG_PATH} ({exc})") from exc

    if not isinstance(cfg, dict):
        raise ConnectVPNError(f"Invalid config JSON: {CONFIG_PATH}")
    cfg.setdefault("version", 1)
    cfg.setdefault("created_at", now_iso())
    cfg.setdefault("servers", [])
    if not isinstance(cfg["servers"], list):
        raise ConnectVPNError("The 'servers' field must be a list.")

    changed = normalize_config(cfg)
    if migrate_to_global_credentials(cfg) or changed:
        save_config(cfg)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=True)
        fh.write("\n")
    chmod_quiet(tmp_path, 0o600)
    tmp_path.replace(CONFIG_PATH)
    chmod_quiet(CONFIG_PATH, 0o600)


def load_state() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    try:
        with STATE_PATH.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    return state if isinstance(state, dict) else None


def save_state(state: dict[str, Any]) -> None:
    ensure_dirs()
    tmp_path = STATE_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    chmod_quiet(tmp_path, 0o600)
    tmp_path.replace(STATE_PATH)
    chmod_quiet(STATE_PATH, 0o600)


def clear_state() -> None:
    for path in (STATE_PATH, PID_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def prepare_log_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    chmod_quiet(path.parent, 0o700)
    if not path_is_relative_to(path, LOGS_DIR):
        raise ConnectVPNError(f"Refusing to write a log outside {LOGS_DIR}")
    with path.open("a", encoding="utf-8"):
        pass
    chmod_quiet(path, 0o600)


def make_log_readable(path: Path) -> None:
    if not path_is_relative_to(path, LOGS_DIR):
        return
    uid_gid = f"{os.getuid()}:{os.getgid()}"
    commands = (
        ["sudo", "-n", "chown", uid_gid, str(path)],
        ["sudo", "-n", "chmod", "600", str(path)],
    )
    for cmd in commands:
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def is_safe_removal_path(path: Path, home: Path | None = None) -> bool:
    resolved = path.expanduser().resolve()
    resolved_home = (home or Path.home()).expanduser().resolve()
    protected = {
        resolved_home,
        resolved_home / ".local",
        resolved_home / ".local" / "share",
        resolved_home / ".config",
        resolved_home / ".local" / "state",
    }

    if resolved in protected:
        return False
    if not path_is_relative_to(resolved, resolved_home):
        return False
    return any(APP_NAME in part for part in resolved.parts)


def remove_tree_safely(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return False
    if not is_safe_removal_path(resolved):
        raise ConnectVPNError(f"Refusing to remove unsafe path: {resolved}")
    shutil.rmtree(resolved)
    return True


def uninstall_from_system(remove_user_data: bool = True) -> list[str]:
    if current_connection():
        raise ConnectVPNError("Disconnect the active VPN before uninstalling connectvpn.")

    removed: list[str] = []
    bin_path = BIN_PATH.expanduser()
    if bin_path.is_symlink() or bin_path.exists():
        bin_path.unlink()
        removed.append(f"Removed command: {bin_path}")

    install_dirs = [INSTALL_DIR.expanduser()]
    if "CONNECTVPN_INSTALL_DIR" not in os.environ and LEGACY_INSTALL_DIR.expanduser() not in install_dirs:
        install_dirs.append(LEGACY_INSTALL_DIR.expanduser())

    for install_dir in install_dirs:
        if remove_tree_safely(install_dir):
            removed.append(f"Removed install directory: {install_dir}")

    if remove_user_data:
        if remove_tree_safely(CONFIG_HOME):
            removed.append(f"Removed config directory: {CONFIG_HOME}")
        if remove_tree_safely(STATE_HOME):
            removed.append(f"Removed state directory: {STATE_HOME}")
    else:
        removed.append(f"Kept user config and credentials: {CONFIG_HOME}")
        removed.append(f"Kept runtime state and logs: {STATE_HOME}")

    if not removed:
        removed.append("Nothing was found to uninstall.")
    return removed


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "server"


def make_server_id(name: str, cfg: dict[str, Any]) -> str:
    existing = {server.get("id") for server in cfg.get("servers", [])}
    base = slugify(name)
    candidate = base
    while candidate in existing:
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
    return candidate


def server_by_id(cfg: dict[str, Any], server_id: str) -> dict[str, Any] | None:
    for server in cfg.get("servers", []):
        if server.get("id") == server_id:
            return server
    return None


def find_server(cfg: dict[str, Any], query: str) -> dict[str, Any] | None:
    needle = query.strip().lower()
    for server in cfg.get("servers", []):
        if server.get("id", "").lower() == needle:
            return server
    matches = [
        server
        for server in cfg.get("servers", [])
        if needle in server.get("name", "").lower()
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def parse_remote_lines(ovpn_text: str) -> list[str]:
    remotes: list[str] = []
    for line in ovpn_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("remote "):
            remotes.append(stripped)
    return remotes[:8]


def split_ovpn_directive(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", ";")):
        return []
    try:
        return shlex.split(stripped, comments=False, posix=True)
    except ValueError:
        return stripped.split()


def executable_file(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def openvpn_dns_updown_helper() -> Path | None:
    override = os.environ.get("CONNECTVPN_DNS_UPDOWN")
    if override:
        helper = expand_path(override)
        return helper if executable_file(helper) else None

    for helper in DNS_UPDOWN_HELPER_CANDIDATES:
        if executable_file(helper):
            return helper
    return None


def ensure_script_security_level(lines: list[str], minimum: int = 2) -> list[str]:
    updated: list[str] = []
    found = False

    for line in lines:
        parts = split_ovpn_directive(line)
        if parts and parts[0] == "script-security":
            found = True
            try:
                current_level = int(parts[1])
            except (IndexError, ValueError):
                current_level = 0
            updated.append(line if current_level >= minimum else f"script-security {minimum}")
        else:
            updated.append(line)

    if not found:
        updated.append(f"script-security {minimum}")
    return updated


def patch_missing_legacy_dns_hooks(
    ovpn_text: str,
    dns_helper: Path | None = None,
    path_exists: Callable[[Path], bool] | None = None,
) -> str:
    exists = path_exists or Path.exists
    helper = dns_helper if dns_helper is not None else openvpn_dns_updown_helper()
    lines = ovpn_text.splitlines()
    patched: list[str] = []
    removed_missing_legacy_hook = False
    has_dns_updown = False

    for line in lines:
        parts = split_ovpn_directive(line)
        if parts and parts[0] == "dns-updown":
            has_dns_updown = True

        if (
            parts
            and parts[0] in {"up", "down"}
            and len(parts) >= 2
            and Path(parts[1]) == LEGACY_UPDATE_RESOLV_CONF
            and helper
            and not exists(LEGACY_UPDATE_RESOLV_CONF)
        ):
            removed_missing_legacy_hook = True
            continue

        patched.append(line)

    if removed_missing_legacy_hook and helper and not has_dns_updown:
        patched = ensure_script_security_level(patched, 2)
        patched.append(f"dns-updown {helper.as_posix()}")

    return "\n".join(patched).rstrip() + "\n"


def patch_ovpn_for_local_system(
    ovpn_text: str,
    auth_path: Path,
    dns_helper: Path | None = None,
    path_exists: Callable[[Path], bool] | None = None,
) -> str:
    patched = patch_ovpn_auth(ovpn_text, auth_path)
    return patch_missing_legacy_dns_hooks(patched, dns_helper=dns_helper, path_exists=path_exists)


def auth_file_has_minimum_shape(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return len(lines) >= 2 and bool(lines[0].strip())


def patch_ovpn_auth(ovpn_text: str, auth_path: Path) -> str:
    auth_directive = f"auth-user-pass {auth_path.as_posix()}"
    lines = ovpn_text.splitlines()
    replaced = False
    patched: list[str] = []

    for line in lines:
        stripped = line.strip()
        parts = stripped.split()
        if stripped and not stripped.startswith(("#", ";")) and parts and parts[0] == "auth-user-pass":
            patched.append(auth_directive)
            replaced = True
        else:
            patched.append(line)

    if not replaced:
        patched.append(auth_directive)

    return "\n".join(patched).rstrip() + "\n"


def secure_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(text)
    chmod_quiet(tmp_path, 0o600)
    tmp_path.replace(path)
    chmod_quiet(path, 0o600)


def secure_copy_auth(source: Path, dest: Path) -> None:
    if not source.exists():
        raise ConnectVPNError(f"Auth file does not exist: {source}")
    if not auth_file_has_minimum_shape(source):
        raise ConnectVPNError("The auth file must contain username and password on the first two lines.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    chmod_quiet(dest, 0o600)


def repatch_managed_profiles(cfg: dict[str, Any]) -> None:
    for server in cfg.get("servers", []):
        raw_profile = server.get("ovpn_path")
        if not raw_profile:
            continue
        profile_path = Path(raw_profile).expanduser()
        if not profile_path.exists():
            continue
        current = read_text(profile_path)
        patched = patch_ovpn_for_local_system(current, service_auth_path(cfg, server.get("service_id", "default")))
        if patched != current:
            secure_write_text(profile_path, patched)


def migrate_to_global_credentials(cfg: dict[str, Any]) -> bool:
    changed = False
    auth_path = credential_auth_path(cfg)

    if not auth_file_has_minimum_shape(auth_path):
        for server in cfg.get("servers", []):
            legacy_auth = server.get("auth_path")
            if not legacy_auth:
                continue
            legacy_path = Path(legacy_auth).expanduser()
            if auth_file_has_minimum_shape(legacy_path):
                secure_copy_auth(legacy_path, auth_path)
                cfg["credentials"]["updated_at"] = now_iso()
                cfg["credentials"]["migrated_from"] = str(legacy_path)
                changed = True
                break

    if auth_file_has_minimum_shape(auth_path):
        for server in cfg.get("servers", []):
            if "auth_path" in server:
                server.pop("auth_path", None)
                changed = True
        repatch_managed_profiles(cfg)

    return changed


def set_global_credentials(username: str, password: str) -> Path:
    if not username.strip():
        raise ConnectVPNError("Username cannot be empty.")
    cfg = load_config()
    auth_path = credential_auth_path(cfg)
    secure_write_text(auth_path, f"{username}\n{password}\n")
    cfg["credentials"]["updated_at"] = now_iso()
    default_service = service_by_id(cfg, "default")
    if default_service:
        default_service["updated_at"] = cfg["credentials"]["updated_at"]
    cfg["credentials"].pop("migrated_from", None)
    repatch_managed_profiles(cfg)
    save_config(cfg)
    return auth_path


def set_global_credentials_from_file(source: Path) -> Path:
    cfg = load_config()
    auth_path = credential_auth_path(cfg)
    secure_copy_auth(expand_path(source), auth_path)
    cfg["credentials"]["updated_at"] = now_iso()
    default_service = service_by_id(cfg, "default")
    if default_service:
        default_service["updated_at"] = cfg["credentials"]["updated_at"]
    cfg["credentials"].pop("migrated_from", None)
    repatch_managed_profiles(cfg)
    save_config(cfg)
    return auth_path


def set_service_credentials(service_id: str, username: str, password: str) -> Path:
    if not username.strip():
        raise ConnectVPNError("Username cannot be empty.")
    cfg = load_config()
    service = service_by_id(cfg, service_id)
    if not service:
        raise ConnectVPNError(f"Service not found: {service_id}")
    auth_path = service_auth_path(cfg, service_id)
    secure_write_text(auth_path, f"{username}\n{password}\n")
    service["updated_at"] = now_iso()
    repatch_managed_profiles(cfg)
    save_config(cfg)
    return auth_path


def set_service_credentials_from_file(service_id: str, source: Path) -> Path:
    cfg = load_config()
    service = service_by_id(cfg, service_id)
    if not service:
        raise ConnectVPNError(f"Service not found: {service_id}")
    auth_path = service_auth_path(cfg, service_id)
    secure_copy_auth(expand_path(source), auth_path)
    service["updated_at"] = now_iso()
    repatch_managed_profiles(cfg)
    save_config(cfg)
    return auth_path


def require_global_credentials() -> Path:
    cfg = load_config()
    auth_path = credential_auth_path(cfg)
    if not auth_file_has_minimum_shape(auth_path):
        raise ConnectVPNError(
            "No credentials are configured. Open connectvpn and use 'Modify credentials'."
        )
    return auth_path


def refresh_managed_profile(profile_path: Path, cfg: dict[str, Any], service_id: str = "default") -> None:
    current = read_text(profile_path)
    patched = patch_ovpn_for_local_system(current, service_auth_path(cfg, service_id))
    if patched != current:
        secure_write_text(profile_path, patched)


def import_profile(
    ovpn_path: Path,
    name: str | None,
    service_id: str = "default",
) -> dict[str, Any]:
    cfg = load_config()
    if not service_by_id(cfg, service_id):
        raise ConnectVPNError(f"Service not found: {service_id}")
    source_ovpn = expand_path(ovpn_path)
    if not source_ovpn.exists():
        raise ConnectVPNError(f".ovpn file does not exist: {source_ovpn}")
    if source_ovpn.suffix.lower() != ".ovpn":
        raise ConnectVPNError("The profile must be a .ovpn file.")

    server_name = (name or source_ovpn.stem).strip() or source_ovpn.stem
    auth_path = service_auth_path(cfg, service_id)

    for server in cfg.get("servers", []):
        if server.get("source_path") == str(source_ovpn):
            server["name"] = server_name
            server["service_id"] = service_id
            profile_path = Path(server["ovpn_path"]).expanduser()
            ovpn_text = read_text(source_ovpn)
            patched = patch_ovpn_for_local_system(ovpn_text, auth_path)
            secure_write_text(profile_path, patched)
            server["remotes"] = parse_remote_lines(ovpn_text)
            server.pop("auth_path", None)
            save_config(cfg)
            return server

    server_id = make_server_id(server_name, cfg)
    profile_path = PROFILES_DIR / f"{server_id}.ovpn"

    ovpn_text = read_text(source_ovpn)
    patched = patch_ovpn_for_local_system(ovpn_text, auth_path)
    secure_write_text(profile_path, patched)

    server = {
        "id": server_id,
        "name": server_name,
        "service_id": service_id,
        "source_path": str(source_ovpn),
        "ovpn_path": str(profile_path),
        "remotes": parse_remote_lines(ovpn_text),
        "added_at": now_iso(),
        "last_connected_at": None,
    }
    cfg["servers"].append(server)
    save_config(cfg)
    return server


def import_current_directory(service_id: str = "default") -> list[dict[str, Any]]:
    current = Path.cwd()
    profiles = sorted(current.glob("*.ovpn"))
    imported: list[dict[str, Any]] = []
    cfg = load_config()
    already = {server.get("source_path") for server in cfg.get("servers", [])}

    for ovpn_path in profiles:
        resolved = str(ovpn_path.resolve())
        if resolved in already:
            continue
        imported.append(import_profile(ovpn_path, ovpn_path.stem, service_id))
    return imported


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def gui_session_available() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def normalized_dialog_dir(initial_dir: Path | None = None) -> Path:
    candidate = (initial_dir or Path.cwd()).expanduser()
    if candidate.is_file():
        candidate = candidate.parent
    if not candidate.exists():
        candidate = Path.home()
    return candidate.resolve()


def run_file_dialog_command(cmd: list[str], env: dict[str, str] | None = None) -> Path | None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode == 0:
        selected = result.stdout.strip().splitlines()
        if selected:
            return expand_path(selected[-1])
        return None

    if result.returncode in (1, 130, 255):
        raise FilePickerCanceled()

    return None


def select_ovpn_file_with_dialog(initial_dir: Path | None = None) -> Path | None:
    if not gui_session_available():
        return None

    start_dir = normalized_dialog_dir(initial_dir)
    start_with_sep = str(start_dir) + os.sep

    candidates: list[list[str]] = []
    if command_exists("zenity"):
        candidates.append(
            [
                "zenity",
                "--file-selection",
                "--title=Select OpenVPN profile",
                f"--filename={start_with_sep}",
                "--file-filter=OpenVPN profiles (*.ovpn) | *.ovpn",
                "--file-filter=All files | *",
            ]
        )
    if command_exists("kdialog"):
        candidates.append(
            [
                "kdialog",
                "--title",
                "Select OpenVPN profile",
                "--getopenfilename",
                str(start_dir),
                "OpenVPN profiles (*.ovpn)",
            ]
        )
    if command_exists("yad"):
        candidates.append(
            [
                "yad",
                "--file-selection",
                "--title=Select OpenVPN profile",
                f"--filename={start_with_sep}",
                "--file-filter=OpenVPN profiles (*.ovpn) | *.ovpn",
                "--file-filter=All files | *",
            ]
        )

    python_cmd = shutil.which("python3") or sys.executable
    if python_cmd:
        tkinter_code = (
            "try:\n"
            "    import os\n"
            "    import tkinter as tk\n"
            "    from tkinter import filedialog\n"
            "    root = tk.Tk()\n"
            "    root.withdraw()\n"
            "    root.attributes('-topmost', True)\n"
            "    path = filedialog.askopenfilename(\n"
            "        title='Select OpenVPN profile',\n"
            "        initialdir=os.environ.get('CONNECTVPN_FILE_DIALOG_DIR'),\n"
            "        filetypes=[('OpenVPN profiles', '*.ovpn'), ('All files', '*')],\n"
            "    )\n"
            "    root.destroy()\n"
            "except Exception:\n"
            "    raise SystemExit(2)\n"
            "if not path:\n"
            "    raise SystemExit(1)\n"
            "print(path)\n"
        )
        dialog_env = dict(os.environ)
        dialog_env["CONNECTVPN_FILE_DIALOG_DIR"] = str(start_dir)
        candidates.append([python_cmd, "-c", tkinter_code])
    else:
        dialog_env = None

    for cmd in candidates:
        env = dialog_env if len(cmd) >= 3 and cmd[1] == "-c" else None
        selected = run_file_dialog_command(cmd, env=env)
        if selected:
            return selected

    return None


def require_runtime_tools() -> None:
    missing = [name for name in ("sudo", "openvpn") if not command_exists(name)]
    if missing:
        joined = ", ".join(missing)
        raise ConnectVPNError(f"Required commands are missing: {joined}")


def read_pid_from_file(pid_path: Path = PID_PATH) -> int | None:
    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
        return int(raw)
    except (FileNotFoundError, ValueError, OSError):
        return None


def pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def current_connection() -> dict[str, Any] | None:
    state = load_state()
    pid = read_pid_from_file(Path(state.get("pid_file", PID_PATH)) if state else PID_PATH)

    if state and pid and pid_exists(pid):
        state["pid"] = pid
        return state

    if state or PID_PATH.exists():
        clear_state()
    return None


def sudo_validate() -> None:
    result = subprocess.run(["sudo", "-v"])
    if result.returncode != 0:
        raise ConnectVPNError("Could not validate sudo.")


def start_vpn(server: dict[str, Any]) -> dict[str, Any]:
    require_runtime_tools()
    cfg = load_config()
    service_id = server.get("service_id", "default")
    auth_path = service_auth_path(cfg, service_id)
    if not auth_file_has_minimum_shape(auth_path):
        service = service_by_id(cfg, service_id)
        service_name = service.get("name", service_id) if service else service_id
        raise ConnectVPNError(f"No credentials are configured for service '{service_name}'.")
    active = current_connection()
    if active:
        raise ConnectVPNError(f"A VPN is already active: {active.get('server_name', active.get('server_id'))}")

    profile_path = Path(server["ovpn_path"]).expanduser()
    if not profile_path.exists():
        raise ConnectVPNError(f"Managed profile does not exist: {profile_path}")

    refresh_managed_profile(profile_path, cfg, service_id)

    ensure_dirs()
    log_path = LOGS_DIR / f"{server['id']}-{int(time.time())}.log"
    prepare_log_file(log_path)
    try:
        PID_PATH.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    sudo_validate()
    cmd = [
        "sudo",
        "-n",
        "openvpn",
        "--config",
        str(profile_path),
        "--daemon",
        f"connectvpn-{server['id']}",
        "--writepid",
        str(PID_PATH),
        "--log",
        str(log_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    make_log_readable(log_path)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ConnectVPNError(f"OpenVPN could not start. {detail}".strip())

    pid: int | None = None
    for _ in range(30):
        pid = read_pid_from_file()
        if pid:
            break
        time.sleep(0.2)

    if not pid:
        make_log_readable(log_path)
        raise ConnectVPNError(f"OpenVPN started without writing a PID. Check the log: {log_path}")

    state = {
        "server_id": server["id"],
        "server_name": server["name"],
        "pid": pid,
        "pid_file": str(PID_PATH),
        "log_path": str(log_path),
        "started_at": now_iso(),
    }
    save_state(state)

    cfg = load_config()
    found = server_by_id(cfg, server["id"])
    if found:
        found["last_connected_at"] = now_iso()
        save_config(cfg)
    return state


def stop_vpn() -> str:
    require_runtime_tools()
    state = current_connection()
    if not state:
        clear_state()
        return "No VPN was active."

    pid = int(state["pid"])
    sudo_validate()
    result = subprocess.run(["sudo", "-n", "kill", "-TERM", str(pid)], capture_output=True, text=True)
    if result.returncode != 0 and "No such process" not in (result.stderr or ""):
        detail = (result.stderr or result.stdout or "").strip()
        raise ConnectVPNError(f"Could not stop OpenVPN. {detail}".strip())

    for _ in range(30):
        if not pid_exists(pid):
            break
        time.sleep(0.2)

    clear_state()
    return f"VPN disconnected: {state.get('server_name', state.get('server_id'))}"


def tail_log_with_sudo(path: Path, lines: int) -> list[str] | None:
    if not path_is_relative_to(path, LOGS_DIR):
        return None
    safe_lines = max(1, min(int(lines), 500))
    try:
        result = subprocess.run(
            ["sudo", "-n", "tail", "-n", str(safe_lines), str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.splitlines()


def unreadable_log_message(path: Path) -> list[str]:
    chown_command = f"sudo chown {os.getuid()}:{os.getgid()} {shlex.quote(str(path))}"
    return [
        f"Log is not readable by this user: {path}",
        "Older logs may be owned by root from a previous connectvpn version.",
        "New connections will create user-readable logs automatically.",
        f"To fix this old log manually: {chown_command}",
    ]


def tail_log(path: str | Path, lines: int = 20) -> list[str]:
    log_path = Path(path)
    if not log_path.exists():
        return ["No log is available yet."]
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-lines:]
    except PermissionError:
        sudo_lines = tail_log_with_sudo(log_path, lines)
        if sudo_lines is not None:
            return sudo_lines
        return unreadable_log_message(log_path)
    except OSError as exc:
        return [f"Could not read the log: {exc}"]


def wrap_text(text: str, width: int) -> list[str]:
    if width <= 10:
        return [text[:width]]
    wrapped: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        if not paragraph:
            wrapped.append("")
            continue
        chunks = re.findall(r".{1,%d}(?:\s+|$)" % max(10, width), paragraph)
        if chunks:
            wrapped.extend(chunk.strip() for chunk in chunks)
        else:
            wrapped.append(paragraph[:width])
    return wrapped


class TUI:
    def __init__(self, stdscr: Any) -> None:
        self.stdscr = stdscr
        self.selected = 0
        self.running = True
        self.colors_enabled = curses.has_colors()
        self.init_screen()

    def init_screen(self) -> None:
        curses.curs_set(0)
        self.stdscr.keypad(True)
        if self.colors_enabled:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
            curses.init_pair(5, curses.COLOR_BLUE, -1)
            curses.init_pair(6, curses.COLOR_WHITE, -1)

    def color(self, pair: int, extra: int = 0) -> int:
        if not self.colors_enabled:
            return extra
        return curses.color_pair(pair) | extra

    def dashboard(self) -> dict[str, Any]:
        cfg = load_config()
        state = current_connection()
        credentials_ready = global_credentials_ready(cfg)
        return {
            "state": state,
            "server_count": len(cfg.get("servers", [])),
            "credentials_ready": credentials_ready,
            "vpn_label": f"active: {state.get('server_name')}" if state else "disconnected",
            "vpn_color": 2 if state else 3,
            "credentials_label": "configured" if credentials_ready else "pending",
            "credentials_color": 2 if credentials_ready else 3,
        }

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        safe = text[: max(0, width - x - 1)]
        try:
            self.stdscr.addstr(y, x, safe, attr)
        except curses.error:
            pass

    def center(self, y: int, text: str, attr: int = 0) -> None:
        _, width = self.stdscr.getmaxyx()
        x = max(0, (width - len(text)) // 2)
        self.addstr(y, x, text, attr)

    def centered_rule(self, y: int, label: str, attr: int = 0) -> None:
        _, width = self.stdscr.getmaxyx()
        label_text = f" {label} "
        side = max(2, min(22, (width - len(label_text) - 4) // 2))
        self.center(y, f"{'-' * side}{label_text}{'-' * side}", attr)

    def key_codes(self, key_label: str) -> list[int]:
        if not key_label:
            return []
        lowered = key_label.lower()
        if lowered in ("enter", "return"):
            return []
        if len(key_label) == 1:
            char = key_label[0]
            return [ord(char.lower()), ord(char.upper())]
        return []

    def unpack_item(self, item: tuple[Any, ...]) -> tuple[str, Callable[[], Any], str]:
        label = item[0]
        action = item[1]
        key_label = item[2] if len(item) > 2 else ""
        return str(label), action, str(key_label)

    def draw_badges(self, y: int) -> None:
        data = self.dashboard()
        parts = [
            (f"[vpn {data['vpn_label']}]", data["vpn_color"]),
            (f"[creds {data['credentials_label']}]", data["credentials_color"]),
            (f"[servers {data['server_count']}]", 1),
        ]
        full = "  ".join(part for part, _ in parts)
        _, width = self.stdscr.getmaxyx()
        x = max(2, (width - len(full)) // 2)
        cursor = x
        for index, (part, pair) in enumerate(parts):
            self.addstr(y, cursor, part, self.color(pair, curses.A_BOLD))
            cursor += len(part)
            if index != len(parts) - 1:
                self.addstr(y, cursor, "  ", self.color(6))
                cursor += 2

    def draw_base(self, title: str, hero: bool = False) -> tuple[int, int, int]:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()

        if hero and height >= 34 and width >= 74:
            self.centered_rule(1, "Welcome to", self.color(5))
            for offset, line in enumerate(LOGO):
                self.center(4 + offset, line, self.color(1, curses.A_BOLD))
            self.center(10, "OPENVPN CONTROL WORKBENCH", self.color(1, curses.A_BOLD))
            self.centered_rule(11, f"Version {VERSION}", self.color(5))
            self.center(13, "Fast Proton/OpenVPN access with one saved credential set.", self.color(6))
            self.center(14, "Import profiles, pick a server, connect, disconnect, repeat.", self.color(6, curses.A_DIM))
            self.draw_badges(16)
            return height, width, 19

        if hero and height >= 28 and width >= 74:
            self.centered_rule(0, "Welcome to", self.color(5))
            for offset, line in enumerate(LOGO):
                self.center(2 + offset, line, self.color(1, curses.A_BOLD))
            self.center(8, f"OPENVPN CONTROL WORKBENCH  |  Version {VERSION}", self.color(1, curses.A_BOLD))
            self.center(10, "Fast Proton/OpenVPN access with one saved credential set.", self.color(6))
            self.draw_badges(12)
            return height, width, 14

        if hero and height >= 20 and width >= 58:
            self.centered_rule(1, "Welcome to", self.color(5))
            self.center(3, "CONNECTVPN", self.color(1, curses.A_BOLD))
            self.center(4, f"OPENVPN CONTROL WORKBENCH  |  Version {VERSION}", self.color(1, curses.A_BOLD))
            self.center(6, "Fast Proton/OpenVPN access with one saved credential set.", self.color(6))
            self.draw_badges(8)
            return height, width, 10

        brand = f"{APP_NAME}  {title}"
        self.centered_rule(1, brand, self.color(5))
        if width >= 44:
            self.center(3, "OPENVPN CONTROL WORKBENCH", self.color(1, curses.A_BOLD))
        else:
            self.center(3, "OPENVPN", self.color(1, curses.A_BOLD))
        self.draw_badges(5)
        self.addstr(6, 2, "-" * max(0, width - 5), self.color(5))
        return height, width, 8

    def footer(self) -> None:
        height, width = self.stdscr.getmaxyx()
        text = "Up/Down or j/k: move | Enter: select | q/Esc: back/quit"
        self.addstr(height - 2, max(2, (width - len(text)) // 2), text, self.color(6, curses.A_DIM))

    def menu(self, title: str, items: list[tuple[Any, ...]], hero: bool = False) -> None:
        index = 0
        while self.running:
            height, width, start_y = self.draw_base(title, hero=hero)
            if height < 16 or width < 48:
                self.addstr(8, 2, "Enlarge the terminal for a better TUI layout.", self.color(3))

            if hero:
                self.center(start_y, "Here are the fastest paths:", self.color(6, curses.A_DIM))
                start_y += 2

            menu_width = min(68, max(30, width - 8))
            menu_x = max(2, (width - menu_width) // 2)
            key_map: dict[int, Callable[[], Any]] = {}
            for raw_item in items:
                _, action, key_label = self.unpack_item(raw_item)
                for code in self.key_codes(key_label):
                    key_map[code] = action

            max_visible = max(1, height - start_y - 3)
            top = min(max(0, index - max_visible + 1), max(0, len(items) - max_visible))
            visible_items = items[top : top + max_visible]

            if top > 0:
                self.addstr(start_y - 1, menu_x + 10, "...", self.color(6, curses.A_DIM))

            for visible_index, raw_item in enumerate(visible_items):
                i = top + visible_index
                label, _, key_label = self.unpack_item(raw_item)

                y = start_y + visible_index
                selected = i == index
                row_attr = self.color(1, curses.A_BOLD) if selected else self.color(6)
                marker = ">" if selected else " "
                key_text = key_label or " "
                if key_label:
                    self.addstr(y, menu_x + 2, f"{key_text:<7}", self.color(1, curses.A_BOLD))
                self.addstr(y, menu_x, marker, row_attr)
                self.addstr(y, menu_x + 10, label, row_attr)

            if top + max_visible < len(items):
                self.addstr(start_y + max_visible, menu_x + 10, "...", self.color(6, curses.A_DIM))
            self.footer()
            self.stdscr.refresh()

            key = self.stdscr.getch()
            if key in (ord("q"), 27):
                return
            if key in key_map:
                outcome = key_map[key]()
                if outcome == "back":
                    return
                continue
            if key in (curses.KEY_UP, ord("k")):
                index = (index - 1) % len(items)
            elif key in (curses.KEY_DOWN, ord("j")):
                index = (index + 1) % len(items)
            elif key in (curses.KEY_ENTER, 10, 13):
                _, action, _ = self.unpack_item(items[index])
                outcome = action()
                if outcome == "back":
                    return
            elif height < 12:
                self.message("Small Terminal", ["Enlarge the window for a better TUI layout."])

    def prompt(self, title: str, label: str, default: str = "", secret: bool = False) -> str | None:
        value = list(default)
        curses.curs_set(1)
        while True:
            _, width, start_y = self.draw_base(title)
            panel_width = min(76, max(30, width - 8))
            form_x = max(4, (width - panel_width) // 2)
            self.addstr(start_y, form_x, label, self.color(6))
            visible = "*" * len(value) if secret else "".join(value)
            self.addstr(start_y + 2, form_x, "> " + visible, self.color(1, curses.A_BOLD))
            self.addstr(start_y + 4, form_x, "Enter confirms | Esc cancels", self.color(6, curses.A_DIM))
            self.stdscr.move(start_y + 2, min(width - 2, form_x + 2 + len(visible)))
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key in (27,):
                curses.curs_set(0)
                return None
            if key in (curses.KEY_ENTER, 10, 13):
                curses.curs_set(0)
                return "".join(value)
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if value:
                    value.pop()
                continue
            if key == curses.KEY_DC:
                value.clear()
                continue
            if 32 <= key <= 126:
                value.append(chr(key))

    def confirm(self, title: str, question: str) -> bool:
        while True:
            _, width, start_y = self.draw_base(title)
            panel_width = min(76, max(30, width - 8))
            form_x = max(4, (width - panel_width) // 2)
            self.addstr(start_y, form_x, question, self.color(6))
            self.addstr(start_y + 2, form_x, "y = yes | n = no", self.color(1, curses.A_BOLD))
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key in (ord("y"), ord("s"), ord("S")):
                return True
            if key in (ord("n"), ord("N"), 27):
                return False

    def message(self, title: str, lines: list[str], wait: bool = True) -> None:
        _, width, start_y = self.draw_base(title)
        panel_width = min(76, max(30, width - 8))
        form_x = max(4, (width - panel_width) // 2)
        y = start_y
        for line in lines:
            for wrapped in wrap_text(line, panel_width):
                self.addstr(y, form_x, wrapped, self.color(6))
                y += 1
        if wait:
            self.addstr(y + 1, form_x, "Press any key to go back.", self.color(1))
            self.stdscr.refresh()
            self.stdscr.getch()

    def run_external(self, label: str, fn: Callable[[], Any]) -> Any:
        curses.def_prog_mode()
        curses.endwin()
        print(f"\n{APP_NAME}: {label}")
        print("If sudo prompts, enter your system password.\n")
        try:
            result = fn()
            print("\nDone.")
            if isinstance(result, dict):
                if result.get("server_name"):
                    print(f"Server: {result['server_name']}")
                if result.get("pid"):
                    print(f"PID: {result['pid']}")
                if result.get("log_path"):
                    print(f"Log: {result['log_path']}")
            elif result is not None:
                if isinstance(result, list):
                    for line in result:
                        print(line)
                else:
                    print(result)
        except ConnectVPNError as exc:
            print(f"\nError: {exc}")
            result = None
        except KeyboardInterrupt:
            print("\nCanceled.")
            result = None
        input("\nPress Enter to return to the TUI...")
        curses.reset_prog_mode()
        curses.curs_set(0)
        self.stdscr.clear()
        return result

    def pick_ovpn_file(self) -> Path | None:
        if gui_session_available():
            curses.def_prog_mode()
            curses.endwin()
            print(f"\n{APP_NAME}: opening file picker")
            print("Select a .ovpn file in the dialog window.\n")
            canceled = False
            selected: Path | None = None
            try:
                selected = select_ovpn_file_with_dialog(Path.cwd())
            except FilePickerCanceled:
                canceled = True
            finally:
                curses.reset_prog_mode()
                curses.curs_set(0)
                self.stdscr.clear()

            if selected:
                return selected
            if canceled:
                return None

        ovpn = self.prompt("Add Server", "Path to .ovpn file:")
        if not ovpn:
            return None
        return expand_path(ovpn)

    def configure_credentials_manual(self) -> bool:
        username = self.prompt("Credentials", "OpenVPN username:")
        if username is None:
            return False
        password = self.prompt("Credentials", "OpenVPN password:", secret=True)
        if password is None:
            return False

        try:
            auth_path = set_global_credentials(username, password)
            self.message("Credentials Saved", [f"Global auth file: {auth_path}"])
            return True
        except ConnectVPNError as exc:
            self.message("Could Not Save", [str(exc)])
            return False

    def configure_credentials_from_file(self) -> bool:
        default_auth = ""
        local_auth = Path.cwd() / "authopenVPN.txt"
        if local_auth.exists():
            default_auth = str(local_auth)
        auth = self.prompt("Credentials", "Path to auth-user-pass file:", default_auth)
        if not auth:
            return False

        try:
            auth_path = set_global_credentials_from_file(expand_path(auth))
            self.message("Credentials Saved", [f"Global auth file: {auth_path}"])
            return True
        except ConnectVPNError as exc:
            self.message("Could Not Save", [str(exc)])
            return False

    def configure_service_credentials_manual(self, service: dict[str, Any]) -> bool:
        username = self.prompt("Service Credentials", f"Username for {service['name']}:")
        if username is None:
            return False
        password = self.prompt("Service Credentials", f"Password for {service['name']}:", secret=True)
        if password is None:
            return False
        try:
            auth_path = set_service_credentials(service["id"], username, password)
            self.message("Credentials Saved", [f"Service: {service['name']}", f"Auth file: {auth_path}"])
            return True
        except ConnectVPNError as exc:
            self.message("Could Not Save", [str(exc)])
            return False

    def choose_service(self) -> dict[str, Any] | None:
        cfg = load_config()
        self.selected_service_id = None
        items: list[tuple[Any, ...]] = []
        for service in cfg.get("services", []):
            status = "configured" if service_credentials_ready(cfg, service["id"]) else "credentials pending"
            items.append((f"{service['name']} [{status}]", lambda s=service: setattr(self, "selected_service_id", s["id"]), ""))
        items.append(("Back", lambda: "back", "b"))
        self.menu("Choose Service", items)
        if self.selected_service_id is None:
            return None
        return service_by_id(load_config(), self.selected_service_id)

    def add_service(self) -> None:
        name = self.prompt("Add Service", "Service name:")
        if name is None:
            return
        cfg = load_config()
        try:
            service = add_service(cfg, name)
            save_config(cfg)
            self.configure_service_credentials_manual(service)
        except ConnectVPNError as exc:
            self.message("Could Not Add", [str(exc)])

    def configure_selected_service(self) -> None:
        service = self.choose_service()
        if service:
            self.configure_service_credentials_manual(service)

    def credentials_menu(self) -> None:
        items: list[tuple[Any, ...]] = [
            ("Configure credentials for a service", self.configure_selected_service, "s"),
            ("Enter username/password", self.configure_credentials_manual, "u"),
            ("Load from auth-user-pass file", self.configure_credentials_from_file, "f"),
            ("Back", lambda: "back", "b"),
        ]
        self.menu("Modify Credentials", items)

    def ensure_credentials_interactive(self) -> bool:
        return self.ensure_service_credentials_interactive("default")

    def ensure_service_credentials_interactive(self, service_id: str) -> bool:
        cfg = load_config()
        if service_credentials_ready(cfg, service_id):
            return True
        service = service_by_id(cfg, service_id)
        service_name = service.get("name", service_id) if service else service_id
        self.message(
            "First Run",
            [
                f"No credentials are configured for {service_name}.",
                "Save them once and every server from this service will use them automatically.",
            ],
        )
        return bool(service and self.configure_service_credentials_manual(service))

    def connect_random(self) -> None:
        cfg = load_config()
        servers = cfg.get("servers", [])
        if not servers:
            self.message("No Servers", ["Add or import a .ovpn file first."])
            return
        if current_connection() and not self.confirm("VPN Active", "A VPN is already active. Disconnect and switch?"):
            return
        if current_connection():
            self.run_external("disconnecting current VPN", stop_vpn)
        server = random.choice(servers)
        if not self.ensure_service_credentials_interactive(server.get("service_id", "default")):
            return
        self.run_external(f"connecting to {server['name']}", lambda: start_vpn(server))

    def connect_chosen(self) -> None:
        cfg = load_config()
        servers = list(cfg.get("servers", []))
        items: list[tuple[Any, ...]] = []

        for index, server in enumerate(servers):
            remote = ""
            if server.get("remotes"):
                remote = f"  ({server['remotes'][0]})"
            key = str(index + 1) if index < 9 else ""
            items.append((f"{server['name']}{remote}", lambda s=server: self.connect_server(s), key))

        items.append(("+ Add a new .ovpn server", self.add_server, "a"))
        items.append(("Back", lambda: "back", "b"))
        self.menu("Choose Server", items)

    def connect_server(self, server: dict[str, Any]) -> None:
        if not self.ensure_service_credentials_interactive(server.get("service_id", "default")):
            return
        if current_connection() and not self.confirm("VPN Active", "A VPN is already active. Disconnect and switch?"):
            return
        if current_connection():
            self.run_external("disconnecting current VPN", stop_vpn)
        self.run_external(f"connecting to {server['name']}", lambda: start_vpn(server))

    def add_server(self) -> None:
        service = self.choose_service()
        if not service:
            return
        ovpn_path = self.pick_ovpn_file()
        if not ovpn_path:
            return
        default_name = ovpn_path.stem if ovpn_path.name else ""
        name = self.prompt("Add Server", "Server name:", default_name)
        if name is None:
            return

        try:
            server = import_profile(ovpn_path, name, service["id"])
            self.message(
                "Server Added",
                [
                    f"Added: {server['name']}",
                    f"ID: {server['id']}",
                    f"Managed profile: {server['ovpn_path']}",
                ],
            )
        except ConnectVPNError as exc:
            self.message("Could Not Add", [str(exc)])

    def import_cwd(self) -> None:
        profiles = sorted(Path.cwd().glob("*.ovpn"))
        if not profiles:
            self.message("No Profiles", ["No .ovpn files were found in this directory."])
            return
        service = self.choose_service()
        if not service:
            return

        try:
            imported = import_current_directory(service["id"])
            if imported:
                self.message("Import Complete", [f"Imported: {len(imported)}"] + [s["name"] for s in imported])
            else:
                self.message("Nothing New", ["All .ovpn files in this folder were already imported."])
        except ConnectVPNError as exc:
            self.message("Could Not Import", [str(exc)])

    def disconnect(self) -> None:
        self.run_external("disconnecting VPN", stop_vpn)

    def uninstall(self) -> None:
        confirmed = self.confirm(
            "Uninstall connectvpn",
            "Remove the app, credentials, imported servers, logs, and state?",
        )
        if not confirmed:
            return

        result = self.run_external("uninstalling connectvpn", uninstall_from_system)
        if result is not None:
            self.running = False

    def show_status(self) -> None:
        state = current_connection()
        if not state:
            self.message("Status", ["VPN disconnected."])
            return
        lines = [
            f"Server: {state.get('server_name')}",
            f"PID: {state.get('pid')}",
            f"Started: {state.get('started_at')}",
            f"Log: {state.get('log_path')}",
            "",
            "Latest lines:",
        ]
        lines.extend(tail_log(state.get("log_path", ""), 14))
        self.message("Status", lines)

    def run(self) -> None:
        while self.running:
            items = [
                ("Choose a server and connect", self.connect_chosen, "Enter"),
                ("Connect to a random server", self.connect_random, "r"),
                ("Add a new VPN service", self.add_service, "v"),
                ("Add a new .ovpn server", self.add_server, "a"),
                ("Import .ovpn files from this folder", self.import_cwd, "i"),
                ("Modify global credentials", self.credentials_menu, "m"),
                ("View status and latest log", self.show_status, "s"),
                ("Disconnect active VPN", self.disconnect, "d"),
                ("Uninstall / remove from system", self.uninstall, "u"),
                ("Quit", self.stop, "q"),
            ]
            self.menu("OpenVPN TUI", items, hero=True)
            self.running = False

    def stop(self) -> None:
        self.running = False


def run_tui() -> None:
    try:
        curses.wrapper(lambda stdscr: TUI(stdscr).run())
    except curses.error as exc:
        raise ConnectVPNError(f"Could not open the TUI in this terminal: {exc}") from exc


def print_servers(cfg: dict[str, Any]) -> None:
    servers = cfg.get("servers", [])
    if not servers:
        print("No imported servers.")
        return
    for server in servers:
        remote = server.get("remotes", [""])[0] if server.get("remotes") else ""
        print(f"{server['id']}\t{server['name']}\t{remote}")


def print_status() -> None:
    cfg = load_config()
    auth_state = "configured" if global_credentials_ready(cfg) else "pending"
    print(f"Credentials: {auth_state}")
    state = current_connection()
    if not state:
        print("VPN disconnected.")
        return
    print(f"VPN active: {state.get('server_name')} (PID {state.get('pid')})")
    print(f"Started: {state.get('started_at')}")
    print(f"Log: {state.get('log_path')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TUI/CLI for managing OpenVPN connections.")
    parser.add_argument("--version", action="store_true", help="Show the version.")
    parser.add_argument("--list", action="store_true", help="List imported servers.")
    parser.add_argument("--status", action="store_true", help="Show the current status.")
    parser.add_argument("--disconnect", action="store_true", help="Disconnect the active VPN.")
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove connectvpn, including config, credentials, imported profiles, logs, and state.",
    )
    parser.add_argument(
        "--keep-user-data",
        action="store_true",
        help="With --uninstall, keep config, credentials, imported profiles, logs, and state.",
    )
    parser.add_argument("--purge", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--random", action="store_true", help="Connect to a random server.")
    parser.add_argument("--connect", metavar="ID_OR_NAME", help="Connect to a server by ID or name.")
    parser.add_argument("--import", dest="import_ovpn", metavar="FILE.ovpn", help="Import a .ovpn profile.")
    parser.add_argument("--import-current", action="store_true", help="Import .ovpn files from the current directory.")
    parser.add_argument("--set-credentials", action="store_true", help="Save or modify global credentials.")
    parser.add_argument("--name", help="Name for the imported profile.")
    parser.add_argument("--auth-file", help="auth-user-pass file for global credentials.")
    parser.add_argument("--username", help="OpenVPN username for --set-credentials.")
    parser.add_argument("--config-path", action="store_true", help="Show the config JSON path.")
    return parser


def cli_set_credentials(args: argparse.Namespace) -> None:
    if args.auth_file:
        auth_path = set_global_credentials_from_file(expand_path(args.auth_file))
        print(f"Global credentials saved: {auth_path}")
        return

    username = args.username
    if not username:
        username = input("OpenVPN username: ")
    password = os.environ.get("CONNECTVPN_PASSWORD") or getpass.getpass("OpenVPN password: ")
    auth_path = set_global_credentials(username, password)
    print(f"Global credentials saved: {auth_path}")


def ensure_global_credentials_cli() -> None:
    if global_credentials_ready(load_config()):
        return
    print("No global credentials are configured.")
    username = input("OpenVPN username: ")
    password = os.environ.get("CONNECTVPN_PASSWORD") or getpass.getpass("OpenVPN password: ")
    set_global_credentials(username, password)


def ensure_service_credentials_cli(service_id: str) -> None:
    cfg = load_config()
    if service_credentials_ready(cfg, service_id):
        return
    service = service_by_id(cfg, service_id)
    if not service:
        raise ConnectVPNError(f"Service not found: {service_id}")
    print(f"No credentials are configured for {service['name']}.")
    username = input("OpenVPN username: ")
    password = os.environ.get("CONNECTVPN_PASSWORD") or getpass.getpass("OpenVPN password: ")
    set_service_credentials(service_id, username, password)


def cli_import(args: argparse.Namespace) -> None:
    if args.auth_file:
        set_global_credentials_from_file(expand_path(args.auth_file))
        print("Global credentials updated.")

    if args.import_current:
        imported = import_current_directory()
        print(f"Imported: {len(imported)}")
        for server in imported:
            print(f"- {server['name']} ({server['id']})")
        if not global_credentials_ready(load_config()):
            print("Credentials pending: run connectvpn --set-credentials or open connectvpn.")
        return

    server = import_profile(expand_path(args.import_ovpn), args.name)
    print(f"Imported: {server['name']} ({server['id']})")
    if not global_credentials_ready(load_config()):
        print("Credentials pending: run connectvpn --set-credentials or open connectvpn.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.version:
            print(f"{APP_NAME} {VERSION}")
            return 0
        if args.config_path:
            ensure_dirs()
            print(CONFIG_PATH)
            return 0
        if args.list:
            print_servers(load_config())
            return 0
        if args.status:
            print_status()
            return 0
        if args.disconnect:
            print(stop_vpn())
            return 0
        if args.purge and not args.uninstall:
            raise ConnectVPNError("--purge can only be used with --uninstall.")
        if args.keep_user_data and not args.uninstall:
            raise ConnectVPNError("--keep-user-data can only be used with --uninstall.")
        if args.purge and args.keep_user_data:
            raise ConnectVPNError("--purge and --keep-user-data cannot be used together.")
        if args.uninstall:
            for line in uninstall_from_system(remove_user_data=not args.keep_user_data):
                print(line)
            return 0
        if args.set_credentials:
            cli_set_credentials(args)
            return 0
        if args.import_ovpn or args.import_current:
            cli_import(args)
            return 0
        if args.random:
            cfg = load_config()
            servers = cfg.get("servers", [])
            if not servers:
                raise ConnectVPNError("No imported servers.")
            server = random.choice(servers)
            ensure_service_credentials_cli(server.get("service_id", "default"))
            state = start_vpn(server)
            print(f"VPN active: {state['server_name']} (PID {state['pid']})")
            return 0
        if args.connect:
            cfg = load_config()
            server = find_server(cfg, args.connect)
            if not server:
                raise ConnectVPNError(f"Server not found: {args.connect}")
            ensure_service_credentials_cli(server.get("service_id", "default"))
            state = start_vpn(server)
            print(f"VPN active: {state['server_name']} (PID {state['pid']})")
            return 0

        run_tui()
        return 0
    except ConnectVPNError as exc:
        print(f"connectvpn: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCanceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
