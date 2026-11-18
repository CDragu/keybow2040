"""End-to-end stdio JSON-RPC checks without invoking real Codex."""
import queue
import sys
import time
import unittest
from pathlib import Path

from desktop.codex_backend import CodexBackend, JsonRpcProcess
from desktop.core import Phase, SessionController


class TransportIntegrationTests(unittest.TestCase):
    def test_actual_child_process_and_approval_response(self):
        process = JsonRpcProcess([sys.executable, "-u", "-m",
                                  "desktop.tests.fake_app_server"])
        process.start()
        try:
            request_id = process.request("initialize", {"clientInfo": {
                "name": "keybow_test", "title": "Test", "version": "1"}})
            reply = process.events.get(timeout=8)
            self.assertEqual(reply["id"], request_id)
            self.assertIn("result", reply)
            process.notify("initialized")

            tid = process.request("thread/start", {"cwd": "."})
            self.assertEqual(process.events.get(timeout=8)["id"], tid)

            turn_request_id = process.request("turn/start", {
                "threadId": "mock-thread",
                "input": [{"type": "text", "text": "Hello"}]})
            messages = []
            for _ in range(8):
                msg = process.events.get(timeout=8)
                messages.append(msg)
                if msg.get("method") == "item/commandExecution/requestApproval":
                    self.assertEqual(msg["id"], 987)
                    process.respond(msg["id"], {"decision": "accept"})
                if msg.get("method") == "turn/completed":
                    break
            self.assertTrue(any(m.get("id") == turn_request_id for m in messages))
            self.assertTrue(any(m.get("method") == "item/agentMessage/delta"
                                for m in messages))
            self.assertEqual(messages[-1]["params"]["turn"]["status"], "completed")
        finally:
            process.close()


class BackendSubprocessIntegrationTests(unittest.TestCase):
    def test_full_real_backend_with_local_fake_server(self):
        session = SessionController(str(Path.cwd()))
        backend = CodexBackend(
            session, command=[sys.executable, "-u", "-m",
                              "desktop.tests.fake_app_server"])
        try:
            self.assertTrue(backend.start("Explain this project"))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and session.phase != Phase.APPROVAL:
                backend.poll()
                time.sleep(0.01)
            self.assertEqual(session.phase, Phase.APPROVAL)
            self.assertIn("Message from fake", session.output)
            token = session.request.request_id
            self.assertTrue(session.mark_presented(session.run_id, token))
            self.assertTrue(backend.decide("once", session.run_id, token))
            while time.monotonic() < deadline and session.phase != Phase.COMPLETED:
                backend.poll()
                time.sleep(0.01)
            self.assertEqual(session.phase, Phase.COMPLETED)
            self.assertIn("Decision: accept.", session.output)
        finally:
            backend.close()


if __name__ == "__main__":
    unittest.main()
