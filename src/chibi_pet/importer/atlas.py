"""Pack layer images into one texture atlas (simple shelf packing).

One atlas = one texture bind on the GPU for the whole character, which keeps
drawing cheap. Shelf packing wastes some space but is short and predictable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Placement:
    x: int
    y: int
    w: int
    h: int


def _try_pack(sizes, width, height, padding):
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    placements = [None] * len(sizes)
    x = y = padding
    shelf_h = 0
    for i in order:
        w, h = sizes[i]
        if w + 2 * padding > width:
            return None
        if x + w + padding > width:  # start a new shelf
            x = padding
            y += shelf_h + padding
            shelf_h = 0
        if y + h + padding > height:
            return None
        placements[i] = Placement(x, y, w, h)
        x += w + padding
        shelf_h = max(shelf_h, h)
    return placements


def pack(sizes: list[tuple[int, int]], padding: int = 4, max_size: int = 8192):
    """Find the smallest power-of-two square-ish atlas that fits all (w, h) sizes."""
    side = 256
    while side <= max_size:
        for width, height in ((side, side // 2), (side, side)):
            if height < 1:
                continue
            placements = _try_pack(sizes, width, height, padding)
            if placements is not None:
                return (width, height), placements
        side *= 2
    raise ValueError(f"layers do not fit in a {max_size}x{max_size} atlas")


def compose(images: list[np.ndarray], atlas_size, placements) -> np.ndarray:
    """Copy each image into its slot. The padding stays fully transparent; renderers
    premultiply alpha before filtering, so sampling into it fades out cleanly."""
    w, h = atlas_size
    atlas = np.zeros((h, w, 4), dtype=np.uint8)
    for img, p in zip(images, placements):
        atlas[p.y : p.y + p.h, p.x : p.x + p.w] = img
    return atlas
