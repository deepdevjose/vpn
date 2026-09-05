import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from connectvpn import app


class ConnectVPNTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config_home = self.root / "config"
        self.state_home = self.root / "state"

        app.CONFIG_HOME = self.config_home
        app.STATE_HOME = self.state_home
        app.CONFIG_PATH = self.config_home / "config.json"
        app.PROFILES_DIR = self.config_home / "profiles"
        app.CREDENTIALS_DIR = self.config_home / "credentials"
        app.GLOBAL_AUTH_PATH = self.config_home / "authopenvpn.auth"
        app.LOGS_DIR = self.state_home / "logs"
        app.STATE_PATH = self.state_home / "state.json"
        app.PID_PATH = self.state_home / "openvpn.pid"
        app.INSTALL_DIR = self.root / "home" / ".local" / "share" / "connectvpn"
        app.BIN_DIR = self.root / "home" / ".local" / "bin"
        app.BIN_PATH = app.BIN_DIR / app.APP_NAME

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_profile(self, name: str = "server.ovpn") -> Path:
        profile = self.root / name
        profile.write_text(
            "\n".join(
                [
                    "client",
                    "dev tun",
                    "proto udp",
                    "remote 10.0.0.1 1194",
                    "auth-user-pass",
                    "verb 3",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return profile

    def test_patch_ovpn_auth_replaces_unqualified_directive(self) -> None:
        patched = app.patch_ovpn_auth("client\nauth-user-pass\n", Path("/safe/auth.txt"))

        self.assertIn("auth-user-pass /safe/auth.txt", patched)
        self.assertEqual(patched.count("auth-user-pass"), 1)

    def test_patch_ovpn_auth_adds_missing_directive(self) -> None:
        patched = app.patch_ovpn_auth("client\nverb 3\n", Path("/safe/auth.txt"))

        self.assertTrue(patched.endswith("auth-user-pass /safe/auth.txt\n"))

    def test_patch_ovpn_for_local_system_replaces_missing_legacy_dns_hook(self) -> None:
        helper = self.root / "dns-updown"
        raw = "\n".join(
            [
                "client",
                "script-security 1",
                "up /etc/openvpn/update-resolv-conf",
                "down /etc/openvpn/update-resolv-conf",
                "auth-user-pass",
                "",
            ]
        )

        patched = app.patch_ovpn_for_local_system(
            raw,
            Path("/safe/auth.txt"),
            dns_helper=helper,
            path_exists=lambda _path: False,
        )

        self.assertNotIn("update-resolv-conf", patched)
        self.assertIn(f"dns-updown {helper}", patched)
        self.assertIn("script-security 2", patched)
        self.assertIn("auth-user-pass /safe/auth.txt", patched)

    def test_patch_ovpn_for_local_system_keeps_legacy_dns_hook_when_available(self) -> None:
        raw = "client\nup /etc/openvpn/update-resolv-conf\ndown /etc/openvpn/update-resolv-conf\n"

        patched = app.patch_ovpn_for_local_system(
            raw,
            Path("/safe/auth.txt"),
            dns_helper=self.root / "dns-updown",
            path_exists=lambda _path: True,
        )

        self.assertIn("up /etc/openvpn/update-resolv-conf", patched)
        self.assertIn("down /etc/openvpn/update-resolv-conf", patched)
        self.assertNotIn("dns-updown", patched)

    def test_import_profile_uses_global_credentials_path(self) -> None:
        app.set_global_credentials("example-user", "example-password")
        source = self.write_profile()

        server = app.import_profile(source, "Test Server")
        managed_profile = Path(server["ovpn_path"])
        config = json.loads(app.CONFIG_PATH.read_text(encoding="utf-8"))

        self.assertEqual(server["id"], "test-server")
        self.assertNotIn("auth_path", server)
        self.assertIn(str(app.GLOBAL_AUTH_PATH), managed_profile.read_text(encoding="utf-8"))
        self.assertNotIn("example-password", app.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(config["credentials"]["auth_path"], str(app.GLOBAL_AUTH_PATH))

    def test_import_profile_updates_existing_source_without_duplicate(self) -> None:
        app.set_global_credentials("example-user", "example-password")
        source = self.write_profile()

        first = app.import_profile(source, "First Name")
        second = app.import_profile(source, "Second Name")
        config = app.load_config()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(config["servers"]), 1)
        self.assertEqual(config["servers"][0]["name"], "Second Name")

    def test_global_credentials_are_mode_600(self) -> None:
        auth_path = app.set_global_credentials("example-user", "example-password")

        mode = stat.S_IMODE(auth_path.stat().st_mode)

        self.assertEqual(mode, 0o600)

    def test_parse_remote_lines_limits_output(self) -> None:
        text = "\n".join(f"remote 10.0.0.{i} 443" for i in range(20))

        remotes = app.parse_remote_lines(text)

        self.assertEqual(len(remotes), 8)
        self.assertEqual(remotes[0], "remote 10.0.0.0 443")

    def test_enter_is_not_registered_as_global_menu_hotkey(self) -> None:
        tui = app.TUI.__new__(app.TUI)

        self.assertEqual(tui.key_codes("Enter"), [])
        self.assertIn(ord("r"), tui.key_codes("r"))

    def test_normalized_dialog_dir_uses_parent_for_files(self) -> None:
        profile = self.write_profile()

        self.assertEqual(app.normalized_dialog_dir(profile), self.root)

    def test_run_file_dialog_command_returns_selected_path(self) -> None:
        profile = self.write_profile()
        selected = app.run_file_dialog_command([sys.executable, "-c", f"print({str(profile)!r})"])

        self.assertEqual(selected, profile.resolve())

    def test_select_ovpn_file_with_dialog_returns_none_without_gui(self) -> None:
        old_display = os.environ.pop("DISPLAY", None)
        old_wayland = os.environ.pop("WAYLAND_DISPLAY", None)
        try:
            self.assertIsNone(app.select_ovpn_file_with_dialog(self.root))
        finally:
            if old_display is not None:
                os.environ["DISPLAY"] = old_display
            if old_wayland is not None:
                os.environ["WAYLAND_DISPLAY"] = old_wayland

    def test_safe_removal_path_requires_home_scope_and_app_name(self) -> None:
        fake_home = self.root / "home"
        fake_home.mkdir()

        self.assertTrue(app.is_safe_removal_path(fake_home / ".local" / "share" / "connectvpn-workbench", fake_home))
        self.assertTrue(app.is_safe_removal_path(fake_home / ".config" / "connectvpn", fake_home))
        self.assertFalse(app.is_safe_removal_path(fake_home, fake_home))
        self.assertFalse(app.is_safe_removal_path(fake_home / ".local", fake_home))
        self.assertFalse(app.is_safe_removal_path(fake_home / ".local" / "share" / "other-tool", fake_home))
        self.assertFalse(app.is_safe_removal_path(Path("/tmp/connectvpn-workbench"), fake_home))

    def test_prepare_log_file_creates_mode_600_file(self) -> None:
        log_path = app.LOGS_DIR / "test.log"

        app.prepare_log_file(log_path)

        self.assertTrue(log_path.exists())
        self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)

    def test_tail_log_reads_latest_lines(self) -> None:
        log_path = app.LOGS_DIR / "test.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        self.assertEqual(app.tail_log(log_path, 2), ["two", "three"])

    def test_tail_log_uses_sudo_fallback_for_unreadable_logs(self) -> None:
        log_path = app.LOGS_DIR / "test.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("hidden\n", encoding="utf-8")

        with mock.patch("pathlib.Path.open", side_effect=PermissionError("denied")):
            with mock.patch.object(app, "tail_log_with_sudo", return_value=["via sudo"]):
                self.assertEqual(app.tail_log(log_path, 2), ["via sudo"])

    def test_tail_log_explains_unreadable_old_root_logs(self) -> None:
        log_path = app.LOGS_DIR / "test.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("hidden\n", encoding="utf-8")

        with mock.patch("pathlib.Path.open", side_effect=PermissionError("denied")):
            with mock.patch.object(app, "tail_log_with_sudo", return_value=None):
                lines = app.tail_log(log_path, 2)

        self.assertIn("Log is not readable by this user", lines[0])
        self.assertTrue(any("New connections" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
