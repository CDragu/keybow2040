"""Real Qt window with a mocked authenticated Pi link (not real Codex)."""
import os
import queue
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from desktop.app import MainWindow
from desktop.core import Phase


class FakeLink:
    def __init__(self):
        self.running = True
        self.connected = True
        self.connection_epoch = 1
        self.events = queue.Queue()
        self.published = []

    def publish(self, phase, arm=None, cue=None, cue_id=0):
        self.published.append((phase, arm, cue, cue_id))

    def close(self):
        self.running = False


class PhysicalUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.win = MainWindow()
        self.win.keybow_server = FakeLink()
        self.win.auto_play.setChecked(False)
        self.win.show()
        self.win.activateWindow()
        self.app.processEvents()
        self.win.keybow_server.events.put(("connected", 1))
        self.win.keybow_server.events.put(("snapshot", 1, []))
        self.win._poll_link()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.app.processEvents()

    def prepare(self):
        self.win.send_button.click()
        self.win.step_button.click()
        self.app.processEvents()
        self.assertEqual(self.win.session.phase, Phase.APPROVAL)
        self.win.activateWindow()
        QTest.qWait(50)
        self.win._sync_link()
        self.assertEqual(self.win.keybow_server.published[-1][0], "approval")
        arm = self.win.keybow_server.published[-1][1]
        self.assertIsNotNone(arm)
        return arm

    def test_wrong_or_missing_arm_cannot_approve(self):
        arm = self.prepare()
        fake = self.win.keybow_server
        fake.events.put(("key", 1, 0, True, "stale-arm"))
        self.win._poll_link()
        self.assertEqual(self.win.session.phase, Phase.APPROVAL)
        fake.events.put(("key", 1, 0, False, None))
        fake.events.put(("key", 1, 0, True, arm))
        self.win._poll_link()
        QTest.qWait(130)
        self.win._poll_link()
        self.assertEqual(self.win.session.phase, Phase.WORKING)
        self.assertEqual(self.win._physical_held, {0})

    def test_ambiguous_simultaneous_approval_chord_is_ignored(self):
        arm = self.prepare()
        fake = self.win.keybow_server
        fake.events.put(("key", 1, 0, True, arm))
        fake.events.put(("key", 1, 1, True, arm))
        self.win._poll_link()
        QTest.qWait(130)
        self.win._poll_link()
        self.assertEqual(self.win.session.phase, Phase.APPROVAL)
        self.assertEqual(self.win._physical_held, {0, 1})
        fake.events.put(("key", 1, 0, False, None))
        fake.events.put(("key", 1, 1, False, None))
        self.win._poll_link()
        self.assertEqual(self.win._physical_held, set())
        self.assertFalse(self.win._ambiguous_chord)
        # Fresh individual key-down is accepted after the chord is released.
        fake.events.put(("key", 1, 0, True, arm))
        self.win._poll_link()
        QTest.qWait(130)
        self.win._poll_link()
        self.assertEqual(self.win.session.phase, Phase.WORKING)

    def test_reconnect_waits_for_snapshot_and_held_key_needs_release(self):
        old_arm = self.prepare()
        fake = self.win.keybow_server
        fake.connection_epoch = 2
        fake.events.put(("connected", 2))
        fake.events.put(("key", 2, 0, True, old_arm))
        self.win._poll_link()
        self.assertIsNone(self.win._physical_arm)
        fake.events.put(("snapshot", 2, [0]))
        self.win._poll_link()
        arm = self.win._physical_arm
        self.assertNotEqual(old_arm, arm)
        fake.events.put(("key", 2, 0, True, arm))
        self.win._poll_link()
        QTest.qWait(110)
        self.assertEqual(self.win.session.phase, Phase.APPROVAL)
        fake.events.put(("key", 2, 0, False, None))
        fake.events.put(("key", 2, 0, True, arm))
        self.win._poll_link()
        QTest.qWait(130)
        self.assertEqual(self.win.session.phase, Phase.WORKING)

    def test_lock_invalidates_buffered_press_and_rotates_the_arm(self):
        arm = self.prepare()
        self.win.keybow_server.events.put(("key", 1, 0, True, arm))
        self.win._poll_link()
        self.win._set_session_locked(True)
        QTest.qWait(130)
        self.assertIsNone(self.win._physical_arm)
        self.assertEqual(self.win.session.phase, Phase.APPROVAL)
        self.win._set_session_locked(False)
        QTest.qWait(50)
        self.assertIsNotNone(self.win._physical_arm)
        self.assertNotEqual(self.win._physical_arm, arm)

    def test_backlogged_key_press_cannot_approve(self):
        arm = self.prepare()
        self.win.keybow_server.events.put(("key", 1, 0, True, arm, time.monotonic() - 2))
        self.win._poll_link()
        QTest.qWait(130)
        self.assertEqual(self.win.session.phase, Phase.APPROVAL)

    def test_disconnected_pi_disarms_request(self):
        self.prepare()
        fake = self.win.keybow_server
        fake.events.put(("disconnected", 1))
        self.win._poll_link()
        self.assertIsNone(self.win._physical_arm)
        self.assertEqual(self.win._physical_epoch, 0)
        self.assertEqual(self.win.session.phase, Phase.APPROVAL)


if __name__ == "__main__":
    unittest.main()
