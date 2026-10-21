"""Deformers: functions that move a part's vertices according to parameters.

Deformers form a tree (like Live2D): a part's vertices first get the part's
own keyforms, then pass through its deformer, then that deformer's parent,
and so on up to the root. Each deformer works in canvas pixel coordinates.

Angle convention (used everywhere in this package): a positive angle rotates
counter-clockwise *as seen on screen* (y axis points down).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


def rotate(v: np.ndarray, deg: float | np.ndarray) -> np.ndarray:
    """Rotate 2D vectors (..., 2) counter-clockwise on screen by `deg` degrees."""
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    x, y = v[..., 0], v[..., 1]
    return np.stack([x * c + y * s, -x * s + y * c], axis=-1)


def linear(coeffs: dict[str, float], values: dict[str, float]) -> float:
    """Sum of coefficient * parameter value, e.g. angle = 0.5 * BodyAngleZ."""
    return sum(c * values.get(p, 0.0) for p, c in coeffs.items())


@dataclass
class Deformer:
    id: str
    parent: str | None

    def apply(self, pts: np.ndarray, z: np.ndarray, values: dict[str, float]) -> np.ndarray:
        raise NotImplementedError


@dataclass
class RotationDeformer(Deformer):
    """Rotate / scale / translate around an origin. Used for body sway, head tilt, breathing."""

    origin: np.ndarray = field(default_factory=lambda: np.zeros(2))
    angle: dict[str, float] = field(default_factory=dict)  # degrees per param unit
    translate: dict[str, np.ndarray] = field(default_factory=dict)  # pixels per param unit
    scale: dict[str, np.ndarray] = field(default_factory=dict)  # (sx, sy) added per param unit

    def apply(self, pts, z, values):
        s = np.ones(2)
        for p, coeff in self.scale.items():
            s = s + coeff * values.get(p, 0.0)
        t = np.zeros(2)
        for p, coeff in self.translate.items():
            t = t + coeff * values.get(p, 0.0)
        local = (pts - self.origin) * s
        return self.origin + rotate(local, linear(self.angle, values)) + t


@dataclass
class Turn3DDeformer(Deformer):
    """Fake 3D head turn ("2.5D parallax").

    Every vertex carries a depth z (set per part: face on a sphere, front hair
    a bit in front, back hair behind). We rotate (x, y, z) around the head
    centre — yaw around the vertical axis, then pitch around the horizontal
    axis — and drop z again (orthographic projection). Points with larger z
    move further, which gives the parallax that sells a head turn.
    """

    center: np.ndarray = field(default_factory=lambda: np.zeros(2))
    yaw_param: str = "ParamAngleX"
    yaw_scale: float = 0.6  # real degrees per parameter unit
    pitch_param: str = "ParamAngleY"
    pitch_scale: float = 0.4

    def apply(self, pts, z, values):
        yaw = math.radians(values.get(self.yaw_param, 0.0) * self.yaw_scale)
        pitch = math.radians(values.get(self.pitch_param, 0.0) * self.pitch_scale)
        x = pts[:, 0] - self.center[0]
        y = pts[:, 1] - self.center[1]
        x1 = x * math.cos(yaw) + z * math.sin(yaw)
        z1 = -x * math.sin(yaw) + z * math.cos(yaw)
        y2 = y * math.cos(pitch) - z1 * math.sin(pitch)  # looking up moves the front up
        return np.stack([x1 + self.center[0], y2 + self.center[1]], axis=1)


@dataclass
class BendDeformer(Deformer):
    """Bend a strip (hair lock, tail) along a chain of segments.

    The rest chain is the straight line root -> tip, split into equal
    segments. Each segment has a bend angle driven by parameters (usually
    physics outputs). The cumulative angle grows smoothly along the chain and
    starts at 0 at the root, so the root stays attached while the tip swings.
    Vertices are expressed as (s along the chain, t sideways) and carried
    along the bent curve.
    """

    root: np.ndarray = field(default_factory=lambda: np.zeros(2))
    tip: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.0]))
    segments: list[dict[str, float]] = field(default_factory=list)  # angle coeffs per segment
    samples: int = 64

    def apply(self, pts, z, values):
        n = len(self.segments)
        if n == 0:
            return pts
        axis = self.tip - self.root
        length = float(np.hypot(*axis))
        d = axis / length
        nrm = np.array([d[1], -d[0]])  # d rotated by +90 degrees (screen CCW)
        rel = pts - self.root
        s = rel @ d
        t = rel @ nrm

        seg_angles = np.array([linear(c, values) for c in self.segments])
        if not np.any(seg_angles):
            return pts
        # cumulative angle at the joints: 0 at the root, sum of segments after each one
        joints_s = np.linspace(0.0, length, n + 1)
        joints_phi = np.concatenate([[0.0], np.cumsum(seg_angles)])

        # integrate the bent centre line
        ss = np.linspace(0.0, length, self.samples + 1)
        phi = np.interp(ss, joints_s, joints_phi)
        dirs = rotate(np.broadcast_to(d, (len(ss), 2)), phi)
        ds = length / self.samples
        mids = 0.5 * (dirs[1:] + dirs[:-1]) * ds
        curve = np.vstack([[0.0, 0.0], np.cumsum(mids, axis=0)])

        s_clamped = np.clip(s, 0.0, length)
        px = np.interp(s_clamped, ss, curve[:, 0])
        py = np.interp(s_clamped, ss, curve[:, 1])
        phi_v = np.interp(s_clamped, ss, phi)
        # beyond the tip: continue straight along the last direction
        extra = np.maximum(s - length, 0.0)
        # before the root (s < 0): unchanged, i.e. no rotation
        before = np.minimum(s, 0.0)
        tangent = rotate(np.broadcast_to(d, (len(s), 2)), phi_v)
        side = rotate(np.broadcast_to(nrm, (len(s), 2)), phi_v)
        out = (
            self.root
            + np.stack([px, py], 1)
            + tangent * (extra + before)[:, None]
            + side * t[:, None]
        )
        return out
