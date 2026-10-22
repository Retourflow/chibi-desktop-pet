"""Find overlap artifacts frame by frame: doubled outlines, slivers, holes, folds.

    python tools/check_overlap.py <model_dir> [--rig assets/rig/chibi.rig.json]
                                  [--scale 0.5] [--fps 30] [--out build/check]

It plays a stress animation that covers everything the pet can do (every head
angle, body sway, blinking, talking, sudden stops that make hair and tail swing,
the click reactions and dozing) and, for every frame, renders on the GPU:

  1. the normal image
  2. an "owner" image: for each pixel, which part is on top there and where that
     pixel sat in the rest pose

A pixel is *exposed* when the part on top shows content that was hidden under
another part at rest (the painted fill under the hair, say). Exposure as such is
expected, it is what the hidden fill is for. It is a defect when:

  line    exposed content is outline ink: a hidden line slid out -> doubled outline.
          (Not where it is the part's own outline along its edge, bordering a part
          below it or the background: a hidden edge drawn as an outline, uncovered.
          Nor where it runs into a line that showed at rest: that line just thickens.)
  sliver  exposed content touches the background: a lower layer peeks out past
          the silhouette as a thin strip
  cut     a part's edge that has no drawn outline shows as a hard edge against
          something it did not border at rest: a lock that was cut out of the
          drawing along a straight line, now lying over the shoulder; the plain
          fill under a lock showing its own boundary
  fold    a mesh triangle flipped over: the part overlaps itself
  seam    a see-through hairline inside the character: two neighbouring parts whose
          soft edges both fade out there, so the background shows between them

Background that becomes enclosed by the character (a gap between the swinging
tail and the hair, say) is reported as "gap" for information; it is natural
motion, not a defect, as long as no cut edge borders it.

The report lists the worst frames and writes overlay images for them (red = line,
yellow = sliver, orange = cut, magenta = gap, cyan = harmless exposed fill), plus
heat.png: the rest pose tinted where defects came from over the whole animation,
and defect totals per part in report.json. Exit code 1 if any frame has more
defect pixels than --tolerance.

Needs OpenGL (moderngl). On a headless Linux box run it under Xvfb.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from chibi_pet.render.gl import GLRenderer, moderngl
from chibi_pet.rig.model import load_model
from chibi_pet.rig.runtime import Rig
from chibi_pet.rig.spec import load_spec

BG = np.array([236, 241, 238], np.float32)
# parts whose hidden content is meant to be revealed: the iris slides under the lids,
# closed eyes and the open mouth appear on cue
EXPECTED_REVEALS = ("iris_R", "iris_L", "eye_closed_R", "eye_closed_L", "mouth_inner")
LINE_LUM = 30.0  # composited luminance (0..255) below which a pixel counts as outline ink
CUT_NOW = 90.0  # colour step (RGB distance) across a part edge that reads as a hard edge
CUT_REST = 60.0  # ... and the step below which that edge was seamless at rest
OUTLINE_W = 12.0  # canvas px: how wide a part's own outline along its edge may be
MERGE_W = 2.0  # canvas px: revealed ink this close to ink that showed at rest just thickens that line

ID_VS = """
#version 330
uniform vec2 u_scale;
uniform vec2 u_size;
in vec2 in_pos;
in vec2 in_uv;
in vec2 in_rest;
out vec2 v_uv;
out vec2 v_rest;
void main() {
    vec2 px = in_pos * u_scale;
    vec2 ndc = px / u_size * 2.0 - 1.0;
    gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);
    v_uv = in_uv;
    v_rest = in_rest;
}
"""

ID_FS = """
#version 330
uniform sampler2D u_atlas;
uniform sampler2D u_mask;
uniform bool u_use_mask;
uniform bool u_mask_pass;
uniform float u_opacity;
uniform float u_index;
uniform vec2 u_size;
in vec2 v_uv;
in vec2 v_rest;
out vec4 f_out;
void main() {
    float a = texture(u_atlas, v_uv).a * u_opacity;
    if (u_mask_pass) { f_out = vec4(a); return; }
    if (u_use_mask) a *= texture(u_mask, gl_FragCoord.xy / u_size).r;
    if (a < 0.5) discard;
    f_out = vec4(u_index, v_rest.x, v_rest.y, 1.0);
}
"""


class OwnerRenderer:
    """Renders the owner image: (part index, rest x, rest y) of the top part per pixel."""

    def __init__(self, model, rig: Rig, atlas_rgba: np.ndarray, size, ctx):
        self.ctx = ctx
        self.size = size
        a = atlas_rgba.astype(np.float32) / 255.0
        a[..., :3] *= a[..., 3:4]
        h, w = a.shape[:2]
        self.atlas = ctx.texture((w, h), 4, (a * 255 + 0.5).astype(np.uint8).tobytes())
        self.atlas.build_mipmaps()
        self.atlas.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        self.prog = ctx.program(vertex_shader=ID_VS, fragment_shader=ID_FS)
        self.prog["u_atlas"].value = 0
        self.prog["u_mask"].value = 1
        self.prog["u_size"].value = size
        self.index = {p.name: i for i, p in enumerate(rig.parts)}
        self._vaos = {}
        for p in rig.parts:
            mesh = model.parts[p.name]
            pos = ctx.buffer(reserve=len(mesh.vertices) * 8, dynamic=True)
            uv = ctx.buffer(mesh.uvs.astype("f4").tobytes())
            rest = ctx.buffer(p.rest.astype("f4").tobytes())
            ibo = ctx.buffer(mesh.triangles.astype("i4").tobytes())
            vao = ctx.vertex_array(self.prog, [(pos, "2f", "in_pos"), (uv, "2f", "in_uv"), (rest, "2f", "in_rest")], ibo)
            self._vaos[p.name] = (pos, vao)
        self.tex = ctx.texture(size, 4, dtype="f4")
        self.fbo = ctx.framebuffer(color_attachments=[self.tex])
        self.mask = ctx.texture(size, 1, dtype="f4")
        self.mask_fbo = ctx.framebuffer(color_attachments=[self.mask])

    def _draw(self, item, scale):
        pos, vao = self._vaos[item.name]
        pos.write(item.vertices.astype("f4").tobytes())
        self.prog["u_scale"].value = (scale, scale)
        self.prog["u_opacity"].value = float(item.opacity)
        self.prog["u_index"].value = float(self.index[item.name])
        vao.render(moderngl.TRIANGLES)

    def render(self, items, scale) -> np.ndarray:
        """Returns float32 (h, w, 4): index (or -1), rest x, rest y, covered."""
        ctx = self.ctx
        by_name = {it.name: it for it in items}
        self.atlas.use(0)
        self.fbo.use()
        self.fbo.clear(-1.0, 0.0, 0.0, 0.0)
        for it in items:
            if it.opacity <= 1e-3:
                continue
            if it.clip:
                self.mask_fbo.use()
                self.mask_fbo.clear(0.0)
                ctx.enable(moderngl.BLEND)
                ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
                if it.clip in by_name:
                    self.prog["u_mask_pass"].value = True
                    self.prog["u_use_mask"].value = False
                    self._draw(by_name[it.clip], scale)
                self.fbo.use()
                self.mask.use(1)
            ctx.disable(moderngl.BLEND)  # later parts simply overwrite earlier ones
            self.prog["u_mask_pass"].value = False
            self.prog["u_use_mask"].value = bool(it.clip)
            self._draw(it, scale)
        w, h = self.size
        data = np.frombuffer(self.fbo.read(components=4, dtype="f4", alignment=1), dtype=np.float32)
        return data.reshape(h, w, 4)[::-1].copy()


# -- the stress animation --------------------------------------------------


def _blink(t, times, length=0.18):
    for t0 in times:
        if t0 <= t < t0 + length:
            return abs((t - t0) / length * 2 - 1)
    return 1.0


def _smooth_steps(t, period, values):
    """Hold each value for `period` seconds, with a quick 0.12 s ease between them."""
    i = int(t // period)
    a, b = values[i % len(values)], values[(i + 1) % len(values)]
    f = min(1.0, max(0.0, (t - i * period - (period - 0.12)) / 0.12))
    f = f * f * (3 - 2 * f)
    return a + (b - a) * f


SECTIONS = [
    # (name, seconds, function of local time -> params)
    ("turn left/right", 4.0, lambda t: {"ParamAngleX": 30 * math.sin(2 * math.pi * t / 4.0)}),
    ("look up/down", 4.0, lambda t: {"ParamAngleY": 30 * math.sin(2 * math.pi * t / 4.0)}),
    ("tilt", 4.0, lambda t: {"ParamAngleZ": 30 * math.sin(2 * math.pi * t / 4.0)}),
    ("look around", 4.0, lambda t: {"ParamAngleX": 30 * math.cos(2 * math.pi * t / 4.0),
                                    "ParamAngleY": 30 * math.sin(2 * math.pi * t / 4.0),
                                    "ParamAngleZ": 10 * math.sin(2 * math.pi * t / 2.0)}),
    ("body sway", 4.0, lambda t: {"ParamBodyAngleX": 10 * math.sin(2 * math.pi * t / 2.0),
                                  "ParamBodyAngleZ": 10 * math.sin(2 * math.pi * t / 4.0),
                                  "ParamBodyAngleY": 10 * math.cos(2 * math.pi * t / 4.0),
                                  "ParamBreath": 0.5 + 0.5 * math.sin(2 * math.pi * t / 1.5),
                                  "ParamSway": math.sin(2 * math.pi * t / 1.2)}),
    ("head flicks", 4.0, lambda t: {"ParamAngleX": _smooth_steps(t, 0.5, [30, -30, 0, 30, 0, -30, 30, -30]),
                                    "ParamAngleZ": _smooth_steps(t, 0.5, [0, 20, -20, 0, -30, 30, 0, 10]),
                                    "ParamBodyAngleX": _smooth_steps(t, 0.5, [10, -10, 0, 10, -10, 0, 10, -10])}),
    ("face", 4.0, lambda t: {"ParamEyeLOpen": _blink(t, (0.3, 1.5, 2.7)), "ParamEyeROpen": _blink(t, (0.3, 1.5, 2.9)),
                             "ParamEyeBallX": math.sin(2 * math.pi * t / 2.0),
                             "ParamEyeBallY": math.cos(2 * math.pi * t / 2.0),
                             "ParamBrowLY": math.sin(2 * math.pi * t / 1.3), "ParamBrowRY": math.sin(2 * math.pi * t / 1.7),
                             "ParamMouthOpenY": abs(math.sin(2 * math.pi * t / 0.7)),
                             "ParamMouthForm": math.sin(2 * math.pi * t / 2.0),
                             "ParamMouthA": max(0.0, math.sin(2 * math.pi * t / 1.0)),
                             "ParamMouthO": max(0.0, -math.sin(2 * math.pi * t / 1.0))}),
    ("extremes", 4.0, lambda t: {"ParamAngleX": 30 * (1 if t < 2 else -1), "ParamAngleY": 30 * (1 if t % 2 < 1 else -1),
                                 "ParamAngleZ": 30 * (1 if t < 1 or 2 <= t < 3 else -1),
                                 "ParamBodyAngleX": 10 * (1 if t < 2 else -1), "ParamBodyAngleZ": 10 * (-1 if t % 2 < 1 else 1),
                                 "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0}),
]


def stress_frames(fps: int):
    """Yield (time, section name, params) for the whole stress animation."""
    t0 = 0.0
    for name, seconds, fn in SECTIONS:
        n = round(seconds * fps)
        for i in range(n):
            yield t0 + i / fps, name, fn(i / fps)
        t0 += seconds


def behaviour_frames(fps: int, t0: float):
    """The pet's own drivers, blended like the window does: idle + mouse follow, then
    dozing, click reactions and lip sync on top (see app/pet_window.py)."""
    import random

    from chibi_pet.drive.behavior import Dozer, LipSync, ReactionPlayer
    from chibi_pet.drive.idle import IdleAnimator
    from chibi_pet.drive.mouse import MouseFollow
    from chibi_pet.drive.vts import blend

    rng = random.Random(7)
    idle, mouse = IdleAnimator(rng), MouseFollow()
    dozer, reaction, lips = Dozer(after_seconds=9.0, rng=rng), ReactionPlayer(), LipSync()
    dt = 1.0 / fps
    head = (1000.0, 500.0)
    events = {1.0: ("pat", 1), 2.0: ("pat", 1), 3.5: ("poke", 1), 4.6: ("poke", -1)}
    for i in range(int(16 * fps)):
        t = i * dt
        for te, (kind, side) in events.items():
            if te <= t < te + dt:
                reaction.play(kind, side)
        if 15.0 <= t < 15.0 + dt and dozer.wake():
            reaction.play("wake")
        if 5.5 <= t < 7.5:
            lips.feed_vowel("aiueo"[int(t * 8) % 5])
        # the cursor circles the pet for 9 s, then stops so the pet dozes off
        a = 2 * math.pi * min(t, 8.0) / 4.0
        cursor = (head[0] + 900 * math.cos(a), head[1] + 600 * math.sin(a))
        if t < 8.0:
            dozer.activity()
        values = idle.update(dt)
        follow = mouse.update(cursor, head, dt)
        values.update(follow)
        values["ParamAngleZ"] = values.get("ParamAngleZ", 0.0) + follow["ParamAngleX"] * 0.15
        for layer in (dozer, reaction, lips):
            v, w = layer.update(dt)
            values = blend(values, v, w)
        yield t0 + t, "pet behaviour", values


# -- metrics -----------------------------------------------------------------


@dataclass
class FrameReport:
    t: float
    section: str
    exposed: int = 0
    line: int = 0
    sliver: int = 0
    cut: int = 0
    fold: int = 0
    seam: int = 0
    gap: int = 0  # information only, see the module docstring
    folds_by_part: dict = field(default_factory=dict)

    @property
    def defects(self) -> int:
        return self.line + self.sliver + self.cut + self.fold + self.seam


def shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """out[y, x] = a[y + dy, x + dx], with the border repeated."""
    h, w = a.shape[:2]
    out = np.empty_like(a)
    ys = slice(max(0, -dy), min(h, h - dy))
    xs = slice(max(0, -dx), min(w, w - dx))
    out[ys, xs] = a[max(0, dy) : min(h, h + dy), max(0, dx) : min(w, w + dx)]
    if dy > 0:
        out[h - dy :] = out[h - dy - 1 : h - dy]
    elif dy < 0:
        out[:-dy] = out[-dy : -dy + 1]
    if dx > 0:
        out[:, w - dx :] = out[:, w - dx - 1 : w - dx]
    elif dx < 0:
        out[:, :-dx] = out[:, -dx : -dx + 1]
    return out


def local_contrast(comp: np.ndarray, radius: int = 2) -> np.ndarray:
    """Largest colour distance between each pixel and its neighbours within `radius`."""
    out = np.zeros(comp.shape[:2], np.float32)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dy or dx:
                out = np.maximum(out, np.linalg.norm(comp - shift(comp, dy, dx), axis=-1))
    return out


def triangle_areas(pts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    return 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))


class Checker:
    def __init__(self, model_dir, rig_path, scale, ctx, ignore=EXPECTED_REVEALS):
        self.model = load_model(model_dir)
        self.rig = Rig(self.model, load_spec(rig_path))
        self.scale = scale
        atlas = np.asarray(Image.open(self.model.atlas_path).convert("RGBA"))
        self.size = (round(self.model.canvas[0] * scale), round(self.model.canvas[1] * scale))
        self.gl = GLRenderer(self.model, atlas, self.size, ctx=ctx)
        self.owner_gl = OwnerRenderer(self.model, self.rig, atlas, self.size, ctx)
        self.names = [p.name for p in self.rig.parts]
        self.ignored = np.array([n in ignore for n in self.names] + [True])  # last entry: index -1
        self.rest_area = {p.name: triangle_areas(p.rest, p.triangles) for p in self.rig.parts}
        # rest pose reference
        self.rig.reset()
        items = self.rig.evaluate()
        owner = self.owner_gl.render(items, scale)
        self.rest_owner = np.rint(owner[..., 0]).astype(np.int32)
        # where each part is visible at rest, grown by 2 px so boundary jitter is not "exposure"
        grow = ndimage.generate_binary_structure(2, 1)
        self.vis_rest = np.zeros((len(self.names),) + self.rest_owner.shape, bool)
        for i in range(len(self.names)):
            m = self.rest_owner == i
            if m.any():
                self.vis_rest[i] = ndimage.binary_dilation(m, grow, iterations=2)
        img = self.gl.render(items, scale=scale)
        solid = img[..., 3] >= 128
        self.rest_holes = int((ndimage.binary_fill_holes(solid) & ~solid).sum())
        self.rest_img = img
        a = img[..., 3:4].astype(np.float32) / 255.0
        self.rest_contrast = local_contrast(img[..., :3].astype(np.float32) + BG * (1 - a))

    def check(self, t, section, values, overlay=False):
        rig = self.rig
        rig.set_values(values)
        items = rig.evaluate()
        img = self.gl.render(items, scale=self.scale)
        owner = self.owner_gl.render(items, self.scale)
        idx = np.rint(owner[..., 0]).astype(np.int32)
        on = idx >= 0
        rx = np.clip(np.rint(owner[..., 1] * self.scale - 0.5).astype(np.int32), 0, self.size[0] - 1)
        ry = np.clip(np.rint(owner[..., 2] * self.scale - 0.5).astype(np.int32), 0, self.size[1] - 1)
        exposed = np.zeros(idx.shape, bool)
        exposed[on] = ~self.vis_rest[idx[on], ry[on], rx[on]]
        exposed &= ~self.ignored[idx]

        a = img[..., 3:4].astype(np.float32) / 255.0
        comp = img[..., :3].astype(np.float32) + BG * (1 - a)
        lum = comp @ np.array([0.299, 0.587, 0.114], np.float32)
        solid = img[..., 3] >= 128
        ink = on & (lum < LINE_LUM)
        line = exposed & ink
        if line.any():
            # revealed ink is fine as the part's own outline along its edge, where it borders
            # something lower (a hidden edge drawn as an outline, uncovered), and where it runs
            # into ink that showed at rest (a line thickening as two parts slide apart)
            ok = ndimage.binary_dilation(ink & ~exposed, iterations=max(1, round(MERGE_W * self.scale)))
            for k in np.unique(idx[line]):
                lower = ndimage.distance_transform_edt(idx >= k) <= OUTLINE_W * self.scale
                ok |= (idx == k) & lower
            line &= ~ok
        near_bg = ndimage.binary_dilation(~solid, iterations=2) & solid
        sliver = exposed & near_bg & ~ink
        filled = ndimage.binary_fill_holes(solid)
        holes = filled & ~solid
        n_holes = max(0, int(holes.sum()) - self.rest_holes)
        # see-through hairlines well inside the character (not the rim of a gap)
        inner = ndimage.binary_erosion(filled, iterations=3) & ~ndimage.binary_dilation(holes, iterations=3)
        seam = inner & solid & (img[..., 3] < 240)

        # cut edges: the edge of a part over something lower (or nothing) that is a hard
        # colour step now, is not a drawn outline, and was seamless where that bit of the
        # part sat at rest (or was hidden then: the edge of a plain fill)
        d_ink, (iy, ix) = ndimage.distance_transform_edt(~ink, return_indices=True)
        outlined = (d_ink <= 2.5) & (idx[iy, ix] == idx)  # the part's own outline is right here
        step = np.zeros(idx.shape, np.float32)
        to_bg = np.zeros(idx.shape, bool)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            below = shift(idx, dy, dx)
            edge = on & (below < idx)
            d = np.where(edge, np.linalg.norm(comp - shift(comp, dy, dx), axis=-1), 0.0)
            step = np.maximum(step, d)
            to_bg |= edge & (below < 0) & (d > CUT_NOW)
        hard = (step > CUT_NOW) & ~outlined & ~self.ignored[idx]
        cut = hard & ~exposed & (self.rest_contrast[ry, rx] < CUT_REST)
        cut |= hard & exposed & ~to_bg & ~sliver  # fills: edges against the background are slivers

        rep = FrameReport(t, section, int(exposed.sum()), int(line.sum()), int(sliver.sum()), int(cut.sum()),
                          seam=int(seam.sum()), gap=n_holes)
        for it in items:
            if it.opacity <= 1e-3:
                continue
            ar = triangle_areas(it.vertices, it.triangles)
            r0 = self.rest_area[it.name]
            bad = ((ar * r0) <= 0) | (np.abs(ar) < 0.05 * np.abs(r0))
            if bad.any():
                rep.folds_by_part[it.name] = int(bad.sum())
        rep.fold = sum(rep.folds_by_part.values())
        # where each defect pixel of this frame sat in the rest pose, and on which part
        self.last = {"idx": idx, "rx": rx, "ry": ry, "line": line, "sliver": sliver, "cut": cut, "seam": seam,
                     "exposed": exposed}
        if not overlay:
            return rep, None
        base = comp * 0.55 + 255 * 0.45
        base[exposed] = (150, 230, 240)
        if n_holes:
            base[holes] = (255, 0, 255)
        base[sliver] = (255, 220, 0)
        base[cut] = (255, 120, 0)
        base[seam] = (0, 0, 255)
        base[line] = (255, 0, 0)
        return rep, (np.clip(comp, 0, 255).astype(np.uint8), base.astype(np.uint8))


HEAT_COLORS = {"line": (255, 0, 0), "sliver": (255, 200, 0), "cut": (255, 110, 0), "seam": (0, 60, 255)}


def write_heat_map(chk, heat, path, colors=HEAT_COLORS):
    """The rest pose, greyed, with each pixel tinted by how many frames it was a defect in
    (any kind, coloured by the most frequent kind): where on the model the problems are.
    (Also used for reveal.png: where hidden content is shown at all, defect or not.)"""
    a = chk.rest_img[..., 3:4].astype(np.float32) / 255.0
    comp = chk.rest_img[..., :3].astype(np.float32) + BG * (1 - a)
    grey = comp.mean(-1, keepdims=True) * 0.35 + 255 * 0.55
    img = np.repeat(grey, 3, -1)
    kinds = list(colors)
    stack = np.stack([heat[k] for k in kinds])
    total = stack.sum(0)
    if total.any():
        top = stack.argmax(0)
        w = np.log1p(total) / np.log1p(total.max())
        w = np.where(total > 0, 0.35 + 0.65 * w, 0.0)[..., None]
        col = np.array([colors[k] for k in kinds], np.float32)[top]
        img = img * (1 - w) + col * w
    bb = bbox_of(chk.rest_img[..., 3])
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).crop(bb)
    d = ImageDraw.Draw(im)
    for j, k in enumerate(kinds):
        d.rectangle([6, 6 + 14 * j, 16, 16 + 14 * j], fill=colors[k])
        d.text((20, 5 + 14 * j), f"{k} (max {int(heat[k].max())} frames)", fill=(0, 0, 0))
    im.save(path)


def bbox_of(img_alpha, pad=10):
    ys, xs = np.nonzero(img_alpha > 0)
    return max(0, xs.min() - pad), max(0, ys.min() - pad), xs.max() + pad, ys.max() + pad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("model_dir")
    ap.add_argument("--rig", default="assets/rig/chibi.rig.json")
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--out", default="build/check")
    ap.add_argument("--tolerance", type=int, default=0, help="defect pixels allowed per frame")
    ap.add_argument("--worst", type=int, default=8, help="overlay images to write")
    ap.add_argument("--ignore", default=",".join(EXPECTED_REVEALS),
                    help="parts whose hidden content may be revealed (comma separated)")
    args = ap.parse_args(argv)

    ctx = moderngl.create_standalone_context()
    chk = Checker(args.model_dir, args.rig, args.scale, ctx, ignore=tuple(filter(None, args.ignore.split(","))))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    frames = list(stress_frames(args.fps))
    frames += list(behaviour_frames(args.fps, frames[-1][0] + 1.0 / args.fps))
    dt = 1.0 / args.fps
    chk.rig.reset()
    defaults = {k: p.default for k, p in chk.rig.params.items()}
    chk.rig.set_values({**defaults, **frames[0][2]})
    for _ in range(60):
        chk.rig.step_physics(dt)
    reports = []
    states = []
    # defect heat map: how often each rest-pose pixel showed up as a defect, by kind,
    # and defect pixel totals (and frames with any) per part
    heat = {k: np.zeros(chk.rest_owner.shape, np.int32) for k in HEAT_COLORS}
    reveal = np.zeros(chk.rest_owner.shape, np.int32)  # hidden content shown (defect or not)
    part_px = {k: np.zeros(len(chk.names) + 1, np.int64) for k in HEAT_COLORS}
    part_frames = {k: np.zeros(len(chk.names) + 1, np.int64) for k in HEAT_COLORS}
    for t, section, values in frames:
        # parameters not driven this frame go back to their defaults; physics keeps its state
        chk.rig.set_values({k: v for k, v in defaults.items() if not k.startswith("ParamHair")
                            and not k.startswith("ParamTail") and not k.startswith("ParamTassel")
                            and not k.startswith("ParamArm")})
        chk.rig.set_values(values)
        chk.rig.step_physics(dt)
        rep, _ = chk.check(t, section, dict(chk.rig.values))
        reports.append(rep)
        states.append(dict(chk.rig.values))
        last = chk.last
        m = last["exposed"]
        np.add.at(reveal, (last["ry"][m], last["rx"][m]), 1)
        for kind in HEAT_COLORS:
            m = last[kind]
            if m.any():
                np.add.at(heat[kind], (last["ry"][m], last["rx"][m]), 1)
                n = np.bincount(last["idx"][m] + 1, minlength=len(chk.names) + 1)  # (0: no part)
                part_px[kind] += n
                part_frames[kind] += n > 0

    worst = sorted(range(len(reports)), key=lambda i: -reports[i].defects)[: args.worst]
    bb = bbox_of(chk.rest_img[..., 3])
    # head close-up: the face rect grown to take in hair, ears and accessories
    fx, fy, fw, fh = chk.model.parts["face"].rect if "face" in chk.model.parts else (0, 0, *chk.model.canvas)
    hb = [int(v * chk.scale) for v in (fx - 1.0 * fw, fy - 1.1 * fh, fx + 2.0 * fw, fy + 1.6 * fh)]
    for rank, i in enumerate(sorted(worst)):
        rep = reports[i]
        if rep.defects == 0:
            continue
        _, (frame, overlay) = chk.check(rep.t, rep.section, states[i], overlay=True)
        a = Image.fromarray(frame).crop(bb)
        b = Image.fromarray(overlay).crop(bb)
        c = Image.fromarray(frame).crop(hb)
        d = Image.fromarray(overlay).crop(hb)
        zoom = a.height / c.height
        c = c.resize((int(c.width * zoom), a.height), Image.LANCZOS)
        d = d.resize((int(d.width * zoom), a.height), Image.NEAREST)
        sheet = Image.new("RGB", (a.width * 2 + c.width * 2 + 18, a.height), (255, 255, 255))
        x = 0
        for im in (a, b, c, d):
            sheet.paste(im, (x, 0))
            x += im.width + 6
        ImageDraw.Draw(sheet).text((8, 8), f"t={rep.t:.2f}s {rep.section} line={rep.line} sliver={rep.sliver} "
                                   f"cut={rep.cut} seam={rep.seam} fold={rep.fold} (gap={rep.gap})", fill=(0, 0, 0))
        sheet.save(out / f"worst_{i:04d}.png")

    by_section = {}
    for r in reports:
        s = by_section.setdefault(r.section, {"frames": 0, "line": 0, "sliver": 0, "cut": 0, "seam": 0, "fold": 0,
                                              "gap": 0, "bad_frames": 0, "max_defects": 0, "exposed_max": 0})
        s["frames"] += 1
        for k in ("line", "sliver", "cut", "seam", "fold", "gap"):
            s[k] = max(s[k], getattr(r, k))
        s["bad_frames"] += r.defects > args.tolerance
        s["max_defects"] = max(s["max_defects"], r.defects)
        s["exposed_max"] = max(s["exposed_max"], r.exposed)
    folds = {}
    for r in reports:
        for k, v in r.folds_by_part.items():
            folds[k] = max(folds.get(k, 0), v)
    summary = {"frames": len(reports), "bad_frames": sum(r.defects > args.tolerance for r in reports),
               "sections": by_section, "folds_by_part": folds,
               "worst": [{"t": round(reports[i].t, 3), "section": reports[i].section, "line": reports[i].line,
                          "sliver": reports[i].sliver, "cut": reports[i].cut, "seam": reports[i].seam,
                          "fold": reports[i].fold, "gap": reports[i].gap} for i in worst]}
    names = ["(none)"] + chk.names
    summary["defects_by_part"] = {
        names[i]: {k: {"px": int(part_px[k][i]), "frames": int(part_frames[k][i])} for k in HEAT_COLORS
                   if part_px[k][i]}
        for i in np.argsort(-sum(part_px.values())) if sum(int(part_px[k][i]) for k in HEAT_COLORS)}
    write_heat_map(chk, heat, out / "heat.png")
    write_heat_map(chk, {"exposed": reveal}, out / "reveal.png", {"exposed": (0, 150, 230)})
    np.savez_compressed(out / "heat.npz", reveal=reveal, scale=chk.scale, **heat)  # (frame counts, rest pose px)
    (out / "report.json").write_text(json.dumps(summary, indent=1))
    (out / "frames.json").write_text(json.dumps([
        {"t": round(r.t, 4), "section": r.section, "line": r.line, "sliver": r.sliver, "cut": r.cut,
         "seam": r.seam, "fold": r.fold, "gap": r.gap, "exposed": r.exposed} for r in reports]))
    print(f"{summary['frames']} frames, {summary['bad_frames']} with defects > {args.tolerance} px")
    for name, s in by_section.items():
        print(f"  {name:16s} frames={s['frames']:4d} bad={s['bad_frames']:4d} max line={s['line']:4d} "
              f"sliver={s['sliver']:4d} cut={s['cut']:4d} seam={s['seam']:4d} fold={s['fold']:3d}  "
              f"(gap {s['gap']}, exposed {s['exposed_max']})")
    if folds:
        print("  folds:", folds)
    print(f"report and overlays in {out}")
    return 1 if summary["bad_frames"] else 0


if __name__ == "__main__":
    sys.exit(main())
