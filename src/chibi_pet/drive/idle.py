"""Idle animation: breathing, random blinking, a slow sway for tail and hair."""

from __future__ import annotations

import math
import random


def approach(current: float, target: float, rate: float, dt: float) -> float:
    """Exponential smoothing: move `current` toward `target`, frame-rate independent.

    After 1/rate seconds about 63% of the gap is closed.
    """
    return target + (current - target) * math.exp(-rate * dt)


class Blinker:
    """Blinks every 2-6 s (sometimes twice in a row). Returns eye openness 0..1."""

    CLOSE, HOLD, OPEN = 0.07, 0.04, 0.10  # seconds

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.next_blink = self.rng.uniform(1.0, 4.0)
        self.phase_t = -1.0  # <0: not blinking
        self.t = 0.0

    def update(self, dt: float) -> float:
        self.t += dt
        if self.phase_t < 0 and self.t >= self.next_blink:
            self.phase_t = 0.0
        if self.phase_t < 0:
            return 1.0
        self.phase_t += dt
        p = self.phase_t
        if p < self.CLOSE:
            return 1.0 - p / self.CLOSE
        if p < self.CLOSE + self.HOLD:
            return 0.0
        if p < self.CLOSE + self.HOLD + self.OPEN:
            return (p - self.CLOSE - self.HOLD) / self.OPEN
        self.phase_t = -1.0
        double = self.rng.random() < 0.2
        self.next_blink = self.t + (self.rng.uniform(0.15, 0.3) if double else self.rng.uniform(2.0, 6.0))
        return 1.0


class IdleAnimator:
    def __init__(self, rng: random.Random | None = None):
        self.t = 0.0
        self.blinker = Blinker(rng)

    def update(self, dt: float) -> dict[str, float]:
        self.t += dt
        t = self.t
        eye = self.blinker.update(dt)
        return {
            "ParamBreath": 0.5 + 0.5 * math.sin(2 * math.pi * t / 3.5),
            "ParamEyeLOpen": eye,
            "ParamEyeROpen": eye,
            "ParamSway": 0.5 * math.sin(2 * math.pi * t / 2.3) + 0.2 * math.sin(2 * math.pi * t / 0.9),
            "ParamAngleZ": 3.0 * math.sin(2 * math.pi * t / 7.0),
            "ParamBodyAngleZ": 1.5 * math.sin(2 * math.pi * t / 5.0 + 1.0),
        }
