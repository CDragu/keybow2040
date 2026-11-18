"""Opt-in authenticated Windows TCP endpoint for one original Pi Keybow.

Networking is exclusively on a background worker thread. The PySide6 main
thread owns Codex approvals and drains .events. No Codex code runs here.
"""
import queue
import socket
import threading
import time

from keybow_net.pairing import WINDOWS_CONFIG, load_config
from keybow_net.protocol import (
    HEARTBEAT_INTERVAL, ProtocolError, server_handshake
)


class KeybowServer:
    def __init__(self, config=WINDOWS_CONFIG):
        self.config = config
        self.events = queue.Queue(maxsize=4096)
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._listener = None
        self._peer = None
        self._epoch = 0
        self._state = {"phase": "idle", "arm": None, "cue": None, "cue_id": 0}
        self.connected = False
        self.running = False

    def start(self):
        if self.running:
            return
        host, port, secret = load_config(self.config, server=True)
        # Bind before starting thread to surface port/firewall errors in UI.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, port))
            listener.listen(1)
            listener.settimeout(0.25)
        except Exception:
            listener.close()
            raise
        self._listener = listener
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(
            target=self._run, args=(listener, secret), daemon=True,
            name="keybow-windows-server")
        self._thread.start()
        self._emit(("listening", host, port))

    def _emit(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Never silently drop release edges; force a disconnect and re-arm.
            self._stop.set()
            with self._lock:
                peer = self._peer
            if peer is not None:
                try:
                    peer.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _run(self, listener, secret):
        while not self._stop.is_set():
            try:
                peer, _addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with peer:
                peer.settimeout(3.0)
                with self._lock:
                    self._peer = peer
                try:
                    channel = server_handshake(peer, secret)
                    self._epoch += 1
                    epoch = self._epoch
                    self.connected = True
                    self._emit(("connected", epoch))
                    self._serve(channel, epoch)
                except (OSError, TimeoutError, ConnectionError, ProtocolError) as exc:
                    if not self._stop.is_set():
                        self._emit(("error", str(exc)))
                finally:
                    was_connected = self.connected
                    self.connected = False
                    with self._lock:
                        self._peer = None
                    if was_connected:
                        self._emit(("disconnected", self._epoch))
        self.running = False

    def _serve(self, channel, epoch):
        sent = None
        last_ping = 0.0
        while not self._stop.is_set():
            with self._lock:
                state = dict(self._state)
            if state != sent:
                channel.send("state", state)
                sent = state
            now = time.monotonic()
            if now - last_ping >= HEARTBEAT_INTERVAL:
                channel.send("ping")
                last_ping = now
            incoming = channel.read(0.04)
            if incoming:
                kind, body = incoming
                if kind == "key":
                    index = body.get("key")
                    pressed = body.get("pressed")
                    arm = body.get("arm")
                    if (type(index) is not int or not 0 <= index < 12 or
                            type(pressed) is not bool or
                            (arm is not None and (not isinstance(arm, str) or
                                                  len(arm) > 80))):
                        raise ProtocolError("Invalid key event.")
                    self._emit(("key", epoch, index, pressed, arm))
                elif kind == "snapshot":
                    keys = body.get("held")
                    if (not isinstance(keys, list) or len(keys) > 12 or
                            any(type(k) is not int or not 0 <= k < 12 for k in keys)
                            or len(set(keys)) != len(keys)):
                        raise ProtocolError("Invalid key snapshot.")
                    self._emit(("snapshot", epoch, keys))
                elif kind == "ping":
                    channel.send("pong")
                elif kind != "pong":
                    raise ProtocolError("Unsupported message kind.")
            if channel.expired():
                raise TimeoutError("Pi heartbeat timed out.")

    def publish(self, phase, arm=None, cue=None, cue_id=0):
        if phase not in ("idle", "working", "approval", "completed",
                         "failed", "cancelled", "offline"):
            raise ValueError("Invalid LED phase.")
        if arm is not None and (not isinstance(arm, str) or len(arm) > 80):
            raise ValueError("Invalid arm token.")
        if cue is not None and cue not in (
                "accepted", "declined", "cancelled", "completed", "failed",
                "connected"):
            raise ValueError("Invalid LED cue.")
        with self._lock:
            self._state = {"phase": phase, "arm": arm,
                           "cue": cue, "cue_id": cue_id}

    def close(self):
        self._stop.set()
        listener = self._listener
        self._listener = None
        if listener:
            listener.close()
        with self._lock:
            peer = self._peer
        if peer:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.connected = False
        self.running = False
