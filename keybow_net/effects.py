"""Deterministic, composable LED animations shared by physical/virtual Keybow.

No I/O or hardware imports; every output pixel is a bounded (R, G, B) tuple.
"""
import math

KEYS = 12
KEY_LOCATIONS = {i: (3 - i // 3, i % 3) for i in range(KEYS)}
PALETTES = {
    "idle": (9, 30, 76),
    "working": (170, 92, 7),
    "approval": (23, 9, 17),
    "completed": (11, 125, 61),
    "failed": (127, 13, 24),
    "cancelled": (127, 62, 10),
    "offline": (43, 21, 3),
}
CUES = {"accepted", "declined", "cancelled", "completed", "failed", "connected"}


def clamp(color):
    return tuple(max(0, min(255, int(v))) for v in color)


def blend(a, b, strength):
    strength = max(0.0, min(1.0, strength))
    return clamp(tuple(a[i] * (1 - strength) + b[i] * strength for i in range(3)))


class LightEngine:
    def __init__(self, now=0.0):
        self.phase = "offline"
        self.phase_since = now
        self.held = set()
        self.ripples = {}   # index -> timestamp; simultaneous presses coexist
        self.cue = None
        self.cue_since = now

    def set_phase(self, phase, now):
        if phase not in PALETTES:
            raise ValueError("Unknown LED phase.")
        if phase != self.phase:
            self.phase = phase
            self.phase_since = now
            if phase in ("completed", "failed", "cancelled"):
                self.trigger(phase, now)

    def key(self, index, pressed, now):
        if type(index) is not int or not 0 <= index < KEYS:
            return False
        if pressed == (index in self.held):
            return False
        if pressed:
            self.held.add(index)
            self.ripples[index] = now
        else:
            self.held.remove(index)
        return True

    def trigger(self, cue, now):
        if cue not in CUES:
            return False
        self.cue = cue
        self.cue_since = now
        return True

    def reset_keys(self):
        self.held.clear()
        self.ripples.clear()

    def frame(self, now):
        base = PALETTES[self.phase]
        age = max(0, now - self.phase_since)
        pixels = []
        for i in range(KEYS):
            row, col = KEY_LOCATIONS[i]
            color = base
            if self.phase in ("working", "offline"):
                rate = 5.0 if self.phase == "working" else 1.6
                intensity = (0.42 + 0.58 * (0.5 + 0.5 *
                             math.sin(rate * now - col * 0.8)))
                color = clamp(tuple(v * intensity for v in base))
            elif self.phase == "approval" and i in (0, 1, 2, 3):
                pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(7 * now - col))
                color = clamp((225 * pulse, 27 * pulse, 42 * pulse))
            elif self.phase == "completed" and age < 1.4:
                # Green success wave, bottom-left to top-right.
                distance = row + col
                glow = max(0, 1 - abs((age / 1.4) * 7 - distance) / 1.5)
                color = blend(color, (110, 255, 135), glow)
            elif self.phase == "failed":
                if age < 1.2 and int(age / 0.15) % 2 == 0:
                    color = blend(color, (255, 90, 90), 0.83)

            for origin, started in tuple(self.ripples.items()):
                dt = now - started
                if dt > 0.65:
                    del self.ripples[origin]
                    continue
                if dt < 0:
                    continue
                r, c = KEY_LOCATIONS[origin]
                distance = abs(r - row) + abs(c - col)
                wave = max(0.0, 1.0 - abs(dt * 9 - distance) / 1.4)
                wave *= max(0, 1 - dt / 0.65) * 0.82
                color = blend(color, (42, 190, 255), wave)

            cue_age = now - self.cue_since
            if self.cue and 0 <= cue_age < 0.8:
                if self.cue in ("accepted", "connected", "completed"):
                    cue_color = (30, 240, 130)
                elif self.cue == "declined" or self.cue == "failed":
                    cue_color = (255, 35, 60)
                else:
                    cue_color = (255, 125, 20)
                # Brief, moving effect doesn't hide all base LED information.
                column_wave = max(0, 1 - abs(cue_age * 7 - col) / 1.1)
                color = blend(color, cue_color, column_wave * (1 - cue_age) * 0.78)

            if i in self.held:
                color = blend(color, (170, 240, 255), 0.85)
            pixels.append(clamp(color))
        return tuple(pixels)
