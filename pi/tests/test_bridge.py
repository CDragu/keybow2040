"""Simulated original 12-key Pi hardware, no GPIO/network dependencies."""
import queue
import unittest

from pi.bridge import PiBridge
from pi.service import MockHardware


class Clock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value

    def advance(self, delta=0.01):
        self.value += delta


class FakeClient:
    def __init__(self):
        self.events = queue.Queue()
        self.epoch = 1
        self.connected = True
        self.sent = []
        self.held = None
        self.closed = False

    def set_held(self, held):
        self.held = set(held)

    def send_key(self, index, pressed, arm, epoch):
        self.sent.append((index, pressed, arm, epoch))
        return True

    def close(self):
        self.closed = True


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hw = MockHardware()
        self.client = FakeClient()
        self.bridge = PiBridge(self.hw, self.client, clock=self.clock)
        self.client.events.put(("connected", 1))
        self.bridge.tick()

    def arm(self, token="abc", cue_id=1):
        self.client.events.put(("state", 1, {
            "phase": "approval", "arm": token, "cue": None, "cue_id": cue_id}))
        self.bridge.tick()

    def test_multikey_press_release_preserves_each_edge(self):
        self.arm()
        self.clock.advance()
        self.hw.emit(0, True)
        self.hw.emit(1, True)
        self.hw.emit(5, True)
        self.bridge.tick()
        self.assertEqual(self.bridge.held, {0, 1, 5})
        self.assertEqual(self.client.sent[:3], [
            (0, True, "abc", 1), (1, True, "abc", 1), (5, True, "abc", 1)])
        self.clock.advance()
        self.hw.emit(1, False)
        self.hw.emit(0, False)
        self.bridge.tick()
        self.assertEqual(self.bridge.held, {5})
        self.assertEqual(self.client.sent[-2:], [
            (1, False, None, 1), (0, False, None, 1)])
        self.assertEqual(self.client.held, {5})
        self.assertEqual(len(self.hw.leds), 12)
        self.assertGreaterEqual(len(self.hw.frames), 1)

    def test_old_press_does_not_gain_new_arm_token(self):
        self.clock.advance()
        self.hw.emit(0, True)
        self.clock.advance()
        self.arm()
        self.bridge.tick()
        self.assertEqual(self.client.sent[-1], (0, True, None, 1))
        self.assertNotIn(0, self.bridge.armed_ready)
        self.clock.advance()
        self.hw.emit(0, False)
        self.bridge.tick()
        self.assertIn(0, self.bridge.armed_ready)
        self.clock.advance()
        self.hw.emit(0, True)
        self.bridge.tick()
        self.assertEqual(self.client.sent[-1], (0, True, "abc", 1))

    def test_pressed_before_state_delivery_not_armed(self):
        self.clock.advance()
        self.hw.emit(2, True)
        self.clock.advance()
        self.arm("new")
        self.assertEqual(self.client.sent[-1], (2, True, None, 1))

    def test_rearm_requires_new_press_and_disconnect_fails_closed(self):
        self.arm("one")
        self.clock.advance()
        self.hw.emit(0, True)
        self.bridge.tick()
        self.arm("two", 2)
        self.assertEqual(self.bridge.arm, "two")
        self.clock.advance()
        self.hw.emit(0, False)
        self.bridge.tick()
        self.clock.advance()
        self.hw.emit(0, True)
        self.bridge.tick()
        self.assertEqual(self.client.sent[-1][2], "two")
        self.client.events.put(("disconnected", 1))
        self.bridge.tick()
        self.assertEqual(self.bridge.effects.phase, "offline")
        self.assertIsNone(self.bridge.arm)
        self.clock.advance()
        self.hw.emit(0, False)
        self.bridge.tick()
        self.clock.advance()
        self.hw.emit(0, True)
        self.bridge.tick()
        self.assertEqual(self.client.sent[-1][-2], None)

    def test_led_and_event_cues(self):
        self.client.events.put(("state", 1, {
            "phase": "completed", "arm": None,
            "cue": "accepted", "cue_id": 5}))
        self.bridge.tick()
        self.assertEqual(self.bridge.effects.phase, "completed")
        self.assertEqual(self.bridge.effects.cue, "accepted")
        self.assertEqual(self.bridge.last_cue_id, 5)

    def tearDown(self):
        self.bridge.close()
        self.assertTrue(self.hw.closed)
        self.assertTrue(self.client.closed)


if __name__ == "__main__":
    unittest.main()
