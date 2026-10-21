"""A small CPU rasterizer (numpy) for previews and tests.

The desktop app renders on the GPU; this renderer exists so the rig can be
checked without a GPU or a window (CI, headless servers, preview GIFs). It
does what the GPU pipeline does, just slowly:

  for each part (back to front):
    rasterize its triangles into a part-sized buffer (texture lookups with
    bilinear filtering on a premultiplied-alpha atlas)
    optionally multiply by a clip mask (another part's coverage)
    blend onto the output with "premultiplied over": dst = src + dst * (1 - src.a)

Rendering a part into its own buffer first avoids double-blending pixels on
edges shared by two triangles (which would show as thin seams).
"""

from __future__ import annotations

import numpy as np
from PIL import Image


def load_atlas(path) -> np.ndarray:
    """Load an atlas PNG as premultiplied float32 RGBA in 0..1."""
    a = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32) / 255.0
    a[..., :3] *= a[..., 3:4]
    return a


def _sample_bilinear(tex: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = tex.shape[:2]
    x = np.clip(x - 0.5, 0, w - 1)
    y = np.clip(y - 0.5, 0, h - 1)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (x - x0)[:, None]
    fy = (y - y0)[:, None]
    top = tex[y0, x0] * (1 - fx) + tex[y0, x1] * fx
    bot = tex[y1, x0] * (1 - fx) + tex[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def rasterize_part(pts, uvs, tris, atlas, width, height):
    """Rasterize one part. Returns (buffer, x0, y0) or None if off-screen."""
    if len(tris) == 0:
        return None
    x0 = max(int(np.floor(pts[:, 0].min())), 0)
    y0 = max(int(np.floor(pts[:, 1].min())), 0)
    x1 = min(int(np.ceil(pts[:, 0].max())) + 1, width)
    y1 = min(int(np.ceil(pts[:, 1].max())) + 1, height)
    if x1 <= x0 or y1 <= y0:
        return None
    buf = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.float32)
    ah, aw = atlas.shape[:2]
    tex_xy = uvs * np.array([aw, ah], dtype=np.float32)

    for tri in tris:
        p = pts[tri]
        t = tex_xy[tri]
        bx0 = max(int(np.floor(p[:, 0].min())), x0)
        by0 = max(int(np.floor(p[:, 1].min())), y0)
        bx1 = min(int(np.ceil(p[:, 0].max())) + 1, x1)
        by1 = min(int(np.ceil(p[:, 1].max())) + 1, y1)
        if bx1 <= bx0 or by1 <= by0:
            continue
        (ax, ay), (bx, by), (cx, cy) = p
        area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(area) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(bx0, bx1) + 0.5, np.arange(by0, by1) + 0.5)
        w0 = ((bx - gx) * (cy - gy) - (by - gy) * (cx - gx)) / area
        w1 = ((cx - gx) * (ay - gy) - (cy - gy) * (ax - gx)) / area
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        u = w0[inside] * t[0, 0] + w1[inside] * t[1, 0] + w2[inside] * t[2, 0]
        v = w0[inside] * t[0, 1] + w1[inside] * t[1, 1] + w2[inside] * t[2, 1]
        sub = buf[by0 - y0 : by1 - y0, bx0 - x0 : bx1 - x0]
        sub[inside] = _sample_bilinear(atlas, u, v)
    return buf, x0, y0


def render(items, atlas, canvas, scale=1.0, offset=(0.0, 0.0), size=None) -> np.ndarray:
    """Render DrawItems to a premultiplied float32 RGBA image of `size` (w, h)."""
    if size is None:
        size = (int(round(canvas[0] * scale)), int(round(canvas[1] * scale)))
    width, height = size
    out = np.zeros((height, width, 4), dtype=np.float32)
    mask_names = {it.clip for it in items if it.clip}
    coverage = {}  # part name -> (alpha buffer, x0, y0), for parts used as clip masks
    off = np.asarray(offset, dtype=np.float64)

    for it in items:
        if it.opacity <= 1e-3 and it.name not in mask_names:
            continue
        pts = it.vertices * scale + off
        r = rasterize_part(pts, it.uvs, it.triangles, atlas, width, height)
        if r is None:
            continue
        buf, x0, y0 = r
        h, w = buf.shape[:2]
        if it.name in mask_names:
            # a fading mask hides what it clips too (e.g. a closing eye hides the iris)
            coverage[it.name] = (buf[..., 3] * min(it.opacity, 1.0), x0, y0)
        if it.clip:
            mask = np.zeros((h, w), dtype=np.float32)
            if it.clip in coverage:
                m, mx, my = coverage[it.clip]
                # copy the overlapping region of the mask buffer
                ox0, oy0 = max(x0, mx), max(y0, my)
                ox1, oy1 = min(x0 + w, mx + m.shape[1]), min(y0 + h, my + m.shape[0])
                if ox1 > ox0 and oy1 > oy0:
                    mask[oy0 - y0 : oy1 - y0, ox0 - x0 : ox1 - x0] = m[oy0 - my : oy1 - my, ox0 - mx : ox1 - mx]
            buf *= mask[..., None]
        if it.opacity <= 1e-3:
            continue
        buf *= it.opacity
        dst = out[y0 : y0 + h, x0 : x0 + w]
        dst[:] = buf + dst * (1.0 - buf[..., 3:4])
    return out


def to_image(premult: np.ndarray, background=None) -> Image.Image:
    """Convert premultiplied float RGBA to a PIL image (optionally over a solid colour)."""
    if background is not None:
        bg = np.array(background, dtype=np.float32) / 255.0
        rgb = premult[..., :3] + bg[None, None, :3] * (1.0 - premult[..., 3:4])
        return Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    a = premult[..., 3:4]
    rgb = np.where(a > 1e-6, premult[..., :3] / np.maximum(a, 1e-6), 0.0)
    rgba = np.concatenate([rgb, a], axis=-1)
    return Image.fromarray((np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")
