"""Offscreen UI tests for selecting and operating the real backend adapter."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from desktop.app import MainWindow
from desktop.core import Phase
from desktop.tests.test_codex_backend import FakeTransport


class RealModeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        FakeTransport.instances.clear()
        self.window = MainWindow()
        self.window.real_backend.process_factory = FakeTransport
        self.window.show()
        self.app.processEvents()
        self.window.mode_picker.setCurrentIndex(
            self.window.mode_picker.findData("real"))
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def feed(self, message):
        FakeTransport.instances[-1].feed(message)
        self.window.real_backend.poll()
        self.app.processEvents()

    def start(self):
        self.window.prompt_edit.setPlainText("Explain this repository")
        self.window.send_button.click()
        self.assertEqual(self.window.session.phase, Phase.WORKING)
        self.assertFalse(self.window.scenario_picker.isEnabled())
        self.assertFalse(self.window.auto_play.isEnabled())
        self.assertIn("REAL CODEX", self.window.mode_badge.text())
        self.feed({"id": 1, "result": {"userAgent": "fake"}})
        self.feed({"id": 2, "result": {"thread": {"id": "tid"}}})
        self.feed({"id": 3, "result": {"turn": {"id": "turn-1"}}})
        self.assertEqual(self.window.real_backend.turn_id, "turn-1")

    def test_real_mode_streamed_output_visible(self):
        self.start()
        self.feed({"method": "item/agentMessage/delta",
                   "params": {"threadId": "tid", "turnId": "turn-1",
                              "delta": "Hello from the real protocol adapter."}})
        self.assertIn("Hello from", self.window.reply_view.toPlainText())
        self.feed({"method": "turn/completed", "params": {
            "threadId": "tid", "turn": {"id": "turn-1", "status": "completed"}}})
        self.assertEqual(self.window.session.phase, Phase.COMPLETED)
        self.assertTrue(self.window.virtual_buttons[4].isEnabled())

    def test_real_virtual_approval_and_cancel(self):
        self.start()
        self.feed({"method": "item/started", "params": {
            "threadId": "tid", "turnId": "turn-1",
            "item": {"id": "item-1", "type": "commandExecution",
                     "command": "echo safe", "cwd": "."}}})
        self.feed({"id": 70, "method": "item/commandExecution/requestApproval",
                   "params": {"threadId": "tid", "turnId": "turn-1",
                              "itemId": "item-1", "reason": "Command",
                              "availableDecisions": ["accept", "decline"]}})
        self.app.processEvents()
        self.assertEqual(self.window.session.phase, Phase.APPROVAL)
        self.assertFalse(self.window.session_button.isEnabled())
        self.assertIn("echo safe", self.window.approval_command.toPlainText())
        self.window.virtual_buttons[0].click()
        self.assertEqual(self.window.session.phase, Phase.WORKING)
        self.assertEqual(FakeTransport.instances[-1].responses,
                         [(70, {"decision": "accept"})])
        self.window.cancel_button.click()
        self.assertEqual(self.window.session.phase, Phase.CANCELLED)
        self.assertEqual(FakeTransport.instances[-1].sent[-1][1],
                         "turn/interrupt")

    def test_switching_mode_disables_real_backend_at_idle(self):
        self.assertTrue(self.window.sandbox_picker.isEnabled())
        self.assertFalse(self.window.step_button.isVisible())
        self.window.mode_picker.setCurrentIndex(
            self.window.mode_picker.findData("simulator"))
        self.assertFalse(self.window.sandbox_picker.isEnabled())
        self.assertTrue(self.window.scenario_picker.isEnabled())


if __name__ == "__main__":
    unittest.main()
