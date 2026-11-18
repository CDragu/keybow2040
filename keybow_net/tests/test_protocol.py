"""No network services or Pi hardware required; localhost socket pairs only."""
import socket
import threading
import unittest

from keybow_net.protocol import (
    ProtocolError, VERSION, client_handshake, server_handshake, canonical, mac
)


class ProtocolTests(unittest.TestCase):
    SECRET = bytes(range(32))

    def pair(self):
        left, right = socket.socketpair()
        left.settimeout(3)
        right.settimeout(3)
        results = []
        errors = []

        def server():
            try:
                results.append(server_handshake(left, self.SECRET))
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        try:
            client = client_handshake(right, self.SECRET)
            thread.join(timeout=3)
            if errors:
                raise errors[0]
            self.assertFalse(thread.is_alive())
            return left, right, results[0], client
        except BaseException:
            left.close()
            right.close()
            raise

    def test_mutual_pairing_and_bidirectional_frames(self):
        left, right, server, client = self.pair()
        try:
            client.send("key", {"key": 0, "pressed": True, "arm": None})
            self.assertEqual(server.read(1)[0], "key")
            server.send("state", {"phase": "approval", "arm": "test"})
            self.assertEqual(client.read(1)[1]["arm"], "test")
            client.send("key", {"key": 1, "pressed": True, "arm": "test"})
            self.assertEqual(server.read(1)[1]["key"], 1)
            self.assertEqual(server.rx, 2)
        finally:
            left.close()
            right.close()

    def test_missing_shared_secret_rejected(self):
        left, right = socket.socketpair()
        left.settimeout(2)
        right.settimeout(2)
        errors = []
        t = threading.Thread(
            target=lambda: self._try_server(left, errors), daemon=True)
        t.start()
        try:
            with self.assertRaises((ProtocolError, ConnectionError, TimeoutError)):
                client_handshake(right, bytes(reversed(self.SECRET)))
        finally:
            right.close()
            t.join(timeout=3)
            left.close()
        self.assertTrue(errors)

    def _try_server(self, sock, errors):
        try:
            server_handshake(sock, self.SECRET)
        except (ProtocolError, ConnectionError, TimeoutError, OSError) as exc:
            errors.append(exc)
        finally:
            sock.close()

    def test_mac_tampering_and_replay_rejected(self):
        left, right, server, client = self.pair()
        try:
            frame = {"v": VERSION, "seq": 1, "kind": "key",
                     "body": {"key": 0, "pressed": True, "arm": None}}
            wire = {**frame, "mac": mac(client.key, frame)}
            client.line.send(wire)
            self.assertEqual(server.read(1)[0], "key")
            client.line.send(wire)
            with self.assertRaises(ProtocolError):
                server.read(1)
        finally:
            left.close()
            right.close()

        left, right, server, client = self.pair()
        try:
            client.line.send({"v": VERSION, "seq": 1, "kind": "key",
                              "body": {"key": 5}, "mac": "0" * 64})
            with self.assertRaises(ProtocolError):
                server.read(1)
        finally:
            left.close()
            right.close()

    def test_split_and_multiple_frames(self):
        left, right, server, client = self.pair()
        try:
            client.send("ping")
            client.send("ping")
            self.assertEqual(server.read(1)[0], "ping")
            self.assertEqual(server.read(1)[0], "ping")
            self.assertIsNone(server.read(0.001))
        finally:
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
