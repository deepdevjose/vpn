import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


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

    def test_safe_removal_path_requires_home_scope_and_app_name(self) -> None:
        fake_home = self.root / "home"
        fake_home.mkdir()

        self.assertTrue(app.is_safe_removal_path(fake_home / ".local" / "share" / "connectvpn-workbench", fake_home))
        self.assertTrue(app.is_safe_removal_path(fake_home / ".config" / "connectvpn", fake_home))
        self.assertFalse(app.is_safe_removal_path(fake_home, fake_home))
        self.assertFalse(app.is_safe_removal_path(fake_home / ".local", fake_home))
        self.assertFalse(app.is_safe_removal_path(fake_home / ".local" / "share" / "other-tool", fake_home))
        self.assertFalse(app.is_safe_removal_path(Path("/tmp/connectvpn-workbench"), fake_home))


if __name__ == "__main__":
    unittest.main()
