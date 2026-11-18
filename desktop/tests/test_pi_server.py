"""Actual localhost PiClient/KeybowServer link with temporary pairing key."""
import json
from pathlib import Path
import queue
import secrets
import socket
import tempfile
import time
import unittest

from desktop.pi_server import KeybowServer
from pi.bridge import PiClient


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(events, kind, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            item = events.get(timeout=min(0.3, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            continue
        if item[0] == kind:
            return item
    raise AssertionError(f"Timed out waiting for {kind}")


class PiLinkSocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.port = free_port()
        self.secret = secrets.token_bytes(32)
        config = Path(self.tmp.name) / "windows.json"
        config.write_text(json.dumps({
            "host": "127.0.0.1", "port": self.port,
            "key": self.secret.hex()}), encoding="utf-8")
        self.server = KeybowServer(config)
        self.server.start()
        self.client = PiClient("127.0.0.1", self.port, self.secret)
        self.client.start()
        self.epoch = wait_for(self.server.events, "connected")[1]
        self.client_epoch = wait_for(self.client.events, "connected")[1]

    def tearDown(self):
        self.client.close()
        self.server.close()
        self.tmp.cleanup()

    def test_two_simultaneous_key_edges_and_remote_status(self):
        self.server.publish("approval", arm="run-1", cue="connected", cue_id=1)
        phase = None
        until = time.monotonic() + 5
        while time.monotonic() < until:
            event = wait_for(self.client.events, "state", 5)
            if event[2]["arm"] == "run-1":
                phase = event[2]
                break
        self.assertEqual(phase["phase"], "approval")
        self.assertTrue(self.client.send_key(0, True, "run-1", self.client_epoch))
        self.assertTrue(self.client.send_key(1, True, "run-1", self.client_epoch))
        self.assertTrue(self.client.send_key(0, False, None, self.client_epoch))
        self.assertTrue(self.client.send_key(1, False, None, self.client_epoch))
        edges = [wait_for(self.server.events, "key") for _ in range(4)]
        self.assertEqual([(e[2], e[3], e[4]) for e in edges], [
            (0, True, "run-1"), (1, True, "run-1"),
            (0, False, None), (1, False, None)])
        self.assertTrue(all(e[1] == self.epoch for e in edges))

    def test_state_update_and_disconnect(self):
        self.server.publish("working", cue="accepted", cue_id=8)
        while True:
            event = wait_for(self.client.events, "state", 5)
            if event[2]["phase"] == "working":
                break
        self.assertEqual(event[2]["cue"], "accepted")
        self.server.close()
        event = wait_for(self.client.events, "disconnected", 5)
        self.assertEqual(event[1], self.client_epoch)
        self.assertFalse(self.client.connected)


if __name__ == "__main__":
    unittest.main()
