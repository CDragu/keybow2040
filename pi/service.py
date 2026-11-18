#!/usr/bin/env python3
"""Original 12-key Keybow diagnostic service for Raspberry Pi OS.

No Windows/Codex approvals are performed by this diagnostic program.
Run from the repository root: python -m pi.service --backend mock --demo
"""

import argparse
import logging
import queue
import signal
import threading
import time
from dataclasses import dataclass
from typing import Callable

KEY_COUNT = 12
# Physical numbering: 9 10 11 / 6 7 8 / 3 4 5 / 0 1 2.
BASE_COLORS = ((12, 0, 0), (0, 12, 0), (0, 0, 12))
PRESSED_COLOR = (0, 160, 30)
OFF = (0, 0, 0)
LOG = logging.getLogger("keybow.pi")


@dataclass(frozen=True)
class KeyEvent:
    index: int
    pressed: bool


class MockHardware:
    """Dependency-free hardware simulator used by tests and desktop development."""

    def __init__(self):
        self.leds = [OFF] * KEY_COUNT
        self.frames = []
        self.handler = None
        self.closed = False

    def on_key(self, callback: Callable[[int, bool], None]):
        self.handler = callback

    def emit(self, index: int, pressed: bool):
        if self.handler is None:
            raise RuntimeError("no key listener registered")
        self.handler(index, pressed)

    def set_led(self, index: int, rgb):
        self.leds[index] = rgb

    def show(self):
        self.frames.append(tuple(self.leds))

    def close(self):
        self.leds = [OFF] * KEY_COUNT
        self.show()
        self.closed = True


class PimoroniHardware:
    """Adapter for pimoroni/keybow-python on the original 12-key board.

    The upstream library owns BCM key pins, SPI APA102 LEDs, and debouncing.
    Do NOT use this with Keybow 2040, which is different hardware.
    """

    def __init__(self):
        try:
            import keybow  # lazy import: tests run on any OS
        except ImportError as exc:
            raise RuntimeError(
                "Install the original Keybow library on the Pi; see pi/README.md"
            ) from exc
        self.device = keybow

    def on_key(self, callback: Callable[[int, bool], None]):
        self.device.on(handler=callback)

    def set_led(self, index: int, rgb):
        self.device.set_led(index, *rgb)

    def show(self):
        self.device.show()

    def close(self):
        self.device.clear()
        self.device.show()


class DiagnosticController:
    """All LED state is owned by the main loop, never by GPIO callbacks."""

    def __init__(self, hardware, log=LOG):
        self.hardware = hardware
        self.log = log
        self.events = queue.SimpleQueue()
        self.pressed = set()
        self.hardware.on_key(self._enqueue)

    def _enqueue(self, index, pressed):
        # GPIO edge callbacks may run on a separate thread.
        self.events.put(KeyEvent(index, bool(pressed)))

    def show_idle(self):
        for index in range(KEY_COUNT):
            self.hardware.set_led(index, BASE_COLORS[index % 3])
        self.hardware.show()

    def led_self_test(self, sleep=time.sleep, interval=0.3):
        """Prove that all 12 LEDs can display each RGB channel."""
        for color in ((80, 0, 0), (0, 80, 0), (0, 0, 80)):
            for index in range(KEY_COUNT):
                self.hardware.set_led(index, color)
            self.hardware.show()
            sleep(interval)
        self.show_idle()

    def process_events(self):
        changed = False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if not 0 <= event.index < KEY_COUNT:
                self.log.warning("ignoring unexpected key index %r", event.index)
                continue
            if (event.index in self.pressed) == event.pressed:
                continue  # duplicated edge
            if event.pressed:
                self.pressed.add(event.index)
            else:
                self.pressed.remove(event.index)
            self.hardware.set_led(event.index, PRESSED_COLOR if event.pressed
                                  else BASE_COLORS[event.index % 3])
            self.log.info("key %02d %s", event.index,
                          "PRESSED" if event.pressed else "RELEASED")
            changed = True
        if changed:
            self.hardware.show()
        return changed


def run(hardware, *, self_test=True, duration=None, demo=False,
        sleep=time.sleep, clock=time.monotonic, stop=None):
    """Run the diagnostic loop; hardware is always cleared on exit."""
    stop = stop or threading.Event()
    controller = DiagnosticController(hardware)
    try:
        if self_test:
            controller.led_self_test(sleep=sleep)
        else:
            controller.show_idle()
        if demo:
            if not isinstance(hardware, MockHardware):
                raise ValueError("--demo requires --backend mock")
            for index in range(KEY_COUNT):
                hardware.emit(index, True)
                controller.process_events()
                sleep(0.03)
                hardware.emit(index, False)
                controller.process_events()
        started = clock()
        while not stop.is_set() and (duration is None or clock() - started < duration):
            controller.process_events()
            sleep(0.03)
    finally:
        hardware.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("real", "mock"), default="real")
    parser.add_argument("--demo", action="store_true",
                        help="generate 12 key presses when using mock backend")
    parser.add_argument("--skip-led-test", action="store_true")
    parser.add_argument("--duration", type=float, default=None,
                        help="optional run length in seconds, useful for smoke tests")
    args = parser.parse_args(argv)
    if args.duration is not None and args.duration < 0:
        parser.error("--duration must be >= 0")
    if args.demo and args.backend != "mock":
        parser.error("--demo requires --backend mock")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _sig, _frame: stop.set())
    hardware = PimoroniHardware() if args.backend == "real" else MockHardware()
    LOG.info("starting 12-key diagnostic, backend=%s", args.backend)
    run(hardware, self_test=not args.skip_led_test, duration=args.duration,
        demo=args.demo, stop=stop)
    LOG.info("diagnostic stopped; LEDs cleared")


if __name__ == "__main__":
    main()
