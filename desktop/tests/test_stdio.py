"""End-to-end stdio JSON-RPC checks without invoking real Codex."""
import queue
import sys
import unittest

from desktop.codex_backend import JsonRpcProcess


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


if __name__ == "__main__":
    unittest.main()
