"""Keyform interpolation — the core idea of Live2D.

A keyform is the shape (vertex offsets) and opacity of a part at specific
parameter values ("keys"). For any parameter value in between, we blend the
neighbouring keyforms linearly. With two parameters (e.g. EyeOpen x EyeSmile)
the keys form a grid and we blend the 4 surrounding corners (bilinear); in
general 2^d corners for d parameters (multilinear).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np


def key_weights(keys: np.ndarray, value: float) -> list[tuple[int, float]]:
    """Return [(index, weight), ...] (1 or 2 entries) for `value` between sorted `keys`.

    Values outside the key range are clamped to the first/last key.
    """
    if len(keys) == 1 or value <= keys[0]:
        return [(0, 1.0)]
    if value >= keys[-1]:
        return [(len(keys) - 1, 1.0)]
    hi = int(np.searchsorted(keys, value, side="right"))
    lo = hi - 1
    t = (value - keys[lo]) / (keys[hi] - keys[lo])
    return [(lo, 1.0 - t), (hi, t)]


@dataclass
class KeyformGroup:
    """Keyforms of one part over 1+ parameters.

    offsets: shape (K1, ..., Kd, N, 2) vertex offsets from the rest pose
    opacity: shape (K1, ..., Kd)
    """

    params: list[str]
    keys: list[np.ndarray]
    offsets: np.ndarray
    opacity: np.ndarray

    def __post_init__(self):
        shape = tuple(len(k) for k in self.keys)
        if self.offsets.shape[: len(shape)] != shape or self.opacity.shape != shape:
            raise ValueError(f"keyform arrays do not match key grid {shape}")
        for k in self.keys:
            if np.any(np.diff(k) <= 0):
                raise ValueError("keys must be strictly increasing")

    def evaluate(self, values: dict[str, float]) -> tuple[np.ndarray, float]:
        per_param = [key_weights(k, values.get(p, 0.0)) for p, k in zip(self.params, self.keys)]
        offset = np.zeros(self.offsets.shape[-2:])
        opacity = 0.0
        for combo in product(*per_param):
            idx = tuple(i for i, _ in combo)
            w = float(np.prod([wt for _, wt in combo]))
            if w == 0.0:
                continue
            offset += w * self.offsets[idx]
            opacity += w * float(self.opacity[idx])
        return offset, opacity
