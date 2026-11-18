import unittest

from keybow_net.effects import LightEngine, KEYS


class EffectsTests(unittest.TestCase):
    def test_all_phases_have_bounded_rgb_frames(self):
        e = LightEngine()
        for phase in ("idle", "working", "approval", "completed",
                      "failed", "cancelled", "offline"):
            e.set_phase(phase, 1)
            for at in (1, 1.35, 3):
                frame = e.frame(at)
                self.assertEqual(len(frame), KEYS)
                self.assertTrue(all(len(pixel) == 3 and
                                    all(0 <= v <= 255 for v in pixel)
                                    for pixel in frame))

    def test_simultaneous_holds_and_independent_releases(self):
        e = LightEngine()
        e.set_phase("approval", 1)
        self.assertTrue(e.key(0, True, 1.1))
        self.assertTrue(e.key(5, True, 1.11))
        self.assertFalse(e.key(5, True, 1.12))
        self.assertEqual(e.held, {0, 5})
        lit = e.frame(1.2)
        self.assertGreater(lit[0][2], lit[1][2])
        self.assertGreater(lit[5][2], lit[6][2])
        self.assertTrue(e.key(0, False, 1.3))
        self.assertEqual(e.held, {5})
        self.assertEqual(len(e.frame(1.4)), 12)
        self.assertTrue(e.key(5, False, 1.5))
        self.assertFalse(e.held)

    def test_cue_and_success_animation_evolve(self):
        e = LightEngine()
        e.set_phase("working", 0)
        e.trigger("accepted", 0.5)
        self.assertNotEqual(e.frame(.6), e.frame(2))
        e.set_phase("completed", 3)
        self.assertNotEqual(e.frame(3.15), e.frame(5))

    def test_reject_bad_keys_and_unknown_effect(self):
        e = LightEngine()
        self.assertFalse(e.key(12, True, 0))
        self.assertFalse(e.key(-1, True, 0))
        self.assertFalse(e.trigger("unsupported", 0))
        with self.assertRaises(ValueError):
            e.set_phase("unknown", 0)


if __name__ == "__main__":
    unittest.main()
