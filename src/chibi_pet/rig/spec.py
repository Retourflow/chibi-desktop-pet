"""Rig spec: the hand-written description of how the character moves.

The spec is JSON (see assets/rig/chibi.rig.json). Positions and lengths in it
are written *relative to parts* rather than in pixels, so the same spec works
for the rough placeholder art and for the final redrawn PSD:

  point:  {"part": "face", "at": [0.5, 1.0]}   -> bottom-centre of face's rect
          {"self": [0.5, 0.8]}                 -> relative to the part being set up
          [x, y]                               -> absolute canvas pixels
          {..., "fallback": <point>}           -> used if the referenced part is missing
  length: {"part": "face", "w": 0.5}           -> half the face's width
          {"part": "face", "h": 0.1} / {"self": {"w": 0.3}} / {"canvas_h": 0.01}
          123.0                                -> pixels
  vector: same as point but without the rect's origin, e.g.
          {"self": [0.1, 0]} -> 10% of own width to the right
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class Resolver:
    def __init__(self, rects: dict[str, tuple[int, int, int, int]], canvas: tuple[int, int]):
        self.rects = rects
        self.canvas = canvas

    def _rect(self, name: str):
        if name not in self.rects:
            raise KeyError(f"rig spec refers to part '{name}', which is not in the model")
        return self.rects[name]

    def point(self, spec, self_part: str | None = None) -> np.ndarray:
        if isinstance(spec, dict) and "fallback" in spec:
            try:
                return self.point({k: v for k, v in spec.items() if k != "fallback"}, self_part)
            except KeyError:
                return self.point(spec["fallback"], self_part)
        if isinstance(spec, (list, tuple)):
            return np.asarray(spec, dtype=np.float64)
        name, rel = self._which(spec, self_part, "at")
        x, y, w, h = self._rect(name)
        return np.array([x + rel[0] * w, y + rel[1] * h], dtype=np.float64)

    def vector(self, spec, self_part: str | None = None) -> np.ndarray:
        if isinstance(spec, (list, tuple)):
            return np.asarray(spec, dtype=np.float64)
        if "canvas_h" in spec:
            return np.asarray(spec["canvas_h"], dtype=np.float64) * self.canvas[1]
        name, rel = self._which(spec, self_part, "wh")
        _, _, w, h = self._rect(name)
        return np.array([rel[0] * w, rel[1] * h], dtype=np.float64)

    def length(self, spec, self_part: str | None = None) -> float:
        if isinstance(spec, dict) and "fallback" in spec:
            try:
                return self.length({k: v for k, v in spec.items() if k != "fallback"}, self_part)
            except KeyError:
                return self.length(spec["fallback"], self_part)
        if isinstance(spec, (int, float)):
            return float(spec)
        if "canvas_h" in spec:
            return float(spec["canvas_h"]) * self.canvas[1]
        if "self" in spec:
            name, sub = self_part, spec["self"]
        else:
            name, sub = spec["part"], spec
        _, _, w, h = self._rect(name)
        if "w" in sub:
            return float(sub["w"]) * w
        if "h" in sub:
            return float(sub["h"]) * h
        raise ValueError(f"bad length spec: {spec}")

    @staticmethod
    def _which(spec, self_part, key):
        if "self" in spec:
            if self_part is None:
                raise ValueError("'self' used outside a part")
            return self_part, spec["self"]
        return spec["part"], spec[key]


def load_spec(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
