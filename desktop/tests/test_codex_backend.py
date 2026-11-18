"""Backend protocol tests use a fake process, not the real Codex CLI."""
import queue
import unittest
from pathlib import Path

from desktop.codex_backend import CodexBackend, JsonRpcProcess, _allowed
from desktop.core import Phase, SessionController


class FakeTransport:
    instances = []

    def __init__(self, command=None):
        self.events = queue.Queue()
        self.sent = []
        self.responses = []
        self.rejections = []
        self.notifications = []
        self.closed = False
        self.counter = 0
        FakeTransport.instances.append(self)

    def start(self):
        pass

    def request(self, method, params):
        self.counter += 1
        self.sent.append((self.counter, method, params))
        return self.counter

    def notify(self, method):
        self.notifications.append(method)

    def respond(self, ident, result):
        self.responses.append((ident, result))

    def reject(self, ident, **kwargs):
        self.rejections.append((ident, kwargs))

    def close(self):
        self.closed = True

    def feed(self, payload):
        self.events.put(payload)


class BackendTests(unittest.TestCase):
    def setUp(self):
        FakeTransport.instances.clear()
        self.session = SessionController(str(Path.cwd()))
        self.backend = CodexBackend(self.session, process_factory=FakeTransport)
        self.assertTrue(self.backend.start("Explain this repository"))
        self.wire = FakeTransport.instances[-1]

    def add_reply(self, ident, result):
        self.wire.feed({"id": ident, "result": result})
        self.backend.poll()

    def ready_turn(self):
        self.assertEqual(self.wire.sent[0][1], "initialize")
        self.add_reply(1, {"userAgent": "fake"})
        self.assertEqual(self.wire.notifications, ["initialized"])
        self.assertEqual(self.wire.sent[1][1], "thread/start")
        self.add_reply(2, {"thread": {"id": "thr-test"}})
        self.assertEqual(self.wire.sent[2][1], "turn/start")
        self.add_reply(3, {"turn": {"id": "turn-1", "status": "inProgress"}})
        self.assertEqual(self.backend.thread_id, "thr-test")
        self.assertEqual(self.backend.turn_id, "turn-1")

    def approval(self, ident=55, **overrides):
        self.wire.feed({
            "method": "item/started", "params": {
                "threadId": "thr-test", "turnId": "turn-1",
                "item": {"id": "item-1", "type": "commandExecution",
                         "command": "python -m unittest", "cwd": str(Path.cwd())}}})
        params = {
            "threadId": "thr-test", "turnId": "turn-1", "itemId": "item-1",
            "reason": "Run tests", "availableDecisions":
            ["accept", "acceptForSession", "decline"]}
        params.update(overrides)
        self.wire.feed({"id": ident, "method": "item/commandExecution/requestApproval",
                        "params": params})
        self.backend.poll()

    def test_initialization_thread_and_readonly_turn(self):
        self.ready_turn()
        self.assertEqual(self.wire.sent[1][2]["approvalPolicy"], "on-request")
        self.assertEqual(self.wire.sent[1][2]["sandbox"], "read-only")
        self.assertEqual(self.wire.sent[2][2]["sandboxPolicy"], {"type": "readOnly"})
        self.assertEqual(self.session.phase, Phase.WORKING)

    def test_agent_message_stream_and_completion(self):
        self.ready_turn()
        for delta in ["Hello ", "world!"]:
            self.wire.feed({"method": "item/agentMessage/delta",
                            "params": {"threadId": "thr-test",
                                       "turnId": "turn-1", "delta": delta}})
        self.wire.feed({"method": "turn/completed",
                        "params": {"threadId": "thr-test",
                                   "turn": {"id": "turn-1", "status": "completed"}}})
        self.backend.poll()
        self.assertEqual(self.session.output, "Hello world!")
        self.assertEqual(self.session.phase, Phase.COMPLETED)

    def test_approval_binding_and_exact_wire_decision(self):
        self.ready_turn()
        self.approval()
        token = self.session.request.request_id
        self.assertEqual(self.session.phase, Phase.APPROVAL)
        self.assertIn("python -m unittest", self.session.request.command)
        self.assertFalse(self.backend.decide("once", 1, token))
        self.assertTrue(self.session.mark_presented(1, token))
        self.assertFalse(self.backend.decide("once", 2, token))
        self.assertTrue(self.backend.decide("once", 1, token))
        self.assertEqual(self.wire.responses, [(55, {"decision": "accept"})])
        self.assertFalse(self.backend.decide("once", 1, token))
        self.assertEqual(self.session.phase, Phase.WORKING)

    def test_decline_continues_until_server_finishes(self):
        self.ready_turn()
        self.approval()
        token = self.session.request.request_id
        self.session.mark_presented(1, token)
        self.assertTrue(self.backend.decide("decline", 1, token))
        self.assertEqual(self.session.phase, Phase.WORKING)
        self.assertEqual(self.wire.responses, [(55, {"decision": "decline"})])
        self.wire.feed({"method": "turn/completed", "params": {
            "threadId": "thr-test", "turn": {"id": "turn-1", "status": "failed",
                                            "error": {"message": "Command declined"}}}})
        self.backend.poll()
        self.assertEqual(self.session.phase, Phase.FAILED)

    def test_unsupported_request_is_rejected(self):
        self.ready_turn()
        self.wire.feed({"id": 88, "method": "item/permissions/requestApproval",
                        "params": {"threadId": "thr-test", "turnId": "turn-1"}})
        self.backend.poll()
        self.assertEqual(self.wire.rejections[0][0], 88)
        self.assertFalse(self.wire.responses)

    def test_missing_command_allows_decline_only(self):
        self.ready_turn()
        self.wire.feed({"id": 8, "method": "item/commandExecution/requestApproval",
                        "params": {"threadId": "thr-test", "turnId": "turn-1",
                                   "itemId": "unseen", "availableDecisions":
                                   ["accept", "decline"]}})
        self.backend.poll()
        self.assertEqual(self.session.request.allowed, ("decline",))
        token = self.session.request.request_id
        self.session.mark_presented(1, token)
        self.assertFalse(self.backend.decide("once", 1, token))

    def test_stale_turn_approval_declined(self):
        self.ready_turn()
        self.wire.feed({"id": 77, "method": "item/commandExecution/requestApproval",
                        "params": {"threadId": "thr-test", "turnId": "turn-old",
                                   "itemId": "item-1", "command": "rm -rf x"}})
        self.backend.poll()
        self.assertEqual(self.wire.responses, [(77, {"decision": "decline"})])
        self.assertEqual(self.session.phase, Phase.WORKING)

    def test_cancel_pending_approval_cancels_and_interrupts(self):
        self.ready_turn()
        self.approval()
        self.assertTrue(self.backend.cancel())
        self.assertEqual(self.session.phase, Phase.CANCELLED)
        self.assertEqual(self.wire.responses[-1], (55, {"decision": "cancel"}))
        self.assertEqual(self.wire.sent[-1][1], "turn/interrupt")
        self.assertFalse(self.backend.decide("once", 1, "1:55"))

    def test_resolved_event_releases_approval(self):
        self.ready_turn()
        self.approval()
        self.wire.feed({"method": "serverRequest/resolved", "params": {
            "threadId": "thr-test", "requestId": 55}})
        self.backend.poll()
        self.assertEqual(self.session.phase, Phase.WORKING)
        self.assertIsNone(self.backend.active_approval)

    def test_process_crash_clears_approval(self):
        self.ready_turn()
        self.approval()
        self.wire.feed({"_closed": True})
        self.backend.poll()
        self.assertTrue(self.wire.closed)
        self.assertEqual(self.session.phase, Phase.FAILED)
        self.assertIsNone(self.session.request)

    def test_multiple_turns_reuse_thread(self):
        self.ready_turn()
        self.wire.feed({"method": "turn/completed", "params": {
            "threadId": "thr-test", "turn": {"id": "turn-1", "status": "completed"}}})
        self.backend.poll()
        self.session.acknowledge()
        self.assertTrue(self.backend.start("Another question"))
        self.assertEqual(self.wire.sent[-1][1], "turn/start")
        self.assertEqual(self.wire.sent[-1][2]["threadId"], "thr-test")

    def test_unknown_decisions_fail_closed(self):
        self.ready_turn()
        self.approval(availableDecisions=["decline"])
        self.assertEqual(self.session.request.allowed, ("decline",))
        token = self.session.request.request_id
        self.session.mark_presented(1, token)
        self.assertFalse(self.backend.decide("session", 1, token))

    def test_server_notification_for_other_thread_ignored(self):
        self.ready_turn()
        self.wire.feed({"method": "item/agentMessage/delta",
                        "params": {"threadId": "other", "turnId": "turn-1",
                                   "delta": "injected"}})
        self.backend.poll()
        self.assertEqual(self.session.output, "")

    def test_network_prompt_uses_host_not_shell(self):
        self.ready_turn()
        self.approval(networkApprovalContext={"host": "example.com", "protocol": "https"},
                      command="misleading shell")
        self.assertEqual(self.session.request.command, "")
        self.assertIn("example.com", self.session.request.description)

    def tearDown(self):
        self.backend.close()


class ProtocolHelpersTests(unittest.TestCase):
    def test_human_command_does_not_allow_session(self):
        p = {"kind": "humanCommand", "availableDecisions":
             ["accept", "acceptForSession", "decline"]}
        self.assertEqual(_allowed(p, "item/commandExecution/requestApproval"),
                         ("once", "decline"))


if __name__ == "__main__":
    unittest.main()
