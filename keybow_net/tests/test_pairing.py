"""Local pairing file creation and validation; never emit secret to logs."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from keybow_net.pairing import create_pairing, load_config


class PairingTests(unittest.TestCase):
    def test_generate_read_and_refuse_accidental_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            windows = Path(directory) / "windows.json"
            pi = Path(directory) / "pi.json"
            a, b = create_pairing("192.168.1.23", windows, pi, 8765)
            self.assertEqual((a, b), (windows, pi))
            host, port, secret = load_config(windows)
            self.assertEqual(host, "0.0.0.0")
            self.assertEqual(port, 8765)
            self.assertEqual(len(secret), 32)
            self.assertEqual(load_config(pi), ("192.168.1.23", 8765, secret))
            with self.assertRaises(FileExistsError):
                create_pairing("192.168.1.24", windows, pi)
            self.assertEqual(load_config(windows)[2], secret)
            if os.name != "nt":
                self.assertEqual(windows.stat().st_mode & 0o777, 0o600)
                self.assertEqual(pi.stat().st_mode & 0o777, 0o600)

    def test_reject_short_secrets_bad_port_and_bad_address(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / "wrong.json"
            for record in (
                {"key": "0", "host": "127.0.0.1", "port": 8765},
                {"key": "ab" * 32, "host": "", "port": 8765},
                {"key": "ab" * 32, "host": "localhost", "port": 0},
                {"key": "ab" * 32, "host": "localhost", "port": True},
            ):
                cfg.write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(cfg)
            with self.assertRaises(ValueError):
                create_pairing("", cfg, Path(directory) / "pi.json")
            with self.assertRaises(ValueError):
                create_pairing("localhost", cfg, Path(directory) / "pi.json", port=0)


if __name__ == "__main__":
    unittest.main()
