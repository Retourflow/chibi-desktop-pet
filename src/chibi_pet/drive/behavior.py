"""Pet behaviour on top of idle / mouse / face tracking: click reactions, dozing, lip sync.

Each piece returns (parameter values, weight 0..1). The window blends them in
order, so a later layer wins where its weight is 1:

    idle + mouse follow -> face tracking -> dozing -> click reaction -> lip sync

Reactions
    head click ("pat")  : eyes closed contentedly, smile, head wobbles side to side
    body click ("poke") : surprised eyes and brows, the body leans away from the click
    waking up           : a small startle after a click wakes the dozing pet

Dozing
    after N minutes without mouse movement or clicks the eyes slowly close, the head
    droops and nods now and then, breathing gets slower. A click on the pet wakes it.

Lip sync (hook for TTS / the AI companion later)
    feed_level(loudness) or feed_vowel("a".."o"); the mouth follows and closes on its
    own shortly after the input stops.
"""

from __future__ import annotations

import math
import random

from .idle import approach

HEAD, BODY = "head", "body"


def classify_click(canvas_xy: tuple[float, float], face_rect: tuple[float, float, float, float]) -> tuple[str, int]:
    """Where a click landed: (HEAD or BODY, side). side is +1 right of the face centre, -1 left.

    Everything above the chin counts as head (hair, antlers, ears included).
    """
    x, y = canvas_xy
    fx, fy, fw, fh = face_rect
    side = 1 if x >= fx + fw / 2 else -1
    return (HEAD if y < fy + fh else BODY), side


def _envelope(t: float, duration: float, fade_in: float = 0.1, fade_out: float = 0.3) -> float:
    if t < 0 or t >= duration:
        return 0.0
    return min(1.0, t / fade_in, (duration - t) / fade_out)


class ReactionPlayer:
    DURATION = {"pat": 1.6, "poke": 1.0, "wake": 0.9}

    def __init__(self):
        self.kind: str | None = None
        self.side = 1
        self.t = 0.0

    @property
    def active(self) -> bool:
        return self.kind is not None

    def play(self, kind: str, side: int = 1) -> None:
        if kind == "pat" and self.kind == "pat" and self.t > 0.15:
            self.t = 0.15  # patting again keeps the happy face going without restarting the fade-in
        else:
            self.kind, self.t = kind, 0.0
        self.side = side

    def update(self, dt: float) -> tuple[dict[str, float], float]:
        if self.kind is None:
            return {}, 0.0
        self.t += dt
        t, kind = self.t, self.kind
        duration = self.DURATION[kind]
        if t >= duration:
            self.kind = None
            return {}, 0.0
        if kind == "pat":
            wobble = 8.0 * math.sin(2 * math.pi * 2.2 * t) * math.exp(-1.0 * t)
            values = {"ParamAngleZ": wobble, "ParamBodyAngleZ": wobble * 0.3, "ParamAngleY": -6.0,
                      "ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.0,  # closed, content (no smile-eye art yet)
                      "ParamMouthForm": 1.0, "ParamMouthOpenY": 0.15,
                      "ParamBrowLY": 0.4, "ParamBrowRY": 0.4}
        elif kind == "poke":
            s = self.side  # poked on the right -> lean away to the left (counter-clockwise = +)
            recoil = s * 7.0 * math.exp(-3.5 * t) * math.cos(2 * math.pi * 1.4 * t)
            values = {"ParamBodyAngleZ": recoil, "ParamAngleZ": recoil * 1.3,
                      "ParamAngleX": -s * 10.0 * math.exp(-2.5 * t), "ParamAngleY": 6.0 * math.exp(-3 * t),
                      "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0, "ParamEyeBallX": s * 0.6,
                      "ParamBrowLY": 1.0, "ParamBrowRY": 1.0,
                      "ParamMouthOpenY": 0.55 * math.exp(-2 * t), "ParamMouthForm": -0.6}
        else:  # wake
            values = {"ParamAngleY": 8.0 * math.exp(-3 * t), "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
                      "ParamBrowLY": 0.8, "ParamBrowRY": 0.8, "ParamMouthOpenY": 0.3 * math.exp(-3 * t)}
        return values, _envelope(t, duration, 0.08, 0.3)


