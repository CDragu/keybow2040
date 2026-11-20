"""The real virtual keypad and authenticated Pi dispatcher, with fake Spotify."""
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.core import Phase
from desktop.spotify import SPOTIFY_KEYS
from desktop.tests.test_physical_ui import FakeLink


class FakeSpotify:
    supported = True

    def __init__(self):
        self.keys = []
        self.messages = []
        self.closed = False

    def submit(self, index):
        self.keys.append(index)
        return True

    def poll(self):
        messages, self.messages = self.messages, []
        return messages

    def close(self):
        self.closed = True


class SpotifyUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.spotify = FakeSpotify()
        self.window = MainWindow(spotify=self.spotify)
        self.window.auto_play.setChecked(False)
        self.window.keybow_server = FakeLink()
        self.window.show()
        self.app.processEvents()
        self.connect(1)

    def connect(self, epoch, held=()):
        link = self.window.keybow_server
        link.connection_epoch = epoch
        link.events.put(("connected", epoch))
        link.events.put(("snapshot", epoch, list(held)))
        self.window._poll_link()

    def key(self, index, pressed=True, epoch=None, when=None):
        self.window.keybow_server.events.put((
            "key", epoch or self.window.keybow_server.connection_epoch,
            index, pressed, None, time.monotonic() if when is None else when))

    def tearDown(self):
        self.window.close()
        self.assertTrue(self.spotify.closed)
        self.app.processEvents()

    def test_all_virtual_controls_and_status(self):
        for index, (_, name) in SPOTIFY_KEYS.items():
            key = self.window.virtual_buttons[index]
            self.assertIn(name, key.text())
            self.assertTrue(key.isEnabled())
            key.click()
        self.assertEqual(self.spotify.keys, list(SPOTIFY_KEYS))
        self.assertEqual(self.window.session.phase, Phase.IDLE)
        self.spotify.messages.append("Spotify volume +5%.")
        self.window._poll_link()
        self.assertEqual(self.window.spotify_status.text(), "Spotify volume +5%.")

    def test_keypad_stays_readable_in_default_window(self):
        upper, lower = self.window.virtual_buttons[9], self.window.virtual_buttons[6]
        self.assertGreaterEqual(lower.y(), upper.y() + upper.height())
        self.assertGreater(self.window.prompt_edit.height(), 40)
        self.assertGreater(self.window.centralWidget().verticalScrollBar().maximum(), 0)

    def test_physical_controls_work_in_background_once_per_fresh_press(self):
        self.window.hide()
        for index in SPOTIFY_KEYS:
            self.key(index)
            self.key(index)  # duplicate edge must not toggle twice
            self.key(index, False)
        self.window._poll_link()
        self.assertEqual(self.spotify.keys, list(SPOTIFY_KEYS))
        self.assertEqual(self.window.session.phase, Phase.IDLE)

    def test_stale_disconnected_and_pre_snapshot_keys_are_ignored(self):
        self.key(6, when=time.monotonic() - 2)
        self.window._poll_link()
        self.key(6, False)
        self.window._poll_link()
        self.key(6, epoch=99)
        self.window._poll_link()
        self.window.keybow_server.connected = False
        self.key(7)
        self.window._poll_link()
        self.assertEqual(self.spotify.keys, [])
        self.window.keybow_server.connected = True
        self.window.keybow_server.events.put(("connected", 2))
        self.key(8, epoch=2)
        self.window._poll_link()
        self.assertEqual(self.spotify.keys, [])

    def test_held_key_across_reconnect_requires_release_and_new_press(self):
        self.connect(2, held=[6])
        self.key(6)
        self.window._poll_link()
        self.assertEqual(self.spotify.keys, [])
        self.key(6, False)
        self.key(6)
        self.window._poll_link()
        self.assertEqual(self.spotify.keys, [6])

    def test_lock_blocks_virtual_and_physical_controls(self):
        self.window._set_session_locked(True)
        self.assertFalse(self.window.virtual_buttons[6].isEnabled())
        self.window.press_virtual(6)
        self.key(7)
        self.window._poll_link()
        self.assertEqual(self.spotify.keys, [])
        self.window._set_session_locked(False)
        self.key(7, False)
        self.key(7)
        self.window._poll_link()
        self.assertEqual(self.spotify.keys, [7])

    def test_spotify_controls_preserve_pending_codex_request(self):
        self.window.send_button.click()
        self.window.step_button.click()
        self.app.processEvents()
        request = self.window.session.request
        self.assertIsNotNone(request)
        self.window.virtual_buttons[6].click()
        self.key(7)
        self.window._poll_link()
        self.assertEqual(self.spotify.keys, [6, 7])
        self.assertEqual(self.window.session.phase, Phase.APPROVAL)
        self.assertIs(self.window.session.request, request)


if __name__ == "__main__":
    unittest.main()
