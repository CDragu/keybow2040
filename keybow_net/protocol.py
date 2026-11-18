"""Keybow Pi<->Windows protocol v1, HMAC-authenticated TCP over a trusted LAN.

This is an integrity/authenticity protocol, NOT TLS: traffic is not encrypted.
Only key events, LED phases and opaque arming tokens are transmitted.
"""
import hashlib
import hmac
import json
import secrets
import select
import socket
import time

VERSION = 1
MAX_LINE = 8192
HEARTBEAT_INTERVAL = 2.0
HEARTBEAT_TIMEOUT = 10.0


class ProtocolError(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def mac(key, value):
    return hmac.new(key, canonical(value), hashlib.sha256).hexdigest()


def check_key(secret):
    if not isinstance(secret, str) or len(secret) != 64:
        raise ValueError("Pairing key must be exactly 64 hex characters.")
    try:
        return bytes.fromhex(secret)
    except ValueError as exc:
        raise ValueError("Pairing key must be valid hex.") from exc


def _nonce(value):
    if not isinstance(value, str) or len(value) != 32:
        raise ProtocolError("Invalid handshake nonce.")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise ProtocolError("Invalid handshake nonce.") from exc
    return value


def _proof(secret, label, server_nonce, client_nonce):
    return hmac.new(secret, (label + "|" + server_nonce + "|" +
                            client_nonce).encode("ascii"), hashlib.sha256).hexdigest()


class LineSocket:
    """Retains partial frames across reads; never uses socket.makefile timeouts."""

    def __init__(self, sock):
        self.sock = sock
        self.buffer = bytearray()

    def send(self, value):
        blob = canonical(value) + b"\n"
        if len(blob) > MAX_LINE:
            raise ProtocolError("Outgoing message too large.")
        self.sock.sendall(blob)

    def read(self, timeout=0.1):
        deadline = time.monotonic() + timeout
        while True:
            end = self.buffer.find(b"\n")
            if end >= 0:
                if end + 1 > MAX_LINE:
                    raise ProtocolError("Incoming frame too large.")
                raw = bytes(self.buffer[:end])
                del self.buffer[:end + 1]
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (UnicodeError, ValueError) as exc:
                    raise ProtocolError("Malformed JSON frame.") from exc
                if not isinstance(parsed, dict):
                    raise ProtocolError("Frame must be an object.")
                return parsed
            if len(self.buffer) >= MAX_LINE:
                raise ProtocolError("Incoming frame too large.")
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            ready, _, _ = select.select([self.sock], [], [], left)
            if not ready:
                return None
            incoming = self.sock.recv(4096)
            if not incoming:
                raise ConnectionError("Peer disconnected.")
            self.buffer.extend(incoming)
            if len(self.buffer) > MAX_LINE and b"\n" not in self.buffer:
                raise ProtocolError("Incoming frame too large.")

    def required(self, timeout=5.0):
        result = self.read(timeout)
        if result is None:
            raise TimeoutError("Keybow authentication timed out.")
        return result


class Session:
    """Strict sequence numbers + per-connection MAC prevent replay/reordering."""

    def __init__(self, line, key):
        self.line = line
        self.key = key
        self.tx = 0
        self.rx = 0
        self.last_received = time.monotonic()

    def send(self, kind, body=None):
        if not isinstance(kind, str) or len(kind) > 32:
            raise ProtocolError("Invalid message kind.")
        self.tx += 1
        payload = {"v": VERSION, "seq": self.tx, "kind": kind, "body": body or {}}
        self.line.send({**payload, "mac": mac(self.key, payload)})

    def read(self, timeout=0.1):
        frame = self.line.read(timeout)
        if frame is None:
            return None
        payload = {k: v for k, v in frame.items() if k != "mac"}
        if (set(payload) != {"v", "seq", "kind", "body"} or
                payload["v"] != VERSION or
                not isinstance(payload["seq"], int) or
                isinstance(payload["seq"], bool) or
                payload["seq"] != self.rx + 1 or
                not isinstance(payload["kind"], str) or
                not isinstance(payload["body"], dict) or
                not isinstance(frame.get("mac"), str) or
                not hmac.compare_digest(mac(self.key, payload), frame["mac"])):
            raise ProtocolError("Frame failed authentication or sequence check.")
        self.rx += 1
        self.last_received = time.monotonic()
        return payload["kind"], payload["body"]

    def expired(self):
        return time.monotonic() - self.last_received > HEARTBEAT_TIMEOUT


def server_handshake(sock, secret):
    """Mutual challenge-response; secret is 32 raw bytes."""
    line = LineSocket(sock)
    s = secrets.token_hex(16)
    line.send({"type": "challenge", "v": VERSION, "nonce": s})
    auth = line.required()
    if auth.get("type") != "auth" or auth.get("v") != VERSION:
        raise ProtocolError("Unsupported handshake.")
    c = _nonce(auth.get("nonce"))
    expected = _proof(secret, "client", s, c)
    if not isinstance(auth.get("proof"), str) or not hmac.compare_digest(
            expected, auth["proof"]):
        raise ProtocolError("Incorrect pairing key.")
    line.send({"type": "welcome", "v": VERSION,
               "proof": _proof(secret, "server", s, c)})
    ready = line.required()
    if ready.get("type") != "ready" or not hmac.compare_digest(
            str(ready.get("proof", "")), _proof(secret, "ready", s, c)):
        raise ProtocolError("Peer failed final authentication.")
    session_key = hmac.new(secret, ("session|" + s + "|" + c).encode("ascii"),
                           hashlib.sha256).digest()
    return Session(line, session_key)


def client_handshake(sock, secret):
    line = LineSocket(sock)
    challenge = line.required()
    if challenge.get("type") != "challenge" or challenge.get("v") != VERSION:
        raise ProtocolError("Unexpected server handshake.")
    s = _nonce(challenge.get("nonce"))
    c = secrets.token_hex(16)
    line.send({"type": "auth", "v": VERSION, "nonce": c,
               "proof": _proof(secret, "client", s, c)})
    response = line.required()
    if (response.get("type") != "welcome" or response.get("v") != VERSION or
            not isinstance(response.get("proof"), str) or
            not hmac.compare_digest(response["proof"],
                                    _proof(secret, "server", s, c))):
        raise ProtocolError("Server pairing authentication failed.")
    line.send({"type": "ready", "proof": _proof(secret, "ready", s, c)})
    session_key = hmac.new(secret, ("session|" + s + "|" + c).encode("ascii"),
                           hashlib.sha256).digest()
    return Session(line, session_key)