class Dozer:
    AWAKE, ASLEEP = "awake", "asleep"

    def __init__(self, after_seconds: float = 180.0, rng: random.Random | None = None):
        self.after = after_seconds  # <= 0: never doze
        self.rng = rng or random.Random()
        self.state = self.AWAKE
        self.idle_t = 0.0
        self.t = 0.0
        self.weight = 0.0
        self.next_nod = 0.0

    @property
    def asleep(self) -> bool:
        return self.state == self.ASLEEP

    def activity(self) -> None:
        """Mouse moved or the user interacted. Resets the idle timer (does not wake a sleeping pet)."""
        self.idle_t = 0.0

    def wake(self) -> bool:
        """Wake up if asleep. Returns True if the pet was asleep."""
        self.idle_t = 0.0
        if self.state == self.ASLEEP:
            self.state = self.AWAKE
            return True
        return False

    def update(self, dt: float) -> tuple[dict[str, float], float]:
        self.t += dt
        self.idle_t += dt
        if self.state == self.AWAKE and self.after > 0 and self.idle_t >= self.after:
            self.state = self.ASLEEP
            self.next_nod = self.t + self.rng.uniform(4.0, 8.0)
        asleep = self.state == self.ASLEEP
        # eyes close slowly (~2.5 s) when falling asleep, open fast when woken
        self.weight = approach(self.weight, 1.0 if asleep else 0.0, 1.2 if asleep else 8.0, dt)
        if self.weight < 1e-3:
            self.weight = 0.0
            return {}, 0.0
        # a nod: the head dips further and comes back up, every 4-8 s
        nod = 0.0
        if asleep and self.t >= self.next_nod:
            p = self.t - self.next_nod
            nod = math.sin(math.pi * p / 1.2) if p < 1.2 else 0.0
            if p >= 1.2:
                self.next_nod = self.t + self.rng.uniform(4.0, 8.0)
        breath = 0.5 + 0.5 * math.sin(2 * math.pi * self.t / 5.0)
        values = {"ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.0, "ParamEyeBallY": -0.5,
                  "ParamAngleX": 0.0, "ParamAngleY": -14.0 - 8.0 * nod, "ParamAngleZ": 7.0,
                  "ParamBodyAngleX": 0.0, "ParamBodyAngleY": -2.0, "ParamBodyAngleZ": 2.0,
                  "ParamBrowLY": -0.2, "ParamBrowRY": -0.2,
                  "ParamMouthOpenY": 0.08, "ParamMouthForm": 0.0, "ParamBreath": breath}
        return values, self.weight


VOWEL_SHAPES = {  # vowel -> (mouth open, mouth form) until the art has real vowel keyforms
    "a": (1.0, 0.0), "i": (0.3, 1.0), "u": (0.35, -0.8), "e": (0.6, 0.6), "o": (0.8, -0.6),
}


class LipSync:
    """Hook for speech: call feed_level() with loudness 0..1 (e.g. TTS audio RMS) or
    feed_vowel() with a/i/u/e/o (e.g. from TTS phoneme timings), ~30+ times a second.

    It sets ParamMouthOpenY / ParamMouthForm (works with the current art) and
    ParamMouthA..O (for vowel keyforms once the redrawn art has them).
    """

    def __init__(self, hold: float = 0.15, rate: float = 25.0):
        self.hold = hold  # seconds the mouth keeps the last input before closing
        self.rate = rate
        self.since_input = float("inf")
        self.target = {"ParamMouthOpenY": 0.0, "ParamMouthForm": 0.0}
        self.values = {"ParamMouthOpenY": 0.0, "ParamMouthForm": 0.0}
        self.vowels = {v: 0.0 for v in "aiueo"}
        self.weight = 0.0

    def feed_level(self, level: float) -> None:
        level = min(max(level, 0.0), 1.0)
        self.target = {"ParamMouthOpenY": level, "ParamMouthForm": 0.2}
        self.vowels = {v: 0.0 for v in "aiueo"}
        self.since_input = 0.0

    def feed_vowel(self, vowel: str, strength: float = 1.0) -> None:
        vowel = vowel.lower()
        if vowel not in VOWEL_SHAPES:
            raise ValueError(f"unknown vowel {vowel!r}, use one of a i u e o")
        opening, form = VOWEL_SHAPES[vowel]
        self.target = {"ParamMouthOpenY": opening * strength, "ParamMouthForm": form}
        self.vowels = {v: (strength if v == vowel else 0.0) for v in "aiueo"}
        self.since_input = 0.0

    def update(self, dt: float) -> tuple[dict[str, float], float]:
        self.since_input += dt
        speaking = self.since_input < self.hold
        if not speaking:
            self.target = {"ParamMouthOpenY": 0.0, "ParamMouthForm": self.target["ParamMouthForm"]}
            self.vowels = {v: 0.0 for v in "aiueo"}
        for k, v in self.target.items():
            self.values[k] = approach(self.values[k], v, self.rate, dt)
        self.weight = approach(self.weight, 1.0 if speaking or self.values["ParamMouthOpenY"] > 0.02 else 0.0, 10.0, dt)
        if self.weight < 1e-3:
            self.weight = 0.0
            return {}, 0.0
        out = dict(self.values)
        out.update({f"ParamMouth{v.upper()}": w for v, w in self.vowels.items()})
        return out, self.weight
