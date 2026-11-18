#!/usr/bin/env python3
"""Original Pi Zero Keybow bridge: 12 concurrent keys, RGB effects, Wi-Fi TCP.

Run from repository root: python -m pi.bridge --config ~/.keybow2040/pi.json
No Codex commands, approval decisions, or user prompts are executed on Pi.
"""
import argparse
import logging
import queue
import signal
import socket
import threading
import time

from keybow_net.effects import LightEngine, KEYS, PALETTES
from keybow_net.pairing import PI_CONFIG, load_config
from keybow_net.protocol import (
    HEARTBEAT_INTERVAL, ProtocolError, client_handshake
)
from pi.service import MockHardware, PimoroniHardware

LOG = logging.getLogger("keybow.pi.bridge")


class PiClient:
    """Background reconnecting network worker; Pi GPIO/LED stay on main thread."""

    def __init__(self, host, port, secret):
        self.host = host
        self.port = port
        self.secret = secret
        self.events = queue.Queue(maxsize=512)
        self.outgoing = queue.Queue(maxsize=2048)
        self.stop = threading.Event()
        self.epoch = 0
        self.connected = False
        self.thread = None
        self.held = set()
        self.held_lock = threading.Lock()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="keybow-pi-link")
        self.thread.start()

    def set_held(self, held):
        with self.held_lock:
            self.held = set(held)

    def send_key(self, index, pressed, arm, epoch):
        if not self.connected or epoch != self.epoch:
            return False
        try:
            self.outgoing.put_nowait((epoch, {"key": index, "pressed": pressed,
                                               "arm": arm}))
            return True
        except queue.Full:
            # Never drop a release without disconnecting; stop and re-pair.
            self.stop.set()
            return False

    def _emit(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.stop.set()

    def _run(self):
        while not self.stop.is_set():
            connected_epoch = None
            try:
                with socket.create_connection((self.host, self.port),
                                              timeout=3.0) as sock:
                    sock.settimeout(3.0)
                    channel = client_handshake(sock, self.secret)
                    self.epoch += 1
                    connected_epoch = self.epoch
                    self.connected = True
                    with self.held_lock:
                        initial_held = sorted(self.held)
                    channel.send("snapshot", {"held": initial_held})
                    self._emit(("connected", connected_epoch))
                    last_ping = 0.0
                    while not self.stop.is_set():
                        for _ in range(80):
                            try:
                                epoch, body = self.outgoing.get_nowait()
                            except queue.Empty:
                                break
                            if epoch == connected_epoch:
                                channel.send("key", body)
                        now = time.monotonic()
                        if now - last_ping >= HEARTBEAT_INTERVAL:
                            channel.send("ping")
                            last_ping = now
                        msg = channel.read(0.035)
                        if msg:
                            kind, body = msg
                            if kind == "state":
                                self._validate_state(body)
                                self._emit(("state", connected_epoch, body))
                            elif kind == "ping":
                                channel.send("pong")
                            elif kind != "pong":
                                raise ProtocolError("Unsupported host message.")
                        if channel.expired():
                            raise TimeoutError("Windows link heartbeat timed out.")
            except (OSError, TimeoutError, ConnectionError, ProtocolError) as exc:
                if connected_epoch is not None:
                    LOG.warning("Disconnected from Windows: %s", exc)
                else:
                    LOG.debug("Windows not available: %s", exc)
            finally:
                self.connected = False
                if connected_epoch is not None:
                    self._emit(("disconnected", connected_epoch))
                # Discard keys from the old session; never replay on reconnect.
                while True:
                    try:
                        self.outgoing.get_nowait()
                    except queue.Empty:
                        break
            if self.stop.wait(1.0):
                break

    @staticmethod
    def _validate_state(body):
        if (body.get("phase") not in PALETTES or
                (body.get("arm") is not None and
                 (not isinstance(body.get("arm"), str) or len(body["arm"]) > 80)) or
                body.get("cue") not in (
                    None, "accepted", "declined", "cancelled",
                    "completed", "failed", "connected") or
                type(body.get("cue_id")) is not int):
            raise ProtocolError("Invalid desktop LED state.")

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3.0)


