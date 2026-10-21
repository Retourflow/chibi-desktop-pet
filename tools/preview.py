"""Render a scripted animation of a model to a GIF (CPU only, no window needed).

    python tools/preview.py <model_dir> <out.gif> [--rig assets/rig/chibi.rig.json]

The script exercises head turns, blinking, talking, body sway and the physics.
"""

from __future__ import annotations

import argparse
import math
import time

from PIL import Image

from chibi_pet.render.soft import load_atlas, render, to_image
from chibi_pet.rig.model import load_model
from chibi_pet.rig.runtime import Rig
from chibi_pet.rig.spec import load_spec


def blink(t: float, times=(1.2, 3.7, 5.3), length=0.18) -> float:
    for t0 in times:
        if t0 <= t < t0 + length:
            return abs((t - t0) / length * 2 - 1)  # 1 -> 0 -> 1
    return 1.0


def pose(t: float) -> dict[str, float]:
    s = lambda period, phase=0.0: math.sin(2 * math.pi * t / period + phase)
    talk = 2.4 <= t < 4.0
    return {
        "ParamAngleX": 25 * s(3.0),
        "ParamAngleY": 8 * s(2.2, 1.0),
        "ParamAngleZ": 10 * s(4.0),
        "ParamEyeBallX": 0.6 * s(3.0),
        "ParamEyeLOpen": blink(t),
        "ParamEyeROpen": blink(t),
        "ParamMouthOpenY": abs(s(0.33)) * 0.8 if talk else 0.0,
        "ParamMouthForm": 0.6 if talk else 0.2,
        "ParamBodyAngleX": 5 * s(3.0, -0.6),
        "ParamBodyAngleZ": 4 * s(4.0, -0.4),
        "ParamBreath": 0.5 + 0.5 * s(3.2),
        "ParamSway": 0.6 * s(1.6),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("model_dir")
    ap.add_argument("out")
    ap.add_argument("--rig", default="assets/rig/chibi.rig.json")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--scale", type=float, default=0.3)
    ap.add_argument("--crop", default="", help="x0,y0,x1,y1 in output pixels")
    args = ap.parse_args(argv)

    model = load_model(args.model_dir)
    rig = Rig(model, load_spec(args.rig))
    atlas = load_atlas(model.atlas_path)
    dt = 1.0 / args.fps
    # let physics settle in the starting pose
    rig.set_values(pose(0.0))
    for _ in range(60):
        rig.step_physics(dt)
    crop = tuple(int(v) for v in args.crop.split(",")) if args.crop else None
    frames = []
    t0 = time.time()
    for i in range(int(args.seconds * args.fps)):
        rig.set_values(pose(i * dt))
        rig.step_physics(dt)
        img = to_image(render(rig.evaluate(), atlas, model.canvas, scale=args.scale), background=(236, 241, 238))
        frames.append(img.crop(crop) if crop else img)
    frames[0].save(args.out, save_all=True, append_images=frames[1:], duration=int(1000 / args.fps), loop=0)
    print(f"{len(frames)} frames in {time.time() - t0:.1f}s -> {args.out}")


if __name__ == "__main__":
    main()
