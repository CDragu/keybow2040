import unittest

from desktop.core import Phase, SessionController
from desktop.simulator import SCENARIOS, SimulatedCodex


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.session = SessionController(project="/project")
        self.backend = SimulatedCodex(self.session)

    def accept(self, choice="once"):
        request = self.session.request
        self.assertIsNotNone(request)
        self.assertTrue(self.session.mark_presented(
            self.session.run_id, request.request_id))
        self.assertTrue(self.session.decide(
            choice, self.session.run_id, request.request_id))

    def test_success_and_failure_scenarios(self):
        self.assertTrue(self.backend.start("test", "success"))
        self.assertTrue(self.backend.advance())
        self.assertEqual(self.session.phase, Phase.COMPLETED)
        self.session.acknowledge()
        self.assertTrue(self.backend.start("test", "failure"))
        self.backend.advance()
        self.assertEqual(self.session.phase, Phase.FAILED)

    def test_command_approval_pauses_simulator(self):
        self.backend.start("test", "command")
        self.backend.advance()
        self.assertEqual(self.session.phase, Phase.APPROVAL)
        self.assertFalse(self.backend.advance())
        self.accept("session")
        self.assertTrue(self.backend.advance())
        self.assertEqual(self.session.phase, Phase.COMPLETED)

    def test_two_approvals_have_separate_ids(self):
        self.backend.start("test", "two")
        self.backend.advance()
        first = self.session.request.request_id
        self.accept()
        self.backend.advance()
        second = self.session.request.request_id
        self.assertNotEqual(first, second)
        self.assertEqual(self.session.phase, Phase.APPROVAL)
        self.assertFalse(self.session.decide("once", 1, first))
        self.accept()
        self.backend.advance()
        self.assertEqual(self.session.phase, Phase.COMPLETED)

    def test_decline_stops_script(self):
        self.backend.start("test", "files")
        self.backend.advance()
        self.accept("decline")
        self.assertFalse(self.backend.advance())
        self.assertEqual(self.session.phase, Phase.FAILED)

    def test_scenario_options_have_usable_labels(self):
        self.assertGreaterEqual(len(SCENARIOS), 5)
        self.assertTrue(all(SCENARIOS.values()))


if __name__ == "__main__":
    unittest.main()