class PiBridge:
    """Owns all hardware state; GPIO callback ONLY enqueues timestamped edges."""

    def __init__(self, hardware, client, clock=time.monotonic):
        self.hw = hardware
        self.client = client
        self.clock = clock
        self.edges = queue.SimpleQueue()
        self.held = set()
        self.armed_ready = set()
        self.arm = None
        self.arm_since = float("inf")
        self.active_epoch = 0
        self.last_cue_id = None
        self.effects = LightEngine(self.clock())
        self.last_frame = None
        self.hw.on_key(self._on_key)
        self.client.set_held(self.held)

    def _on_key(self, index, pressed):
        # GPIO interrupt callback can arrive on a separate native thread.
        self.edges.put((index, bool(pressed), self.clock()))

    def _network_event(self, event):
        kind, epoch, *data = event
        now = self.clock()
        if kind == "connected":
            self.active_epoch = epoch
            self.arm = None
            self.arm_since = float("inf")
            self.armed_ready.clear()
            self.last_cue_id = None
            self.effects.set_phase("idle", now)
            self.effects.trigger("connected", now)
        elif epoch == self.active_epoch and kind == "disconnected":
            self.active_epoch = 0
            self.arm = None
            self.arm_since = float("inf")
            self.armed_ready.clear()
            self.effects.set_phase("offline", now)
        elif epoch == self.active_epoch and kind == "state":
            state = data[0]
            self.effects.set_phase(state["phase"], now)
            new_arm = state.get("arm") if state["phase"] == "approval" else None
            if new_arm != self.arm:
                self.arm = new_arm
                self.arm_since = now
                # Keys held before arming must be released and pressed again.
                self.armed_ready = set(range(KEYS)) - self.held if new_arm else set()
            cue_id = state.get("cue_id")
            if cue_id != self.last_cue_id:
                self.last_cue_id = cue_id
                if state.get("cue"):
                    self.effects.trigger(state["cue"], now)

    def tick(self, max_events=256):
        # Apply network arm state first; timestamps reject earlier physical edges.
        for _ in range(max_events):
            try:
                event = self.client.events.get_nowait()
            except queue.Empty:
                break
            self._network_event(event)

        for _ in range(max_events):
            try:
                index, pressed, when = self.edges.get_nowait()
            except queue.Empty:
                break
            if type(index) is not int or not 0 <= index < KEYS:
                LOG.warning("Ignored key outside original Keybow range: %r", index)
                continue
            if not self.effects.key(index, pressed, when):
                continue
            if pressed:
                self.held.add(index)
                valid_arm = (self.arm is not None and when >= self.arm_since
                             and index in self.armed_ready)
                if valid_arm:
                    self.armed_ready.discard(index)
                tag = self.arm if valid_arm else None
            else:
                self.held.discard(index)
                if self.arm is not None and when >= self.arm_since:
                    self.armed_ready.add(index)
                tag = None
            self.client.set_held(self.held)
            if self.active_epoch:
                self.client.send_key(index, pressed, tag, self.active_epoch)
        self.paint()

    def paint(self):
        frame = self.effects.frame(self.clock())
        if frame != self.last_frame:
            for index, color in enumerate(frame):
                self.hw.set_led(index, color)
            self.hw.show()
            self.last_frame = frame

    def close(self):
        self.client.close()
        self.hw.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PI_CONFIG))
    parser.add_argument("--backend", choices=("real", "mock"), default="real")
    parser.add_argument("--duration", type=float, default=None,
                        help="optional finite diagnostic runtime, seconds")
    args = parser.parse_args(argv)
    if args.duration is not None and args.duration < 0:
        parser.error("--duration must be nonnegative.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    host, port, secret = load_config(args.config)
    hw = PimoroniHardware() if args.backend == "real" else MockHardware()
    client = PiClient(host, port, secret)
    bridge = PiBridge(hw, client)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _sig, _frame: stop.set())
    started = time.monotonic()
    try:
        client.start()
        while not stop.is_set() and (
                args.duration is None or time.monotonic() - started < args.duration):
            bridge.tick()
            time.sleep(1 / 30)
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
