"""Pendulum-chain physics for hair, tail and tassels.

Each physics group is a small chain of particles hanging from a root, simulated
with Verlet integration (position-based: velocity is implied by the last two
positions, which makes damping and length constraints simple and stable).

Inputs move the root sideways ("x" inputs, e.g. head turn or body sway) or
tilt the chain's reference frame ("angle" inputs, e.g. head roll, so hair keeps
hanging down while the head tilts). Outputs are the bend angle of each segment
relative to the one before it, in degrees, written into rig parameters that a
BendDeformer consumes.

Everything is in abstract units: segment length 1, gravity in units/s^2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .deformers import rotate


def signed_angle(a: np.ndarray, b: np.ndarray) -> float:
    """Angle in degrees that rotates vector a onto b (screen CCW positive)."""
    cross = a[1] * b[0] - a[0] * b[1]
    dot = a[0] * b[0] + a[1] * b[1]
    return math.degrees(math.atan2(cross, dot))


@dataclass
class PhysicsInput:
    param: str
    kind: str  # "x" (moves the root sideways) or "angle" (tilts the frame, degrees)
    weight: float


@dataclass
class PhysicsOutput:
    param: str
    segment: int
    scale: float = 1.0  # parameter units per degree


@dataclass
class PendulumChain:
    id: str
    inputs: list[PhysicsInput]
    outputs: list[PhysicsOutput]
    segments: int = 2
    gravity: float = 30.0
    damping: float = 0.12  # fraction of velocity removed per 1/60 s
    stiffness: float = 0.0  # extra pull of each segment back toward straight
    max_angle: float = 45.0
    substep: float = 1.0 / 120.0
    pos: np.ndarray = field(init=False)
    prev: np.ndarray = field(init=False)

    def __post_init__(self):
        self.reset()

    def reset(self):
        self.pos = np.array([[0.0, float(i)] for i in range(self.segments + 1)])
        self.prev = self.pos.copy()

    def _drive(self, values):
        x = sum(i.weight * values.get(i.param, 0.0) for i in self.inputs if i.kind == "x")
        tilt = sum(i.weight * values.get(i.param, 0.0) for i in self.inputs if i.kind == "angle")
        return x, tilt

    def step(self, values: dict[str, float], dt: float) -> dict[str, float]:
        root_x, tilt = self._drive(values)
        # Gravity in the chain's frame: the frame is tilted by `tilt`, so gravity tilts the other way.
        g = rotate(np.array([0.0, self.gravity]), -tilt)
        rest_down = np.array([0.0, 1.0])  # the strip's rest direction in the head's frame
        n_sub = max(1, int(math.ceil(min(dt, 0.1) / self.substep)))
        h = min(dt, 0.1) / n_sub
        keep = (1.0 - self.damping) ** (h * 60.0)
        for _ in range(n_sub):
            self.pos[0] = (root_x, 0.0)
            self.prev[0] = self.pos[0]
            vel = (self.pos[1:] - self.prev[1:]) * keep
            self.prev[1:] = self.pos[1:]
            self.pos[1:] = self.pos[1:] + vel + g * h * h
            if self.stiffness:
                # pull each particle toward "straight continuation" of its parent segment
                for i in range(1, self.segments + 1):
                    ref_dir = rest_down if i == 1 else self.pos[i - 1] - self.pos[i - 2]
                    ref_dir = ref_dir / (np.linalg.norm(ref_dir) + 1e-9)
                    target = self.pos[i - 1] + ref_dir
                    self.pos[i] += (target - self.pos[i]) * min(1.0, self.stiffness * h * 60.0)
            for _ in range(2):  # keep segment lengths at 1
                for i in range(1, self.segments + 1):
                    v = self.pos[i] - self.pos[i - 1]
                    self.pos[i] = self.pos[i - 1] + v / (np.linalg.norm(v) + 1e-9)
        return self.angles()

    def angles(self) -> dict[str, float]:
        """Bend of each segment relative to the previous one; the first is relative to
        the rest direction (straight down in the head's frame). When the head rolls by
        +a, hair hanging straight in the world is at -a in the head's frame, so the
        output cancels the roll and the hair keeps hanging down."""
        seg_angles = []
        ref = np.array([0.0, 1.0])
        for i in range(1, self.segments + 1):
            v = self.pos[i] - self.pos[i - 1]
            a = signed_angle(ref, v)
            seg_angles.append(float(np.clip(a, -self.max_angle, self.max_angle)))
            ref = v
        return {o.param: seg_angles[o.segment] * o.scale for o in self.outputs}
