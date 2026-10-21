"""Head and eyes follow the mouse cursor when there is no face tracking."""

from __future__ import annotations

import math

from .idle import approach


class MouseFollow:
    def __init__(self, reach_px: float = 700.0, rate: float = 6.0):
        self.reach = reach_px  # cursor distance at which the head is ~76% turned
        self.rate = rate
        self.values = {"ParamAngleX": 0.0, "ParamAngleY": 0.0, "ParamEyeBallX": 0.0,
                       "ParamEyeBallY": 0.0, "ParamBodyAngleX": 0.0}

    def update(self, cursor: tuple[float, float], head: tuple[float, float], dt: float) -> dict[str, float]:
        dx = (cursor[0] - head[0]) / self.reach
        dy = (cursor[1] - head[1]) / self.reach
        target = {
            "ParamAngleX": 30 * math.tanh(dx),
            "ParamAngleY": -30 * math.tanh(dy),  # cursor above the head -> look up
            "ParamEyeBallX": math.tanh(2 * dx),
            "ParamEyeBallY": -math.tanh(2 * dy),
            "ParamBodyAngleX": 6 * math.tanh(dx),
        }
        for k, v in target.items():
            # eyes react faster than the head, the body slower
            rate = self.rate * (2.5 if "EyeBall" in k else 0.6 if "Body" in k else 1.0)
            self.values[k] = approach(self.values[k], v, rate, dt)
        return dict(self.values)
