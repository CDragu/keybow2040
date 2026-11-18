"""Hardware-free checks for Pi diagnostics."""
import logging
import unittest
from unittest.mock import Mock

from pi.service import (BASE_COLORS, KEY_COUNT, OFF, PRESSED_COLOR,
                        DiagnosticController, MockHardware, run)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.hardware = MockHardware()
        self.log = Mock(spec=logging.Logger)
        self.controller = DiagnosticController(self.hardware, log=self.log)

    def test_idle_map_covers_twelve_keys(self):
        self.controller.show_idle()
        self.assertEqual(len(self.hardware.leds), KEY_COUNT)
        self.assertEqual(self.hardware.leds[0], BASE_COLORS[0])
        self.assertEqual(self.hardware.leds[11], BASE_COLORS[2])

    def test_press_release_and_duplicate_edges(self):
        self.controller.show_idle()
        self.hardware.emit(5, True)
        self.hardware.emit(5, True)
        self.assertTrue(self.controller.process_events())
        self.assertEqual(self.hardware.leds[5], PRESSED_COLOR)
        self.assertEqual(len(self.controller.pressed), 1)
        self.hardware.emit(5, False)
        self.assertTrue(self.controller.process_events())
        self.assertEqual(self.hardware.leds[5], BASE_COLORS[2])
        self.assertFalse(self.controller.pressed)
        self.assertFalse(self.controller.process_events())

    def test_invalid_index_does_not_access_hardware(self):
        self.hardware.emit(-1, True)
        self.hardware.emit(12, True)
        self.assertFalse(self.controller.process_events())
        self.assertEqual(self.hardware.leds, [OFF] * KEY_COUNT)
        self.assertEqual(self.log.warning.call_count, 2)

    def test_led_test_shows_rgb_in_order(self):
        self.controller.led_self_test(sleep=lambda _: None)
        self.assertEqual(len(self.hardware.frames), 4)
        self.assertEqual(self.hardware.frames[0], ((80, 0, 0),) * KEY_COUNT)
        self.assertEqual(self.hardware.frames[1], ((0, 80, 0),) * KEY_COUNT)
        self.assertEqual(self.hardware.frames[2], ((0, 0, 80),) * KEY_COUNT)
        self.assertEqual(self.hardware.frames[3][0], BASE_COLORS[0])

    def test_run_cleans_up_and_mock_demo_exercises_all_keys(self):
        run(self.hardware, demo=True, self_test=False, duration=0,
            sleep=lambda _: None)
        self.assertTrue(self.hardware.closed)
        self.assertEqual(self.hardware.leds, [OFF] * KEY_COUNT)
        self.assertEqual(len(self.hardware.frames), 26)  # idle, 24 events, off

    def test_cleanup_even_when_sleep_fails(self):
        def fail(_):
            raise RuntimeError("simulated I/O failure")
        with self.assertRaises(RuntimeError):
            run(self.hardware, sleep=fail, duration=0)
        self.assertTrue(self.hardware.closed)


if __name__ == "__main__":
    unittest.main()
