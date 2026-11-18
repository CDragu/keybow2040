import unittest

from desktop.core import ApprovalRequest, Phase, SessionController


def command_request(request_id="r1"):
    return ApprovalRequest(
        request_id=request_id,
        kind="Command execution",
        command="echo harmless",
        cwd="/test",
        reason="Demo",
        description="Preview",
        allowed=("once", "session", "decline"),
    )


class SessionControllerTests(unittest.TestCase):
    def setUp(self):
        self.session = SessionController(project="/test")

    def test_start_and_finish_success(self):
        self.assertFalse(self.session.start(" "))
        self.assertTrue(self.session.start("test prompt"))
        self.assertFalse(self.session.start("another"))
        self.assertTrue(self.session.complete())
        self.assertEqual(self.session.phase, Phase.COMPLETED)
        self.assertTrue(self.session.acknowledge())
        self.assertEqual(self.session.phase, Phase.IDLE)

    def test_no_approval_before_review_visible(self):
        self.session.start("task")
        self.assertTrue(self.session.request_approval(command_request()))
        self.assertFalse(self.session.decide("once", 1, "r1"))
        self.assertFalse(self.session.virtual_key(0, 1, "r1"))
        self.assertEqual(self.session.phase, Phase.APPROVAL)
        self.assertTrue(self.session.mark_presented(1, "r1"))
        self.assertTrue(self.session.decide("once", 1, "r1"))
        self.assertEqual(self.session.phase, Phase.WORKING)

    def test_reject_stale_token_and_unknown_choices(self):
        self.session.start("task")
        self.session.request_approval(command_request())
        self.session.mark_presented(1, "r1")
        self.assertFalse(self.session.decide("once", 999, "r1"))
        self.assertFalse(self.session.decide("once", 1, "r2"))
        self.assertFalse(self.session.decide("accept-anything", 1, "r1"))
        self.assertTrue(self.session.virtual_key(1, 1, "r1"))
        self.assertFalse(self.session.virtual_key(1, 1, "r1"))

    def test_decline_is_terminal(self):
        self.session.start("task")
        self.session.request_approval(command_request())
        self.session.mark_presented(1, "r1")
        self.assertTrue(self.session.virtual_key(2, 1, "r1"))
        self.assertEqual(self.session.phase, Phase.FAILED)
        self.assertIsNone(self.session.request)
        self.assertFalse(self.session.complete())

    def test_cancel_clears_pending(self):
        self.session.start("task")
        self.session.request_approval(command_request())
        self.assertTrue(self.session.virtual_key(3))
        self.assertEqual(self.session.phase, Phase.CANCELLED)
        self.assertIsNone(self.session.presented)
        self.assertFalse(self.session.virtual_key(0, 1, "r1"))
        self.assertTrue(self.session.virtual_key(4))

    def test_reuse_request_ids_rejected_within_turn(self):
        self.session.start("task")
        self.session.request_approval(command_request())
        self.session.mark_presented(1, "r1")
        self.session.decide("once", 1, "r1")
        self.assertFalse(self.session.request_approval(command_request()))
        self.assertTrue(self.session.request_approval(command_request("r2")))

    def test_wrong_decision_not_allowed(self):
        self.session.start("task")
        req = ApprovalRequest("r1", "File change", "edit", "/test",
                              "demo", allowed=("once", "decline"))
        self.assertTrue(self.session.request_approval(req))
        self.assertTrue(self.session.mark_presented(1, "r1"))
        self.assertFalse(self.session.virtual_key(1, 1, "r1"))
        self.assertEqual(self.session.phase, Phase.APPROVAL)

    def test_keypresses_are_inert_without_approval(self):
        self.assertFalse(self.session.virtual_key(0))
        self.assertFalse(self.session.virtual_key(8))
        self.session.start("task")
        self.assertFalse(self.session.virtual_key(2))
        self.assertFalse(self.session.virtual_key(4))
        self.assertEqual(self.session.phase, Phase.WORKING)

    def test_led_layout_for_approval(self):
        self.session.start("task")
        self.session.request_approval(command_request())
        leds = self.session.leds()
        self.assertEqual(len(leds), 12)
        self.assertEqual([item.mode for item in leds[:4]], ["pulse"] * 4)
        self.assertEqual(leds[4].mode, "solid")

    def test_reject_old_run_token_after_restart(self):
        self.session.start("first")
        self.session.request_approval(command_request())
        self.session.mark_presented(1, "r1")
        self.session.cancel()
        self.session.acknowledge()
        self.session.start("second")
        self.session.request_approval(command_request())
        self.session.mark_presented(2, "r1")
        self.assertFalse(self.session.virtual_key(0, 1, "r1"))
        self.assertTrue(self.session.virtual_key(0, 2, "r1"))

    def test_project_locked_during_turn(self):
        self.assertTrue(self.session.set_project("/other"))
        self.session.start("task")
        self.assertFalse(self.session.set_project("/unexpected"))
        self.assertEqual(self.session.project, "/other")


if __name__ == "__main__":
    unittest.main()
