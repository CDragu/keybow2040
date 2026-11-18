"""Offscreen Qt widget tests: no Windows machine, Keybow, or Codex needed."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.core import Phase


class DesktopUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.window.auto_play.setChecked(False)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def start(self, scenario):
        index = self.window.scenario_picker.findData(scenario)
        self.assertGreaterEqual(index, 0)
        self.window.scenario_picker.setCurrentIndex(index)
        self.window.send_button.click()
        self.assertEqual(self.window.session.phase, Phase.WORKING)

    def step(self):
        self.window.step_button.click()
        QTest.qWait(50)

    def test_command_approval_via_virtual_key(self):
        self.start("command")
        self.step()
        self.assertEqual(self.window.session.phase, Phase.APPROVAL)
        self.assertFalse(self.window.approval_card.isHidden())
        self.assertIn("python", self.window.approval_command.text())
        self.assertTrue(self.window.virtual_buttons[0].isEnabled())
        self.window.virtual_buttons[0].click()
        self.assertEqual(self.window.session.phase, Phase.WORKING)
        self.step()
        self.assertEqual(self.window.session.phase, Phase.COMPLETED)
        self.window.virtual_buttons[4].click()
        self.assertEqual(self.window.session.phase, Phase.IDLE)

    def test_file_change_disallows_session_approval(self):
        self.start("files")
        self.step()
        self.assertEqual(self.window.session.phase, Phase.APPROVAL)
        self.assertFalse(self.window.session_button.isEnabled())
        self.assertIn("parser", self.window.approval_description.toPlainText())
        self.window.decline_button.click()
        self.assertEqual(self.window.session.phase, Phase.FAILED)
        self.assertTrue(self.window.approval_card.isHidden())

    def test_cancel_and_failure_scenarios(self):
        self.start("command")
        self.window.cancel_button.click()
        self.assertEqual(self.window.session.phase, Phase.CANCELLED)
        self.window.ack_button.click()
        self.start("failure")
        self.step()
        self.assertEqual(self.window.session.phase, Phase.FAILED)

    def test_multiple_approvals_and_stale_press(self):
        self.start("two")
        self.step()
        old_token = self.window._current_token()
        self.window.once_button.click()
        self.step()
        self.assertEqual(self.window.session.phase, Phase.APPROVAL)
        self.assertFalse(self.window.session.virtual_key(0, *old_token))
        self.app.processEvents()
        self.window.virtual_buttons[0].click()
        self.step()
        self.assertEqual(self.window.session.phase, Phase.COMPLETED)

    def test_reserved_keys_and_idle_approval_are_inert(self):
        self.assertFalse(self.window.virtual_buttons[0].isEnabled())
        self.assertFalse(self.window.virtual_buttons[9].isEnabled())
        self.assertFalse(self.window.session.virtual_key(0))
        self.assertEqual(self.window.session.phase, Phase.IDLE)


if __name__ == "__main__":
    unittest.main()
