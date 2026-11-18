import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from desktop.settings import DesktopSettings, load_windows_pairing, pair_device
from keybow_net.pairing import create_pairing, load_config


class SettingsTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_legacy_preferences_migrate_without_sandbox_or_link_defaults(self):
        path = self.root / "settings.json"
        path.write_text(json.dumps({
            "project": "work folder", "backend": "real",
            "sandbox": "workspace-write", "listener": True,
        }), encoding="utf-8")
        settings = DesktopSettings.load(path)
        self.assertEqual(settings, DesktopSettings("work folder", "real"))
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"version", "project", "backend", "codex_path"})
        self.assertEqual(DesktopSettings.load(path), settings)

    def test_unknown_version_is_preserved(self):
        path = self.root / "settings.json"
        content = '{"version": 999}'
        path.write_text(content, encoding="utf-8")
        with self.assertRaises(ValueError):
            DesktopSettings.load(path)
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_pairing_binds_selected_ip_and_refuses_overwrite(self):
        windows = self.root / "windows.json"
        pi = self.root / "pi.json"
        self.assertEqual(pair_device("192.168.1.4", pi, 18765, windows), pi)
        self.assertEqual(load_windows_pairing(windows), load_config(pi))
        original = windows.read_bytes()
        with self.assertRaises(FileExistsError):
            pair_device("192.168.1.5", self.root / "another.json", 18765, windows)
        self.assertEqual(windows.read_bytes(), original)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_legacy_d2_pairing_migrates_in_place_under_dpapi(self):
        windows, pi = create_pairing("192.168.1.4", self.root / "windows.json")
        previous = load_config(windows)
        self.assertEqual(load_windows_pairing(windows), previous)
        record = json.loads(windows.read_text(encoding="utf-8"))
        self.assertNotIn("key", record)
        self.assertIn("key_dpapi", record)
        self.assertEqual(load_windows_pairing(windows), previous)
        self.assertEqual(load_config(pi)[2], previous[2])
