"""Real Qt window with a mocked authenticated Pi link (not real Codex)."""
import os
import queue
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.core import Phase


class FakeLink:
    def __init__(self):
        self.running = True
        self.connected = True
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
        self.app.processEvents()
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
        self.assertEqual(self.win.session.phase, Phase.WORKING)
        self.assertEqual(self.win._physical_held, {0})

    def test_two_press_events_make_only_one_decision(self):
        arm = self.prepare()
        fake = self.win.keybow_server
        fake.events.put(("key", 1, 0, True, arm))
        fake.events.put(("key", 1, 1, True, arm))
        self.win._poll_link()
        self.assertEqual(self.win.session.phase, Phase.WORKING)
        self.assertEqual(self.win._physical_held, {0, 1})
        self.assertIsNone(self.win._physical_arm)
        fake.events.put(("key", 1, 0, False, None))
        fake.events.put(("key", 1, 1, False, None))
        self.win._poll_link()
        self.assertEqual(self.win._physical_held, set())

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
