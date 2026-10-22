"""Cut rough placeholder layers out of the three-view reference sheet.

This is NOT the real art. It is a split of the low-resolution front view so the
importer / rig / renderer can be developed and previewed before the redrawn
PSD (docs/LAYER_SPEC.md) exists. Expect a blurry upscale and plain colour
fills where a moved part uncovers what was behind it.

    python tools/make_placeholder.py <reference_sheet.png> <out_folder>

Output: one full-canvas PNG per part (2048x2048), named like the PSD layers,
which `python -m chibi_pet.importer <out_folder> <model_dir>` accepts.

How the split works (it decides what moves cleanly, so the order matters):

1. Every pixel of the flat drawing gets exactly one owner part, so no content
   is ever in two layers. Content in two layers is what shows up as doubled
   lines ("ghosts") once the layers move by different amounts.
2. Accessories and ears are cut along their drawn outlines: the drawing is
   split into areas enclosed by dark lines, and an area belongs to the part
   whose rough polygon covers most of it. Hair and face are split by colour
   (skin vs. hair) inside rough polygons.
3. A dark outline pixel belongs to the upper of the parts it touches (the
   outline of a hair lock lying on the face belongs to the lock). The eyes are
   split into lashes, iris (an ellipse fitted to the drawn iris) and eye white,
   each with all of its own drawing, so blinking and looking around leave
   nothing behind on the face.
4. Soft edges are unmixed: an anti-aliased edge pixel is a blend of the part on
   top and whatever lies under it (a lower part or the background). It goes to
   the upper part with partial alpha, and the lower part keeps its clean colour
   there. A moving part carries its whole soft edge along: no light fringe stays
   behind, and the outline has no grey rim on a dark desktop.
5. Where an upper part covers a lower one, the lower layer is filled with a
   plain continuation of its own colours (skin under the eyes and bangs, hair
   under the clips, the dress under the arms, ...), never with leftovers of
   the upper part, and every layer reaches at least 3 px in under the layers
   above it, so two soft edges side by side never let the background show
   through. Fills only go under other parts, never into the background, and
   stay 3 px inside the character's outline (where the top part's soft edge
   has to lie over the background, as drawn).

The generated images are derived from the user's character art, so they are
not committed to the repository.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

# Crop of the front view on the reference sheet (x0, y0, x1, y1). All polygons
# below are in the coordinates of this crop.
FRONT_BOX = (10, 70, 550, 740)
CANVAS = 2048
SCALE = 2.9
OFFSET = (1024 - 280 * SCALE, 52.0)  # body centre x=280 -> canvas centre

HAIR_COLOR = (36, 48, 54)
EAR_ROOT = 16.0  # sheet px an ear goes on in under the side lock (its swing at ear height, plus some)
HAIR_SWING = (22, 14)  # sheet px the long back hair slides against the body (x, y; the extremes)
OUTLINE_COLOR = (12, 14, 16)
SHEET_BG = (248, 248, 248)  # the reference sheet's background, for unmixing the outer edge
LINE_LUM = 34  # darker than this = drawn outline (hair is ~45-70, outlines ~0-25)

POLY = {
    "antler_R": [(148, 55), (175, 38), (185, 8), (215, 6), (242, 28), (247, 75), (234, 96), (232, 112), (214, 112), (190, 112), (172, 100), (150, 78)],
    "antler_L": [(338, 95), (343, 58), (358, 14), (390, 6), (406, 38), (426, 48), (440, 70), (422, 100), (402, 110), (392, 112), (366, 112), (352, 110)],
    "clip_R_ring": [(58, 226), (104, 224), (107, 302), (58, 304)],
    "clip_R_plate": [(113, 243), (154, 243), (154, 295), (113, 295)],
    "clip_L": [(397, 228), (440, 228), (440, 284), (397, 284)],
    "tassel_R": [(52, 296), (102, 296), (102, 356), (52, 356)],
    "ear_R": [(86, 296), (112, 288), (132, 290), (132, 340), (120, 350), (100, 328)],
    "ear_L": [(432, 286), (482, 296), (476, 318), (444, 332), (432, 324)],
    "arm_R": [(225, 390), (242, 392), (247, 410), (238, 432), (226, 452), (212, 472), (203, 500), (202, 526), (182, 532), (150, 527), (138, 502), (146, 478), (166, 464), (181, 444), (194, 418), (208, 400)],
    "arm_L": [(318, 386), (336, 390), (353, 404), (366, 424), (381, 444), (399, 463), (416, 477), (425, 500), (417, 524), (395, 532), (370, 527), (350, 506), (340, 481), (330, 456), (321, 431), (313, 408)],
    # bangs and the crown of the head
    "hair_front": [(90, 255), (93, 212), (110, 172), (138, 140), (170, 113), (208, 94), (262, 84), (312, 85), (366, 97), (410, 126), (442, 165), (463, 210), (471, 255), (456, 268), (430, 262), (406, 266), (387, 293), (370, 272), (345, 250), (322, 233), (305, 280), (300, 293), (250, 293), (240, 262), (230, 240), (205, 258), (186, 288), (178, 264), (150, 252), (122, 262)],
    # side hair: the locks framing the face plus the outer locks in front of the ears,
    # down to their tips at the shoulders (the long hair below the tips is back hair)
    "hair_side_R": [(100, 250), (122, 262), (150, 252), (178, 264), (186, 290), (196, 315), (206, 345), (217, 380), (220, 393), (195, 396), (160, 397), (130, 395), (106, 392), (85, 393), (64, 395), (58, 386), (80, 370), (98, 356), (108, 342), (106, 300)],
    "hair_side_L": [(387, 293), (406, 266), (430, 262), (456, 268), (470, 262), (486, 285), (484, 330), (478, 360), (498, 384), (496, 394), (470, 395), (440, 396), (410, 397), (380, 395), (350, 391), (338, 383), (352, 368),
                    # along the lock's outline next to the eye corner, and its strand tip over the cheek
                    (350, 346), (358, 344), (363, 342), (366, 339), (369, 336), (371, 333), (373, 330), (375, 326), (377, 322), (378, 318), (379, 314), (381, 310), (385, 302)],
    "face": [(186, 290), (205, 258), (230, 240), (240, 262), (250, 293), (300, 293), (305, 280), (322, 233), (345, 250), (370, 272), (387, 293), (382, 330), (364, 357), (322, 372), (280, 377), (240, 372), (212, 360), (197, 338)],
    "brow_R": [(214, 240), (246, 240), (246, 256), (214, 256)],
    "brow_L": [(313, 242), (343, 242), (343, 256), (313, 256)],
    "eye_R": [(180, 296), (196, 284), (226, 283), (252, 290), (258, 312), (252, 334), (226, 338), (200, 336), (186, 320)],
    "eye_L": [(298, 291), (322, 282), (352, 282), (378, 288), (386, 306), (376, 326), (352, 336), (326, 336), (305, 324)],
    "mouth_omega": [(265, 335), (295, 335), (295, 352), (265, 352)],
    "tail": [(4, 470), (40, 426), (92, 450), (116, 464), (134, 484), (144, 506), (152, 528), (168, 536), (178, 562), (180, 600), (164, 613), (120, 611), (94, 596), (60, 560), (14, 546), (18, 518)],
    # the bit of neck seen between the chin and the collar
    "neck": [(264, 377), (296, 377), (296, 388), (264, 388)],
    "torso": [(262, 370), (298, 370), (312, 384), (334, 390), (346, 420), (347, 468), (386, 500), (402, 560), (401, 590), (386, 602), (366, 602), (363, 652), (346, 664), (298, 664), (290, 612), (270, 612), (266, 656), (250, 664), (198, 664), (194, 612), (170, 602), (158, 580), (164, 540), (174, 500), (205, 470), (212, 420), (222, 392), (246, 384)],
}
CIRCLE = {"flower_R": ((198, 134), 30), "flower_L": ((378, 134), 29)}
IRIS = {"R": ((226, 313), 23, 21), "L": ((336, 312), 22, 21)}  # rough centre, rx, ry (refined by fit_iris)
FOREHEAD = [(204, 200), (370, 200), (370, 296), (204, 296)]  # face skin continues up under the bangs
NECK_COLUMN = [(265, 318), (295, 318), (295, 394), (265, 394)]  # the whole neck, mostly hidden
UNDER_CHIN = [(262, 378), (298, 378), (298, 390), (262, 390)]  # below the chin's outline: the collar's edges
# the tip of a back-hair strand hanging down onto the tail, beside the right arm: its left
# outline runs into the tail's, and is the strand's
STRAND_TIP = [(136.5, 457.5), (146.5, 457.5), (146.5, 477.5), (138.5, 477.5), (137.5, 469.5), (135.5, 465.5)]
# the bottom outline of the long lock that ends on the dress's left edge (the dress's outline
# runs into it, so the dress took it)
LOCK_HEM = [(398, 558), (410, 558), (410, 564), (398, 564)]

# Draw order, top first (must agree with draw_order in assets/rig/chibi.rig.json).
# An outline pixel touching several parts goes to the first of them in this list.
TOP_FIRST = [
    "tassel_R", "clip_R", "clip_L", "flower_R", "flower_L", "antler_R", "antler_L",
    "hair_front", "hair_side_R", "hair_side_L",
    "mouth_omega", "eyelash_R", "eyelash_L", "iris_R", "iris_L", "eye_white_R", "eye_white_L",
    "brow_R", "brow_L", "face", "ear_R", "ear_L",
    "arm_R", "arm_L", "torso", "neck", "tail", "hair_back",
]
IDX = {n: i for i, n in enumerate(TOP_FIRST)}
ACCESSORIES = ("tassel_R", "clip_R", "clip_L", "flower_R", "flower_L", "antler_R", "antler_L", "ear_R", "ear_L")
NOBODY, DROPPED = -1, -2  # DROPPED: inside the eyes/mouth, replaced by plain skin on the face


def poly_mask(points, shape):
    img = Image.new("L", (shape[1], shape[0]), 0)
    ImageDraw.Draw(img).polygon(points, fill=255)
    return np.asarray(img) > 0


def ellipse_mask(center, rx, ry, shape):
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    return ((xx - center[0]) / rx) ** 2 + ((yy - center[1]) / ry) ** 2 <= 1.0


def disk(r):
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    return np.hypot(yy, xx) <= r


def outer_rim(mask, width=2):
    """The outer `width` pixels of a mask."""
    return mask & ~ndimage.binary_erosion(mask, iterations=width)


def rounded_box(box, n=3.0):
    """Rounded rectangle (superellipse) with the same bounding box as the mask `box`."""
    ys, xs = np.nonzero(box)
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    rx, ry = (xs.max() - xs.min()) / 2 + 0.5, (ys.max() - ys.min()) / 2 + 0.5
    yy, xx = np.mgrid[: box.shape[0], : box.shape[1]]
    return np.abs((xx - cx) / rx) ** n + np.abs((yy - cy) / ry) ** n <= 1.0


def classify(rgb):
    r, g, b = [rgb[..., i].astype(int) for i in range(3)]
    lum = (r + g + b) / 3
    hairlike = (lum > 28) & (lum < 120) & (b >= r + 5) & (g >= r + 3)  # dark teal hair
    teal = (b > 110) & (g > 90) & (r < 90)  # bright teal hair ends
    bg = (lum > 205) & (np.max(rgb, -1).astype(int) - np.min(rgb, -1) < 22)
    return hairlike | teal, bg


def character_alpha(rgb):
    """Everything not connected to the image border through light, grey background."""
    _, bg = classify(rgb)
    labels, _ = ndimage.label(bg)
    border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    outside = np.isin(labels, border[border > 0])
    alpha = ~outside
    alpha = ndimage.binary_opening(alpha, iterations=1)
    # keep only the character itself (drops bits of the side view at the crop edge)
    labels, n = ndimage.label(alpha)
    sizes = ndimage.sum(alpha, labels, range(1, n + 1))
    return labels == 1 + int(np.argmax(sizes))


def line_regions(free, radius=2, min_seed=12):
    """Split the drawing into areas enclosed by outlines ("trapped ball" fill).

    Eroding the line-free area with a small disk closes the little gaps in the
    outlines; the eroded blobs are labelled and then grown back to their full
    size without crossing a line.
    """
    core = ndimage.binary_erosion(free, structure=disk(radius))
    lab, n = ndimage.label(core)
    if n == 0:
        return lab
    sizes = ndimage.sum(core, lab, range(1, n + 1))
    lab = np.where(np.isin(lab, 1 + np.flatnonzero(sizes >= min_seed)), lab, 0)
    while True:
        grown = ndimage.grey_dilation(lab, size=3)
        new = (lab == 0) & free & (grown > 0)
        if not new.any():
            return lab
        lab[new] = grown[new]


def areas_inside(regions, poly, min_share=0.55, not_mostly=None):
    """Union of the outline-enclosed areas that lie mostly inside `poly`
    (and, if given, are not mostly `not_mostly`, e.g. hair-coloured)."""
    ids = np.unique(regions[poly])
    ids = ids[ids > 0]
    if len(ids) == 0:
        return np.zeros_like(poly)
    total = ndimage.sum(np.ones_like(poly), regions, ids)
    keep = ndimage.sum(poly, regions, ids) / total >= min_share
    if not_mostly is not None:
        keep &= ndimage.sum(not_mostly, regions, ids) / total < 0.4
    return np.isin(regions, ids[keep])


def areas_mostly(regions, mask, share):
    """Union of the outline-enclosed areas that are at least `share` `mask`."""
    ids = np.unique(regions)
    ids = ids[ids > 0]
    if len(ids) == 0:
        return np.zeros(regions.shape, bool)
    frac = ndimage.sum(mask, regions, ids) / ndimage.sum(np.ones(regions.shape), regions, ids)
    return np.isin(regions, ids[frac >= share])


def nearest_fill(img, known):
    """Copy every unknown pixel's colour from its nearest known pixel."""
    _, (iy, ix) = ndimage.distance_transform_edt(~known, return_indices=True)
    return img[iy, ix]


def smooth_fill(img, known, region, blur=5):
    """Plain continuation of `img`'s own colours into `region`: nearest colour,
    then blurred so stray detail along the edge does not get smeared in as stripes."""
    out = img.copy()
    if not region.any() or not known.any():
        return out
    near = nearest_fill(img, known).astype(np.float32)
    near = cv2.GaussianBlur(near, (0, 0), blur)
    out[region] = np.clip(near[region], 0, 255).astype(np.uint8)
    return out


def membrane_fill(img, known, region, iters=60, sigma=3.0):
    """Smooth continuation of the `known` colours into `region`: starts from the nearest
    known colour and is blurred over and over (only over known + region pixels, the known
    ones held fixed), which settles into an even gradient between the region's edges
    instead of patches and streaks copied from single edge pixels."""
    out = img.copy()
    if not region.any() or not known.any():
        return out
    cur = nearest_fill(img, known).astype(np.float32)
    w = (known | region).astype(np.float32)
    den = np.maximum(cv2.GaussianBlur(w, (0, 0), sigma), 1e-6)[..., None]
    for _ in range(iters):
        upd = cv2.GaussianBlur(cur * w[..., None], (0, 0), sigma) / den
        cur[region] = upd[region]
    out[region] = np.clip(cur[region] + 0.5, 0, 255).astype(np.uint8)
    return out


def extrude_cut(img, own_m, cover_m, length):
    """Carry a part on past the line where a part on top cuts it off (an ear going in under
    a side lock): the root's cross-section is swept on under the cover, straight in (along
    the root's normal), so the outlines that reach the root carry on as lines and the
    part's paint between them as it is at the root.

    The root is the bit of the cut that faces along the part (from its tip to the cut):
    where the cover only runs along the part's side (the lock lying along the top of an
    ear) nothing is swept. The continuation is a plain band as wide as the root (its sides
    straight lines, not the pixel steps of the drawing where the outlines meet the cover),
    each line across it in the colour of the root at that point.

    Returns (colours, mask, depth): the continuation's colours, the cover pixels it takes
    (up to `length` px past the root) and how far past the part each of them lies."""
    shape = own_m.shape
    out = img.copy()
    mask = np.zeros(shape, bool)
    depth = np.zeros(shape, np.float32)
    cross = ndimage.generate_binary_structure(2, 1)
    cut = own_m & ndimage.binary_dilation(cover_m, cross)
    if cut.sum() < 3:
        return out, mask, depth
    # each cut pixel's normal: the directions in which the cover borders it
    nrm_px = np.zeros(shape + (2,))
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nb = np.zeros(shape, bool)
        nb[max(0, -dy) : shape[0] - max(0, dy), max(0, -dx) : shape[1] - max(0, dx)] = cover_m[
            max(0, dy) : shape[0] - max(0, -dy), max(0, dx) : shape[1] - max(0, -dx)]
        nrm_px[cut & nb] += (dx, dy)
    # the part's axis: from its tip (the pixel farthest from the cut) to the cut
    far = np.where(own_m, ndimage.distance_transform_edt(~cut), -1.0)
    ty, tx = np.unravel_index(np.argmax(far), shape)
    cy, cx = np.nonzero(cut)
    axis = np.array([cx.mean() - tx, cy.mean() - ty])
    axis /= max(np.linalg.norm(axis), 1e-6)
    unit = nrm_px / np.maximum(np.linalg.norm(nrm_px, axis=-1, keepdims=True), 1e-6)
    root = cut & (unit @ axis > 0.5)
    if root.sum() < 3:
        return out, mask, depth
    nrm = nrm_px[root].sum(0)
    nrm /= max(np.linalg.norm(nrm), 1e-6)
    if np.abs(nrm).max() > np.cos(np.radians(20)):  # (nearly straight across: exactly, so the
        nrm = np.round(nrm)  # lines along it are pixel rows, without steps)
    # coordinates along the root (s, whole px) and in under the cover (t)
    gy, gx = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float32)
    t = gx * nrm[0] + gy * nrm[1]
    s = np.rint(gy * nrm[0] - gx * nrm[1]).astype(int)
    ry, rx = np.nonzero(root)
    s0 = s[root].min()
    n = s[root].max() - s0 + 1
    # per line across: the root's colour (its pixel farthest in), where the part ends, and
    # where the root ends
    profile = np.full((n, 3), -1.0)
    t_root = np.full(n, -np.inf)
    for y, x in zip(ry, rx):
        k = s[y, x] - s0
        if t[y, x] > t_root[k]:
            t_root[k] = t[y, x]
            profile[k] = img[y, x]
    have = t_root > -np.inf
    near = np.abs(np.arange(n)[:, None] - np.flatnonzero(have)[None, :]).argmin(1)
    profile = profile[np.flatnonzero(have)[near]]
    t_end = t_root[have].max()
    sel = (s >= s0) & (s < s0 + n)
    k_all = np.clip(s - s0, 0, n - 1)
    t_part = np.full(n, -np.inf)
    np.maximum.at(t_part, k_all[own_m & sel], t[own_m & sel])
    t_part = np.where(np.isfinite(t_part), t_part, t_end)
    take = sel & cover_m & ~own_m & (t > t_part[k_all]) & (t <= t_end + length)
    # (only what lies right behind the root, not round some other bit of the part)
    lab, _ = ndimage.label(take | root)
    take &= np.isin(lab, np.unique(lab[root]))
    out[take] = np.clip(np.rint(profile[k_all[take]]), 0, 255).astype(out.dtype)
    mask[take] = True
    depth[take] = t[take] - t_part[k_all[take]]
    return out, mask, depth


def comes_out(cover, bad_end, reach):
    """Pixels of `cover` that a part hidden under it shows over `bad_end` once the cover
    slides `reach` = (dx, dy) px against it: walking straight left, right, up or down through
    `cover`, the walk leaves it within that many px onto a `bad_end` pixel (or off the
    image). Next to anything else (the part's own pixels, say) the hidden bit just carries
    the part on."""
    bad = np.zeros(cover.shape, bool)
    stop = ~cover
    for axis, r in ((1, reach[0]), (0, reach[1])):
        n = cover.shape[axis]
        idx = np.broadcast_to(np.arange(n).reshape((1, n) if axis == 1 else (n, 1)), cover.shape)
        # the first pixel off the cover after each pixel, and the last one before it
        after = np.flip(np.minimum.accumulate(np.flip(np.where(stop, idx, n), axis), axis=axis), axis)
        before = np.maximum.accumulate(np.where(stop, idx, -1), axis=axis)
        for end, dist in ((after, after - idx), (before, idx - before)):
            on_image = (end >= 0) & (end < n)
            lands_bad = np.take_along_axis(bad_end, np.clip(end, 0, n - 1), axis=axis)
            bad |= cover & (dist <= r) & (lands_bad | ~on_image)
    return bad


def keep_big(mask, min_size):
    """The connected pieces of `mask` (8-connected) with at least `min_size` pixels."""
    lab, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    return np.isin(lab, 1 + np.flatnonzero(sizes >= min_size))


def color_field(img, mask, sigma=1.0):
    """Local mean colour of the `mask` pixels, carried out to every pixel from the nearest
    covered one. Returns (field, distance to the nearest covered pixel)."""
    m = mask.astype(np.float32)
    den = ndimage.gaussian_filter(m, sigma)
    num = np.stack([ndimage.gaussian_filter(img[..., c] * m, sigma) for c in range(3)], -1)
    covered = den > 0.05
    mean = num / np.maximum(den, 1e-6)[..., None]
    if not covered.any():
        return np.zeros_like(img), np.full(mask.shape, np.inf)
    dist, (iy, ix) = ndimage.distance_transform_edt(~covered, return_indices=True)
    return mean[iy, ix], dist


def blend_fit(c, x, u):
    """Unmix c = a*u + (1-a)*x for a: returns (a unclipped, a, residual)."""
    ux = u - x
    n2 = (ux ** 2).sum(-1)
    proj = ((c - x) * ux).sum(-1) / np.maximum(n2, 1e-6)
    a = np.clip(proj, 0.0, 1.0)
    return proj, a, np.linalg.norm(c - (x + a[:, None] * ux), axis=-1)


def best_endpoint(img, mask, ys, xs, c, x, r=3, margin=10.0):
    """For each pixel (ys, xs) with colour c over lower colour x: the colour u of a `mask`
    pixel within `r` px (and further from x than c is) that best explains c as a blend of
    x and u. For thin strokes with no inside of their own, and for parts of several colours
    (a red liner along a black lash band). Returns (u, residual; inf where none)."""
    h, w = mask.shape
    best_res = np.full(len(ys), np.inf, np.float32)
    best_u = np.zeros((len(ys), 3), np.float32)
    dcx = np.linalg.norm(c - x, axis=-1)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy * dy + dx * dx > r * r:
                continue
            yy, xx = np.clip(ys + dy, 0, h - 1), np.clip(xs + dx, 0, w - 1)
            inb = (ys + dy >= 0) & (ys + dy < h) & (xs + dx >= 0) & (xs + dx < w)
            u = img[yy, xx]
            _, _, res = blend_fit(c, x, u)
            better = inb & mask[yy, xx] & (np.linalg.norm(u - x, axis=-1) > dcx + margin) & (res < best_res)
            best_res[better] = res[better]
            best_u[better] = u[better]
    return best_u, best_res


def fit_iris(lum, sat, red, guess):
    """Ellipse along the outer edge of a drawn iris, as ((cx, cy), (half width, half height),
    angle in degrees). Rays from the rough centre stop at the first light, unsaturated pixel
    (eye white or skin) or bright red one (the dots under the eye). The top of the iris is
    hidden under the lashes, so only rays to the sides and below are used."""
    (cx, cy), rx, ry = guess
    h, w = lum.shape
    pts = []
    for deg in range(0, 360, 3):
        t = np.radians(deg)
        dx, dy = np.cos(t), np.sin(t)
        if dy < -0.3:
            continue
        inside = False
        for r in np.arange(14.0, 1.6 * max(rx, ry), 0.5):
            x, y = round(cx + dx * r), round(cy + dy * r)
            if not (0 <= x < w and 0 <= y < h):
                break
            if (lum[y, x] > 185 and sat[y, x] < 50) or (red[y, x] and lum[y, x] > 100):
                if inside:
                    pts.append((cx + dx * (r - 0.25), cy + dy * (r - 0.25)))
                break
            inside = True
    (ex, ey), (ew, eh), ang = cv2.fitEllipse(np.array(pts, np.float32))
    return (ex, ey), (ew / 2.0, eh / 2.0), ang


def rotated_ellipse(params, shape, grow=0.0):
    (cx, cy), (a, b), ang = params
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    t = np.radians(ang)
    x, y = xx - cx, yy - cy
    u = x * np.cos(t) + y * np.sin(t)
    v = -x * np.sin(t) + y * np.cos(t)
    return (u / (a + grow)) ** 2 + (v / (b + grow)) ** 2 <= 1.0


def polar_fill(img, known, region, params, n_r=20, n_phi=120, r_max=1.15):
    """Fill `region` of an iris from its `known` pixels in the ellipse's polar coordinates:
    each hidden pixel takes the colours found at the same relative radius on either side of
    it (interpolated round the circle). The dark ring and the iris's colour bands carry on
    under the lashes as rings, instead of a blob smeared down from the edge above."""
    out = img.copy()
    if not region.any() or not known.any():
        return out
    (cx, cy), (a, b), ang = params
    t = np.radians(ang)

    def polar(ys, xs):
        x, y = xs - cx, ys - cy
        u = (x * np.cos(t) + y * np.sin(t)) / a
        v = (-x * np.sin(t) + y * np.cos(t)) / b
        return np.hypot(u, v) / r_max * n_r - 0.5, (np.arctan2(v, u) + np.pi) / (2 * np.pi) * n_phi - 0.5

    ky, kx = np.nonzero(known)
    kr, kp = polar(ky, kx)
    ri = np.clip(np.round(kr).astype(int), 0, n_r - 1)
    pi = np.round(kp).astype(int) % n_phi
    sums = np.zeros((n_r, n_phi, 3))
    cnt = np.zeros((n_r, n_phi))
    np.add.at(sums, (ri, pi), img[ky, kx].astype(np.float64))
    np.add.at(cnt, (ri, pi), 1)
    grid = np.full((n_r, n_phi, 3), np.nan)
    rows = []
    for r in range(n_r):
        has = cnt[r] > 0
        if not has.any():
            continue
        rows.append(r)
        ph = np.flatnonzero(has)
        for c in range(3):
            grid[r, :, c] = np.interp(np.arange(n_phi), ph, sums[r, ph, c] / cnt[r, ph], period=n_phi)
    rows = np.array(rows)
    for r in range(n_r):  # radii with no known pixel at all: the nearest radius that has some
        if np.isnan(grid[r, 0, 0]):
            grid[r] = grid[rows[np.argmin(np.abs(rows - r))]]
    grid = np.concatenate([grid[:, -1:], grid, grid[:, :1]], 1)  # wrap round the circle
    fy, fx = np.nonzero(region)
    fr, fp = polar(fy, fx)
    coords = [np.clip(fr, 0, n_r - 1), fp + 1]
    for c in range(3):
        out[fy, fx, c] = np.clip(ndimage.map_coordinates(grid[..., c], coords, order=1, mode="nearest") + 0.5, 0, 255)
    return out


def split_eye(side, rgb, lum, sat, red, hairlike, free, P):
    """Split one eye of the drawing into lashes, iris and eye white (masks on the sheet).

    lash:  the dark and red strokes: the thick lash band across the top, the liner wings
           and the lower liner (not the small red dots under the eye: face paint)
    iris:  what shows of the drawn iris; `iris_full` is its whole ellipse, fitted to the
           outer edge of the iris ring, including the part hidden under the lash band
    white: the light, neutral eye white next to the iris; `opening` is the visible eye
           (white + iris), the shape the iris is clipped to when it moves
    """
    shape = lum.shape
    r, b = rgb[..., 0].astype(int), rgb[..., 2].astype(int)
    yy = np.mgrid[: shape[0], : shape[1]][0]
    poly = P[f"eye_{side}"]
    ep = poly & free & ~hairlike
    # red strokes of the eye (liner, pupil ring) lie mostly inside the eye polygon; a red
    # hair streak running past the eye corner does not, and the small red dots under the
    # eye are too small (face paint)
    lab, n = ndimage.label(red & free, structure=np.ones((3, 3), bool))
    ids = np.arange(1, n + 1)
    size = ndimage.sum(np.ones(shape), lab, ids)
    inside = ndimage.sum(poly, lab, ids)
    liner = np.isin(lab, ids[(size >= 30) & (inside >= 0.5 * size)])
    ink = keep_big((ep & (lum < 60)) | liner, 30)
    iris_fit = fit_iris(lum, sat, red, IRIS[side])
    iris_full = rotated_ellipse(iris_fit, shape)
    # the lash band is a thick dark stroke; the iris ring is thin and stays with the iris
    thick = keep_big(ndimage.binary_opening(ink & (lum < 45), structure=disk(2)), 40)
    band = ndimage.binary_dilation(thick, structure=disk(1)) & ink & (lum < 45)
    # the eye opening lies below the band's top edge (per column; carried on flat past
    # the band's ends): above it are only the band's soft edge and skin
    cols = np.flatnonzero(band.any(axis=0))
    top = np.argmax(band[:, cols], axis=0)
    below_top = yy > np.interp(np.arange(shape[1]), cols, top)[None, :]
    ring = ndimage.binary_dilation(iris_full, structure=disk(1)) & ink & (lum < 60) & ~red & ~band
    iris = ((iris_full & ep) | ring) & ~band & below_top
    lash = ink & ~iris
    # eye white: light, neutral pixels next to the iris or the lash band, not below the iris
    light = ep & ~iris_full & ~lash & (lum > 195) & (np.abs(r - b) < 14) & below_top
    seed = ndimage.binary_dilation(iris_full | band, structure=disk(3)) & light
    lab, _ = ndimage.label(light)
    ids = np.unique(lab[seed])
    white = np.isin(lab, ids[ids > 0]) & (yy <= np.nonzero(iris_full.any(axis=1))[0].max() + 1)
    opening = ndimage.binary_fill_holes(ndimage.binary_closing(white | iris, structure=disk(2)))
    opening &= (ep | iris) & below_top
    white = opening & ~iris & ~lash
    return {"lash": lash, "iris": iris, "iris_full": iris_full, "iris_fit": iris_fit, "band": band,
            "white": white, "opening": opening, "liner": liner}


def assign_owners(rgb, alpha):
    """Return (owner map, helper masks). owner[y, x] is an index into TOP_FIRST,
    NOBODY for background or DROPPED for eye/mouth interiors."""
    shape = rgb.shape[:2]
    r, g, b = [rgb[..., i].astype(int) for i in range(3)]
    lum = (r + g + b) / 3
    hairlike, _ = classify(rgb)
    line = alpha & (lum < LINE_LUM)
    skinlike = (lum > 175) & (r >= g) & (r >= b) & (r - b < 70)
    red = (r > 150) & (g < 110) & (b < 120)
    yy = np.mgrid[: shape[0], : shape[1]][0]
    P = {name: poly_mask(pts, shape) for name, pts in POLY.items()}
    regions = line_regions(alpha & ~line)

    owner = np.full(shape, NOBODY, np.int16)

    def take(name, m):
        m = m & alpha & (owner == NOBODY)
        owner[m] = IDX[name] if isinstance(name, str) else name
        return m

    # 1. accessories and ears: whole outline-enclosed areas
    for name, (c, rad) in CIRCLE.items():
        take(name, areas_inside(regions, ellipse_mask(c, rad, rad, shape) & alpha, not_mostly=hairlike) & ~line)
    P["clip_R"] = P.pop("clip_R_ring") | P.pop("clip_R_plate")
    for name in ("tassel_R", "clip_R", "clip_L", "antler_R", "antler_L", "ear_R", "ear_L"):
        m = areas_inside(regions, P[name] & alpha, not_mostly=hairlike) & ~line
        if name.startswith("clip"):
            m &= ~red  # red hair streaks run right next to the clips
        if name.startswith("antler"):  # the red base dips into the hair below the polygon
            near = ndimage.binary_dilation(P[name], structure=disk(8)) & alpha
            m |= areas_inside(regions, near & red, min_share=0.5) & ~line
        take(name, m)

    # 2. eyes (lashes, iris, eye white), brows, mouth. Their soft edges and what is left
    # of the drawing around them are sorted out by unmix_edges (step 9).
    face_poly = P["face"]
    sat = np.max(rgb, -1).astype(int) - np.min(rgb, -1)
    eye_parts = {}
    for side in ("R", "L"):
        e = split_eye(side, rgb, lum, sat, red, hairlike, alpha & (owner == NOBODY), P)
        eye_parts[side] = e
        take(f"eyelash_{side}", e["lash"])
        take(f"iris_{side}", e["iris"])
        take(f"eye_white_{side}", e["white"])
    for name in ("brow_R", "brow_L"):
        take(name, P[name] & (g - r > 25) & (lum < 170))
    take("mouth_omega", P["mouth_omega"] & face_poly & (r - g > 40))
    # The face must stay inside its bounding box: the rig spec positions the whole
    # head relative to the face rect, so the face must not grow (the box reaches 4 px
    # below the face polygon so the chin's outline fits).
    forehead = poly_mask(FOREHEAD, shape)
    ys, xs = np.nonzero(face_poly | forehead)
    in_box = np.zeros(shape, bool)
    in_box[ys.min() : ys.max() + 5, xs.min() : xs.max() + 1] = True
    # Face paint is light (skin, blush, eye whites) or red (eyeliner, cheek dots); dark
    # pixels on the face are hair strands or outlines.
    face_paint = ((lum >= 120) | red) & ~line
    # eyeliner wings and bits of eye white just outside the eye polygons belong to the
    # eye, not to the hair lock next to it. Outside the face box only what joins the lash
    # (not specks of a lock's red streak nearby), and also the dark end of a red wing, pure
    # red darkening into the outline: left on the hair, it stayed behind as a red speck
    # when the eye closes.
    for side in ("R", "L"):
        other = P["eye_L" if side == "R" else "eye_R"]
        near_eye = ndimage.binary_dilation(P[f"eye_{side}"], structure=disk(8)) & ~other
        zone = near_eye & face_paint & ~hairlike
        take(f"eyelash_{side}", zone & eye_parts[side]["liner"])
        lash = owner == IDX[f"eyelash_{side}"]
        wing_end = near_eye & (r > 40) & (g < 30) & (b < 30)
        cand = ((zone & ~in_box) | wing_end) & (owner == NOBODY)
        lab, _ = ndimage.label(cand | lash, structure=np.ones((3, 3), bool))
        take(f"eyelash_{side}", cand & np.isin(lab, np.unique(lab[lash])) & (lab > 0))
        take("face", zone & in_box & ~red)  # (red next to the eye but not its liner: hair streaks)

    # 3. hair in front of the face: bangs (plus strand tips hanging onto the upper face), side hair.
    # Light neutral greys are the soft edge between an outline and the background; they
    # go with the outlines (step 7), not with whatever polygon they happen to be in.
    edge_grey = (lum > 80) & (sat < 30) & ~skinlike
    bangs = P["hair_front"]
    strands = face_poly & (lum < 130) & ~red & (yy < 300)
    cand = (bangs | strands) & ~skinlike & ~line & ~edge_grey
    lab, _ = ndimage.label(cand & alpha & (owner == NOBODY))
    touching = np.unique(lab[bangs])
    take("hair_front", np.isin(lab, touching[touching > 0]))
    take("hair_front", bangs & ~skinlike & ~line & ~edge_grey)
    # A side lock ends at its drawn tips. The long hair below the tips is a separate
    # outline-enclosed area that runs on far below the chin; it stays back hair, so the
    # lock has no straight cut through the long hair that would show when the head tilts.
    chin_y = int(np.nonzero(face_poly.any(axis=1))[0].max())
    long_hair = np.zeros(shape, bool)
    for i in np.unique(regions[(P["hair_side_R"] | P["hair_side_L"]) & alpha]):
        area = regions == i
        if i > 0 and (area & (yy > chin_y + 20)).sum() > 0.3 * area.sum():
            long_hair |= area
    for side in ("R", "L"):
        lock = P[f"hair_side_{side}"] & ~long_hair & ~skinlike & ~line & ~edge_grey & alpha & (owner == NOBODY)
        # drop 1-px fringes (soft edge pixels along the long hair's outline): left in, they
        # would pull that outline into the lock. Thin bits of the lock itself get handed
        # back with the outlines in step 7.
        take(f"hair_side_{side}", ndimage.binary_opening(lock, structure=np.ones((3, 3), bool)))

    # 4. face: skin inside the face polygon and the skin seen between the strands, plus
    # the reddish-brown line drawn along the chin (lighter than the black outlines)
    near_face = ndimage.binary_dilation(face_poly, iterations=10)
    skin_extra = (bangs | P["hair_side_R"] | P["hair_side_L"]) & skinlike & in_box & near_face
    shade = (r - g > 25) & (r - b > 25) & (lum > 60)  # skin in shadow, brownish skin lines
    take("face", (face_poly | skin_extra) & (face_paint | shade) & in_box)

    # 5. body. Not the hair or the tail where the rough polygons reach over them: hair left
    # on an arm went with it, a strip of hair sliding about over the hair as the arm swayed
    # (and a strand seen between the arm and the dress stayed on the dress).
    hair_areas = areas_mostly(regions, hairlike, 0.9)
    tail_area = (P["tail"] | areas_inside(regions, P["tail"] & alpha)) & ~hair_areas
    for name in ("arm_R", "arm_L"):
        take(name, P[name] & ~line & ~hair_areas & ~tail_area)
    take("neck", P["neck"] & shade & ~line)
    take("torso", P["torso"] & ~line & ~hair_areas & ~tail_area)
    take("tail", tail_area & ~line)
    # 6. remaining hair is back hair (thin hair-tinted slivers are soft outline edges and
    # go with the outlines instead)
    take("hair_back", ndimage.binary_opening(alpha & ~line & hairlike, structure=disk(1)))

    # 7. outlines, and the few odd pixels nobody claimed (mostly soft outline edges)
    assign_lines(owner, alpha & (owner == NOBODY), limits={IDX["face"]: in_box})
    # (the tail being the upper part, it took the strand tip's outline where the two run
    # into each other: the strand then swung out of its own outline as a bare blob of fill,
    # leaving the line hanging on the tail)
    tip = poly_mask(STRAND_TIP, shape) & (owner == IDX["tail"])
    owner[tip] = IDX["hair_back"]
    # (the same with the lock's bottom outline the dress took: the lock slid out from over it
    # with a bare, ragged edge of fill)
    hem = poly_mask(LOCK_HEM, shape) & (owner == IDX["torso"])
    owner[hem] = IDX["hair_back"]
    # the very end of a strand tip lying on the cheek went to the face (the face is lower,
    # but the end was out of the lock's reach) and stayed on the cheek as a black dot when
    # the lock swung away: small bits of outline on the face that run into the outline of
    # a lock over the face go with that lock
    eight = np.ones((3, 3), bool)
    locks = [IDX[n] for n in ("hair_side_R", "hair_side_L", "hair_front")]
    lab, n_bits = ndimage.label(line & (owner == IDX["face"]), structure=eight)
    for j in range(1, n_bits + 1):
        bit = lab == j
        if bit.sum() > 30:  # (the face's own outlines: the jaw)
            continue
        near = owner[ndimage.binary_dilation(bit, structure=eight) & line & ~bit]
        hits = [i for i in locks if (near == i).any()]
        if hits:
            owner[bit] = max(hits, key=lambda i: int((near == i).sum()))
    # 8. specks: tiny islands of a part inside another part (soft pixels of an outline that
    # a polygon happened to cover) join the part around them; left alone they would stay
    # behind as dots when the two parts move apart
    despeckle(owner, [IDX[n] for n in TOP_FIRST if not n.startswith(("eye", "iris", "brow", "mouth"))],
              limits={IDX["face"]: in_box})
    # 9. soft edges -> partial alpha on the part on top. (The lashes do not take the iris's
    # dark green paint as their soft edge: next to the white highlight it looked like a blend
    # of lash and white, and would have stayed on the lash when the iris moves.)
    limits = {IDX["face"]: in_box}
    greenish = (g > r + 6) & (g > b + 4)
    for side in ("R", "L"):
        limits[IDX[f"eyelash_{side}"]] = ~(eye_parts[side]["iris_full"] & greenish)
    edge_alpha, edge_color, edge_below, under_color = unmix_edges(rgb, alpha, owner, limits=limits)

    helpers = {"P": P, "line": line, "lum": lum, "skinlike": skinlike, "hairlike": hairlike, "forehead": forehead,
               "in_box": in_box, "skin_extra": skin_extra, "eye_parts": eye_parts, "yy": yy, "shade": shade, "red": red,
               "edge_alpha": edge_alpha, "edge_color": edge_color, "edge_below": edge_below, "under_color": under_color}
    return owner, helpers


def unmix_edges(rgb, alpha, owner, radius=2.0, min_contrast=40.0, max_residual=45.0, limits=None):
    """Turn the drawing's anti-aliased edges into partial alpha.

    An edge pixel between a part on top (u) and a lower part or the background (x) is a
    blend c = a*u + (1-a)*x. It goes to the upper part with alpha a and colour u; the lower
    part keeps its clean colour x there (its layer is filled in under the edge later). A
    moving part then carries its whole soft edge along: no light fringe stays behind on the
    face, and the outline has no grey rim on a dark desktop. Halo pixels lighter than both
    sides (sharpening overshoot in the reference) become clean lower paint.

    Edges between similar colours (closer than `min_contrast`) and pixels that are not such a
    blend (residual above `max_residual`, e.g. the red dots under the eyes) are left alone.
    `limits` optionally keeps a part inside a mask. Updates `owner` in place; returns
    (edge_alpha, edge_color, edge_below, under_color): per pixel for its owner, NaN where
    unchanged; for a soft edge, the part it was unmixed against (NOBODY: the background;
    -3: none) and the clean colour that part must show under it (x).
    """
    limits = limits or {}
    img = rgb.astype(np.float32)
    h, w = owner.shape
    edge_alpha = np.full((h, w), np.nan, np.float32)
    edge_color = np.full((h, w, 3), np.nan, np.float32)
    edge_below = np.full((h, w), -3, np.int16)
    under_color = np.full((h, w, 3), np.nan, np.float32)
    done = np.zeros((h, w), bool)
    src = np.where(alpha[..., None], img, np.array(SHEET_BG, np.float32))
    bottom = len(TOP_FIRST)
    for i, name in enumerate(TOP_FIRST):
        top = owner == i
        if not top.any():
            continue
        level = np.where(alpha, np.where(owner < 0, bottom, owner), bottom + 1)
        lower = level > i  # lower parts and the background
        d_top = ndimage.distance_transform_edt(~top)
        d_low = ndimage.distance_transform_edt(~lower)
        zone = alpha & ~done & (d_top <= radius) & (d_low <= radius) & (top | lower)
        if i in limits:
            zone &= limits[i] | top
        if not zone.any():
            continue
        core = top & (ndimage.distance_transform_edt(top) >= 1.5)  # inside the part's own stroke
        u_field, u_dist = color_field(img, core if core.sum() >= 5 else top)
        clean = lower & (d_top >= radius + 0.6)  # what lies under it, beyond the soft edge
        x_field, x_dist = color_field(src, clean)
        _, (cy, cx) = ndimage.distance_transform_edt(~clean, return_indices=True)
        below = np.where(alpha[cy, cx], owner[cy, cx], NOBODY)  # the part under it (or nobody)
        _, (ly, lx) = ndimage.distance_transform_edt(~lower, return_indices=True)
        nearest = np.where(alpha[ly, lx], owner[ly, lx], NOBODY)  # the part right across the edge
        ys, xs = np.nonzero(zone)
        c, u, x = img[ys, xs], u_field[ys, xs], x_field[ys, xs]
        # x from the part under it alone: where two lower parts meet under this edge, their
        # mean left a tint of the other one in this one's fill (pink lash paint in the hair
        # under the bangs' edge, showing once the bangs swing)
        below_here = below[ys, xs]
        owner_or_bg = np.where(alpha, owner, NOBODY)
        for j in np.unique(below_here):
            sel = below_here == j
            fj, dj = color_field(src, clean & (owner_or_bg == j))
            fine = sel & np.isfinite(dj[ys, xs])
            x[fine] = fj[ys[fine], xs[fine]]
        # u: the part's local colour; where one of its own pixels close by explains the blend
        # clearly better, that one (a thin stroke with no inside of its own, a red liner
        # running along the black lash band)
        near_core = u_dist[ys, xs] <= 4
        _, _, res_f = blend_fit(c, x, u)
        res_f[~near_core] = np.inf
        u_c, res_c = best_endpoint(img, top, ys, xs, c, x)
        use = res_c + 8.0 < res_f
        u[use] = u_c[use]
        u_ok = near_core | np.isfinite(res_c)
        ux = u - x
        n2 = (ux ** 2).sum(-1)
        proj, a, resid = blend_fit(c, x, u)
        # (and only where the clean colour comes from the part right across the edge, not from
        # something further away behind a thin strip of another part)
        ok = (np.sqrt(n2) >= min_contrast) & (x_dist[ys, xs] <= 6) & u_ok
        ok &= below[ys, xs] == nearest[ys, xs]
        # (nor against a part that has to stay inside its limits and cannot lie under it here:
        # nothing could show that clean colour under the edge. Such a pixel stays solid, and
        # so do its neighbours: a soft pixel next to it would leave a hairline gap between
        # the solid pixel and that part's edge once both are scaled up.)
        blocked = np.zeros((h, w), bool)
        for j, m in limits.items():
            blocked[ys, xs] |= (below[ys, xs] == j) & ~m[ys, xs]
        blocked = ndimage.binary_dilation(blocked, structure=np.ones((3, 3), bool))
        ok &= ~blocked[ys, xs]
        blend = ok & (resid <= max_residual)
        # (almost) none of this part's colour: the lower colour, or a halo lighter than it
        nothing = ok & (proj < 0.06) & (np.linalg.norm(c - x, axis=-1) < 80)
        mine = top[ys, xs]
        # the part's own edge pixels: partial alpha
        sel = mine & blend & ~nothing & (a < 0.98)
        edge_alpha[ys[sel], xs[sel]] = a[sel]
        edge_color[ys[sel], xs[sel]] = u[sel]
        edge_below[ys[sel], xs[sel]] = below[ys[sel], xs[sel]]
        under_color[ys[sel], xs[sel]] = x[sel]
        # an edge pixel with nothing of the part in it goes to what lies under it
        sel = mine & nothing
        down = below[ys, xs]
        for j, m in limits.items():  # (unless that part has to stay inside its limits)
            sel &= ~((down == j) & ~m[ys, xs])
        owner[ys[sel], xs[sel]] = down[sel]
        edge_color[ys[sel], xs[sel]] = x[sel]
        # a lower part's pixels that are really this part's soft edge: move them up
        take = ~mine & blend & ~nothing
        owner[ys[take], xs[take]] = i
        edge_alpha[ys[take], xs[take]] = a[take]
        edge_color[ys[take], xs[take]] = u[take]
        edge_below[ys[take], xs[take]] = below[ys[take], xs[take]]
        under_color[ys[take], xs[take]] = x[take]
        # ... and the lower part keeps a clean colour where it stays, in place of a faint blend
        # (a trace of this part's colour, left behind when it moves) or a halo lighter than it.
        # Detail of its own (a red liner's bright core next to its dark band) stays as drawn.
        dcx = c - x
        faint = np.linalg.norm(dcx, axis=-1) < 30
        halo = dcx.min(-1) > 4  # (lighter in every channel: not a brighter red)
        keep = ~mine & nothing & (owner[ys, xs] >= 0) & (faint | halo)
        edge_color[ys[keep], xs[keep]] = x[keep]
        done[ys[ok], xs[ok]] = True
    return edge_alpha, edge_color, edge_below, under_color


def despeckle(owner, parts, max_size=8, limits=None):
    """Give every small island (<= max_size px, not the part's largest piece) of the
    listed parts to the part that surrounds it most."""
    limits = limits or {}
    for i in parts:
        lab, n = ndimage.label(owner == i)
        if n < 2:
            continue
        sizes = ndimage.sum(np.ones(owner.shape), lab, range(1, n + 1))
        biggest = 1 + int(np.argmax(sizes))
        for k, sl in enumerate(ndimage.find_objects(lab), start=1):
            if k == biggest or sizes[k - 1] > max_size:
                continue
            y0, y1 = max(sl[0].start - 1, 0), sl[0].stop + 1
            x0, x1 = max(sl[1].start - 1, 0), sl[1].stop + 1
            m = lab[y0:y1, x0:x1] == k
            ring = ndimage.binary_dilation(m) & ~m
            nb = owner[y0:y1, x0:x1][ring]
            nb = nb[(nb >= 0) & (nb != i)]
            if nb.size == 0:
                continue
            vals, counts = np.unique(nb, return_counts=True)
            for v in vals[np.argsort(-counts)]:
                if v in limits and not limits[v][y0:y1, x0:x1][m].all():
                    continue
                owner[y0:y1, x0:x1][m] = v
                break


def assign_lines(owner, todo, limits=None):
    """Give each outline pixel to the uppermost part on either side of its stroke.

    A stroke between a hair lock (upper) and the face (lower) is the lock's outline,
    so all of it goes to the lock, not half to each (half an outline left on the face
    would show as a second line when the lock moves). "Either side" = parts within
    about one stroke width of the pixel. Thick dark areas that are not strokes go to
    the nearest part. `limits` optionally keeps a part inside a mask.
    """
    limits = limits or {}
    inner = ndimage.distance_transform_edt(todo)
    width = 2.0 * ndimage.maximum_filter(inner, size=7)
    best = np.full(owner.shape, -1, np.int16)
    d_min = np.full(owner.shape, np.inf)
    dists = []
    for i in range(len(TOP_FIRST)):
        mine = owner == i
        d = ndimage.distance_transform_edt(~mine) if mine.any() else np.full(owner.shape, np.inf)
        dists.append(d)
        d_min = np.minimum(d_min, d)
    for i, name in enumerate(TOP_FIRST):  # top first: the first part within reach wins
        # small accessories only take their own outline, not thin dark hair next to them
        cap = 3.0 if name in ACCESSORIES else 7.0
        hit = todo & (best < 0) & (dists[i] <= np.minimum(width, cap) + 0.5)
        if i in limits:
            hit &= limits[i]
        best[hit] = i
    rest = todo & (best < 0)
    for i in range(len(TOP_FIRST)):
        hit = rest & (dists[i] <= d_min)
        if i in limits:
            hit &= limits[i]
        best[hit] = i
        rest &= ~hit
    if rest.any():  # nearest part is not allowed there: take the nearest allowed one
        allowed = [i for i in range(len(TOP_FIRST)) if i not in limits]
        stack = np.stack([dists[i] for i in allowed])
        best[rest] = np.asarray(allowed)[np.argmin(stack, axis=0)][rest]
    owner[todo] = best[todo]


def make_layers(sheet_path: Path, hints: dict | None = None):
    """Split the front view into part layers (sheet-sized RGBA arrays, by part name).
    If `hints` is a dict, it receives drawing hints for the upscale (see to_canvas), by
    part name: "smooth", where a designed shape's cut edges may be redrawn smoothly, and
    "stroke", a shape drawn from its exact outline instead of its pixels."""
    sheet = Image.open(sheet_path).convert("RGB")
    rgb = np.asarray(sheet.crop(FRONT_BOX)).copy()
    shape = rgb.shape[:2]
    alpha = character_alpha(rgb)
    owner, h = assign_owners(rgb, alpha)
    P, line = h["P"], h["line"]
    # Below the chin's outline the drawn lines are the collar's edges (where they run into
    # the chin's outline the line pixels, and their soft edges, went to the face, the upper
    # part): looking up, the chin carried them off as black blobs under it. They are the
    # collar's, as drawn.
    off_chin = poly_mask(UNDER_CHIN, rgb.shape[:2]) & (owner == IDX["face"])
    owner[off_chin] = IDX["torso"]
    h["edge_alpha"][off_chin] = np.nan
    h["edge_color"][off_chin] = np.nan
    h["edge_below"][off_chin] = -3  # (no soft edge)
    h["under_color"][off_chin] = np.nan
    own = {n: owner == i for n, i in IDX.items()}

    def under(name):
        """Pixels covered by a part drawn above `name` (eye/mouth interiors count as face)."""
        level = np.where(owner == DROPPED, IDX["face"], owner)
        return alpha & (level >= 0) & (level < IDX[name])

    edge_alpha, edge_color = h["edge_alpha"], h["edge_color"]
    # a part's own pixels that keep the drawing's colour at full alpha (not a soft edge)
    solid = {n: own[n] & np.isnan(edge_alpha) & np.isnan(edge_color[..., 0]) for n in IDX}

    def rgba(color_img, m, name=None):
        """Layer image: `color_img` where `m`; the part's soft edges as unmixed."""
        out = np.zeros(shape + (4,), np.uint8)
        out[..., :3] = color_img
        out[..., 3] = (m * 255).astype(np.uint8)
        if name is not None:
            mine = own[name]
            sel = mine & ~np.isnan(edge_color[..., 0])
            out[..., :3][sel] = np.clip(edge_color[sel] + 0.5, 0, 255).astype(np.uint8)
            sel = mine & ~np.isnan(edge_alpha)
            out[..., 3][sel] = np.clip(edge_alpha[sel] * 255 + 0.5, 0, 255).astype(np.uint8)
        return out

    layers: dict[str, np.ndarray] = {}
    for name in TOP_FIRST:
        if name.startswith(("eye_white_", "iris_")) or name in ("face", "hair_back"):
            continue
        layers[name] = rgba(rgb, own[name], name)

    yy = h["yy"]

    # face: everything inside its full shape that it does not own (under the eyes, mouth,
    # brows, bangs and side hair) is a smooth continuation of the skin around it. The shape
    # is rounded off inside the face box (same bounding box, so the rig anchors do not move;
    # no square forehead).
    # It also lies under the whole of both eyes, out to the box's edge: a blink uncovers all
    # of that (a gap there showed as a notch in the face's edge).
    eyes = np.zeros(shape, bool)
    for n in ("eyelash_R", "eyelash_L", "eye_white_R", "eye_white_L", "iris_R", "iris_L"):
        eyes |= own[n]
    face_full = ndimage.binary_closing(P["face"] | h["skin_extra"] | h["forehead"], iterations=2)
    face_full = (ndimage.binary_fill_holes(face_full) & h["in_box"] & rounded_box(h["in_box"])) | own["face"]
    face_full |= eyes & h["in_box"]
    # (plain skin only: not the eye-white bits, highlights and paint on the face)
    rb = rgb[..., 0].astype(int) - rgb[..., 2]
    skin_known = solid["face"] & h["skinlike"] & ~h["red"] & (rb >= 15)
    face_img = membrane_fill(rgb, skin_known, face_full & ~solid["face"])
    layers["face"] = rgba(face_img, face_full, "face")

    # eyes
    white_fill = {}
    highlight_rim = {"R": np.zeros(shape, bool), "L": np.zeros(shape, bool)}
    yy_, xx_ = np.mgrid[: shape[0], : shape[1]]
    for side in ("R", "L"):
        p = h["eye_parts"][side]
        # eye white: the visible opening (white where it shows, carried on under the iris)
        # and 3 px in under the lashes. Where the iris's soft edge reaches past the opening
        # (onto the skin below it) the eye white covers that edge too, coloured like the skin,
        # so the iris is not clipped there.
        wn, iname = f"eye_white_{side}", f"iris_{side}"
        opening = p["opening"] & ~(owner > IDX[wn])  # (minus edge pixels unmixing gave to the skin)
        lashes = own[f"eyelash_{side}"]
        rim = (own[iname] | p["iris"]) & ~opening & ~lashes & face_full
        # (in under the thick lash band only, not the thin liner wings: the iris would show
        # through those when it moves)
        under_band = lashes & ndimage.binary_dilation(p["band"], structure=disk(1)) & np.isnan(edge_alpha)
        white_shape = opening | (ndimage.binary_dilation(opening, structure=disk(3)) & under_band) | rim
        known = solid[wn] & (h["lum"] > 200)
        img = membrane_fill(rgb, known if known.any() else solid[wn], white_shape & ~solid[wn])
        white_fill[side] = img.copy()
        img[rim] = face_img[rim]
        # and 1 px further out over light skin (in the face's own colours, so nothing changes at
        # rest): the eye white's edge, which clips the iris, then lies outside the iris's soft
        # edge and can be drawn crisp and smooth without nicking it (not over the red dots)
        fc = layers["face"][..., :3].astype(int)
        light = (fc.mean(-1) > 175) & (fc[..., 0] - fc[..., 2] < 70)
        band = ndimage.binary_dilation(rim, structure=disk(1)) & own["face"] & light & ~white_shape & ~own[wn]
        img[band] = layers["face"][..., :3][band]
        layers[wn] = rgba(img, white_shape | band | own[wn], wn)
        if hints is not None:
            hints.setdefault(wn, {})["smooth"] = ~ndimage.binary_dilation(own["face"] & ~light, structure=disk(1))
        # iris: what the drawing shows of it (with its soft edges), plus the rest of its
        # ellipse where the lashes cover it, continuing the iris colours and its dark ring,
        # so the iris looks whole when it moves down. Where the drawing shows skin or eye
        # white just past the fitted ellipse, the iris is not there.
        full = p["iris_full"]
        covered = full & (owner >= 0) & (owner < IDX[iname])
        known = solid[iname]
        # (carried on from the iris paint: not its white highlight with its soft edge, or the
        # pupil's red ring)
        sat = np.max(rgb, -1).astype(int) - np.min(rgb, -1)
        glint = ndimage.binary_dilation(known & (h["lum"] >= 200) & (sat < 40), structure=disk(2))
        paint = known & ~glint & ~h["red"]
        img = polar_fill(rgb, paint, (full | own[iname]) & ~known, p["iris_fit"])
        # the white highlight is a round dot: under the lashes it carries on as that circle
        sat = np.max(rgb, -1).astype(int) - np.min(rgb, -1)
        lab, n = ndimage.label(own[iname] & (h["lum"] >= 200) & (sat < 40))
        hl = lab == 1 + int(np.argmax(ndimage.sum(lab > 0, lab, range(1, n + 1)))) if n else lab > 0
        if hl.sum() >= 4:
            # (its size from the dot with its soft edge: the light pixels joined to it)
            lab, _ = ndimage.label(own[iname] & (h["lum"] >= 150) & (sat < 40))
            dot = np.isin(lab, np.unique(lab[hl])) & (lab > 0)
            (hx, hy), hr = cv2.minEnclosingCircle(np.stack(np.nonzero(dot)[::-1], 1).astype(np.float32))
            dist = np.hypot(xx_ - hx, yy_ - hy)
            wgt = np.clip(hr + 1.0 - dist, 0, 1)[..., None]
            highlight_rim[side] = dist <= hr + 2.0
            hidden = full & ~own[iname]
            mix = img.astype(np.float32) * (1 - wgt) + np.median(rgb[hl], axis=0) * wgt
            img[hidden] = np.clip(mix[hidden] + 0.5, 0, 255).astype(np.uint8)
        layers[iname] = rgba(img, covered | own[iname], iname)
        # closed eye: a soft downward arc near the lower lid, 3 px thick in the middle and
        # thinning to round tips. It is drawn from its exact outline at canvas size (a
        # stroke), so it has no pixel steps.
        ys, xs = np.nonzero(opening)
        cx, rx = (xs.min() + xs.max()) / 2, (xs.max() - xs.min()) / 2
        cy, ry = (ys.min() + ys.max()) / 2, (ys.max() - ys.min()) / 2
        t = np.radians(np.linspace(25.0, 155.0, 261))
        along = (t - t[0]) / (t[-1] - t[0])
        centre = np.stack([cx + (rx + 0.5) * np.cos(t), cy - 0.05 * ry + (0.95 * ry - 1.5) * np.sin(t)], 1)
        stroke = Stroke(centre.astype(np.float32), (0.4 + 1.1 * np.sin(np.pi * along) ** 0.6).astype(np.float32))
        closed = np.zeros(shape + (4,), np.uint8)
        closed[..., :3] = (30, 22, 26)
        closed[..., 3] = stroke.mask(shape) * 255
        layers[f"eye_closed_{side}"] = closed
        if hints is not None:
            hints.setdefault(f"eye_closed_{side}", {})["stroke"] = stroke

    def extend(name, region, blur=4):
        """Fill `region` (under upper parts) with a plain continuation of the layer's own colours."""
        img = layers[name]
        mine = img[..., 3] > 0
        region = region & ~mine
        known = (img[..., 3] == 255) & ~line  # continue the paint, not the outlines or soft edges
        if not known.any():
            known = mine
        img[..., :3] = smooth_fill(img[..., :3], known, region, blur)
        img[..., 3] = np.maximum(img[..., 3], region * 255).astype(np.uint8)

    # the head's outline with everything that sits on it filled in, but not what sticks
    # out of it (antler tips, the clip ring, the tassel)
    chin = int(np.nonzero(h["in_box"])[0].max())
    head_core = own["hair_front"] | own["hair_side_R"] | own["hair_side_L"] | face_full
    head_core |= own["hair_back"] & (yy < chin)
    lab, n = ndimage.label(head_core)  # the head itself, not stray hair-coloured specks
    head_core = lab == 1 + int(np.argmax(ndimage.sum(head_core, lab, range(1, n + 1))))
    accessories = np.zeros(shape, bool)
    for n in ("antler_R", "antler_L", "flower_R", "flower_L", "clip_R", "clip_L", "tassel_R"):
        accessories |= own[n]
    # the hair goes on under the clips and flowers that sit on the head's outline
    head_core |= accessories & (P["hair_front"] | P["hair_side_R"] | P["hair_side_L"])
    head_sil = np.zeros(shape, bool)  # row by row between the outermost head pixels
    for y in np.flatnonzero(head_core.any(axis=1)):
        xs = np.flatnonzero(head_core[y])
        head_sil[y, xs[0] : xs[-1] + 1] = True
    head_sil &= alpha

    acc_ring = ndimage.binary_dilation(accessories, structure=disk(3)) & head_sil

    # bangs and side hair continue under the accessories lying on them
    extend("hair_front", acc_ring & under("hair_front") & ndimage.binary_dilation(P["hair_front"], iterations=3))
    for side in ("R", "L"):
        n = f"hair_side_{side}"
        extend(n, acc_ring & under(n) & ndimage.binary_dilation(P[n], iterations=3))

    # ears go on in under the side lock that cuts them off: the ear as it is at the cut,
    # carried straight on (its outlines going on as lines, the skin between) far enough that
    # the lock swinging in toward the face shows more ear, never the end of it, with an
    # outline all round (contour pass). (Before, a blob of plain skin around the cut showed
    # as a pale block with a stepped edge.) Not under the clip and the tassel at the tip:
    # they lie along the ear's edge there, and carrying that edge on smeared its outline
    # into a wide black wedge that showed when the tassel swung off it.
    ear_root = {}
    for side in ("R", "L"):
        n = f"ear_{side}"
        img, m, _ = extrude_cut(layers[n][..., :3], own[n], own[f"hair_side_{side}"], EAR_ROOT)
        layers[n][..., :3][m] = img[m]
        layers[n][..., 3][m] = 255
        ear_root[n] = m

    # torso continues under the arms (shoulders, sides of the dress)
    arms = own["arm_R"] | own["arm_L"]
    extend("torso", ndimage.binary_dilation(arms, structure=disk(2)) & under("torso") & P["torso"], blur=3)
    # tail root continues behind the dress, and behind the arm it runs along (the arm swaying
    # off it showed a see-through gap between them)
    tail_root = ndimage.binary_dilation(own["tail"], structure=disk(14)) & under("tail") & (own["torso"] | arms)
    extend("tail", tail_root, blur=3)

    # back hair: its own visible strands, plus one plain sheet of hair behind the whole
    # head and, below the chin, between the long locks (behind the neck and shoulders).
    # Only under other parts, never into the background, and not behind the tail.
    hb_own = own["hair_back"]
    locks = hb_own | own["hair_side_R"] | own["hair_side_L"]
    xx = np.mgrid[: shape[0], : shape[1]][1]
    mid = shape[1] // 2
    sides = []
    for side_mask in (locks & (xx < mid), locks & (xx >= mid)):
        lab, n = ndimage.label(side_mask)
        sizes = ndimage.sum(side_mask, lab, range(1, n + 1))
        sides.append(lab == 1 + int(np.argmax(sizes)) if n else side_mask)
    left, right = sides
    # below the chin each long lock continues in behind the body, as far as the middle
    span = np.zeros(shape, bool)
    for y in range(chin - 20, shape[0]):
        lx, rx = np.flatnonzero(left[y]), np.flatnonzero(right[y])
        if len(lx):
            span[y, lx[0] : mid] = True
        if len(rx):
            span[y, mid : rx[-1] + 1] = True
    # rounded off, so an uncovered corner of the sheet never shows a straight cut
    sheet = ndimage.binary_closing(head_sil | span, structure=disk(6))
    sheet = ndimage.binary_opening(sheet, structure=disk(6)) | head_sil
    # the hidden sheet stays 3 px inside the drawn outline: there the front hair's soft edge
    # lies over the background, as drawn (a sheet reaching the outline showed as a dark rim)
    sheet &= ndimage.distance_transform_edt(alpha) > 3.0
    behind = sheet & under("hair_back") & ~own["tail"]
    # The long hair swings against the body (sideways, mostly: up to HAIR_SWING px against the
    # dress and the arms). Under them the sheet keeps what that only uncovers next to the
    # hair, which it carries on: not what came out beside the tail or below a lock's tip,
    # where the drawing has no hair (a plain block with a stepped edge, before).
    body = own["torso"] | own["arm_R"] | own["arm_L"] | own["neck"]
    behind &= ~comes_out(body, ~alpha | (owner == NOBODY) | own["tail"], HAIR_SWING)
    hb_img = rgb.copy()
    # colour per row from its own visible hair (dark at the crown -> teal at the ends)
    known = hb_own & ~line & h["hairlike"]
    rows = np.full((shape[0], 3), np.nan)
    for y in np.flatnonzero(known.any(axis=1)):
        rows[y] = np.median(rgb[y][known[y]], axis=0)
    ok = ~np.isnan(rows[:, 0])
    yi = np.arange(shape[0])
    for c in range(3):
        rows[:, c] = np.interp(yi, yi[ok], rows[ok, c]) if ok.any() else HAIR_COLOR[c]
    rows = ndimage.uniform_filter1d(rows, 25, axis=0)
    fill = np.broadcast_to(rows[:, None, :], shape + (3,)).astype(np.uint8).copy()
    crown = np.median(rgb[own["hair_front"] & h["hairlike"] & ~line], axis=0)
    # behind the face and bangs: the plain crown colour, fading into the row colours of
    # the long hair over the 40 rows around the chin (no visible seam)
    wc = np.clip((chin + 10 - yi) / 40.0, 0.0, 1.0)[:, None, None]
    fill = (crown[None, None, :] * wc + fill.astype(np.float32) * (1 - wc)).astype(np.uint8)
    hb_img[behind] = fill[behind]
    # outline the hidden sheet along the outside of the head: when the head turns and the
    # back hair peeks out past the front hair, it shows as hair with an outline, not a smudge.
    # (Drawn smoothly over this in the contour pass at the end; this pixel version stays for
    # the few px next to the drawing's soft edges, where the contour may not go.)
    full = hb_own | behind
    edge = full & ~ndimage.binary_erosion(full, structure=disk(2)) & behind
    edge &= ndimage.distance_transform_edt(alpha) <= 6.0
    hb_img[edge] = OUTLINE_COLOR
    layers["hair_back"] = rgba(hb_img, full, "hair_back")
    # a lash's outer corner reaches past the face box, over the side lock (the face may not
    # grow: the rig anchors the head to its rect). Under it the back hair carries the skin
    # in a band 2 px either side of the box's edge (the drawing has skin there under the
    # lash's soft edge, and the face's cut edge then lies over skin, not over dark hair: no
    # seam), and the lock's own colour further out, so a blink uncovers the lock going on
    # as it is drawn above and below the eye.
    corner = ndimage.binary_dilation(eyes & ~h["in_box"], structure=disk(3)) & behind
    if corner.any():
        lock = (own["hair_side_R"] | own["hair_side_L"]) & ~line & h["hairlike"]
        hair = smooth_fill(rgb, lock, corner, blur=2)
        hb = layers["hair_back"]
        out_box = corner & ~h["in_box"]
        hb[..., :3][out_box] = hair[out_box]
        box_edge = ndimage.binary_dilation(h["in_box"], iterations=2) & ~ndimage.binary_erosion(h["in_box"], iterations=2)
        band = corner & box_edge
        skin = smooth_fill(layers["face"][..., :3], layers["face"][..., 3] == 255, band, blur=1)
        hb[..., :3][band] = skin[band]

    # neck: the bit seen under the chin, continued as a plain column up under the face
    # (looking up or turning uncovers it) and down under the collar. Its sides get an
    # outline where they are hidden, so an uncovered neck looks drawn, not cut out.
    column = poly_mask(NECK_COLUMN, shape)
    hidden = column & under("neck")
    neck_img = rgb.copy()
    known = own["neck"] & ~line & (h["lum"] > 120)
    if not known.any():
        known = column & h["skinlike"]
    # one plain colour: the skin as drawn where the neck shows (in the chin's shadow). (A fill
    # smeared out from those few pixels had a dark streak across it.)
    neck_img[hidden] = np.median(rgb[known], axis=0).astype(np.uint8)
    # (right round what shows of it, under the chin's outline, the colour drawn there: scaled
    # up, the two blend across the edge, and the plain skin showed as a light smudge on the
    # tip of the chin)
    halo = hidden & ndimage.binary_dilation(own["neck"], structure=np.ones((3, 3), bool))
    neck_img[halo] = nearest_fill(rgb, own["neck"])[halo]
    neck_sides = column & ~ndimage.binary_erosion(column, structure=np.ones((1, 2 * 6 + 1), bool))
    layers["neck"] = rgba(neck_img, own["neck"] | hidden, "neck")

    # mouth interior (drawn, not cut): dark red rounded shape, opened by the rig
    img = Image.new("RGBA", (shape[1], shape[0]), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([268, 338, 292, 356], fill=(150, 40, 60, 255), outline=(60, 20, 30, 255), width=2)
    d.ellipse([273, 348, 287, 356], fill=(225, 110, 120, 255))  # tongue
    layers["mouth_inner"] = np.asarray(img).copy()

    # A soft edge's colour is right only over what it was unmixed against, so a hidden fill
    # (what a layer has where it does not own the drawing) never sits under a soft edge that
    # was unmixed against the background or against a part further down. Fills also stay
    # 3 px inside the character's outline (except right under an edge unmixed against them),
    # and 1 px clear of where the drawing shows a part further down (or a soft edge over
    # one): scaled up, a fill right next to it showed through there as a hairline (the ear's
    # skin under the side lock's edge, along the back hair).
    eb = h["edge_below"]
    near_bg = ndimage.distance_transform_edt(alpha) <= 3.0
    level = np.where(owner == DROPPED, IDX["face"], owner)
    soft_edge = ~np.isnan(edge_alpha)

    def near_lower(i):
        """1 px around where the drawing shows a part below i, or a soft edge over one."""
        wrong = (eb == NOBODY) | (eb > i)
        return ndimage.binary_dilation((alpha & (level > i)) | (soft_edge & wrong)) & (eb != i)

    def fill_allowed(i):
        wrong = (eb == NOBODY) | (eb > i)
        return (eb == i) | (~near_bg & ~wrong & ~near_lower(i))

    for name, layer in layers.items():
        if name in IDX and not name.startswith(("iris_", "eye_white_")):  # (designed shapes)
            layer[..., 3][~fill_allowed(IDX[name]) & ~own[name]] = 0

    # right under a soft edge, the part it was unmixed against shows exactly the clean colour
    # the unmixing assumed, so the rest pose adds up to the drawing (a plain skin fill under
    # a lash edge that was drawn over a dark outline showed as a light spot)
    under_color = h["under_color"]
    has_under = ~np.isnan(under_color[..., 0])
    for name, layer in layers.items():
        if name not in IDX:
            continue
        m = has_under & (eb == IDX[name]) & ~own[name]
        if name.startswith("iris_"):
            # (the iris keeps its own fill under the lashes, its rings and highlight, except
            # right round the highlight where the drawing shows it lighter: as drawn there)
            d = np.linalg.norm(np.nan_to_num(under_color) - layer[..., :3].astype(np.float32), axis=-1)
            m &= highlight_rim[name[-1]] & (d > 40)
        if name.startswith("eye_white_"):
            # the eye white lies between the iris/lashes and the face: where their soft edge
            # was unmixed against something below it (the skin under the iris), it carries
            # that colour itself
            m |= has_under & (owner >= 0) & (owner < IDX[name]) & ((eb > IDX[name]) | (eb == NOBODY))
        if name == "face":
            m &= h["in_box"]
        if name.startswith(("iris_", "eye_white_")):
            m &= layer[..., 3] == 255  # (designed shapes: colour only)
        layer[..., :3][m] = np.clip(under_color[m] + 0.5, 0, 255).astype(np.uint8)
        layer[..., 3][m] = 255

    # the eye white's own soft edge (over the skin) becomes a plain cut, drawn crisp and smooth
    # when scaled up: its outline is what the iris gets clipped to, and a soft, pixel-stepped
    # outline clipped it into a jagged edge. The face shows the drawing where the soft edge
    # lay outside the cut (white blending into skin: nothing to see when a blink moves it).
    for side in ("R", "L"):
        ew = layers[f"eye_white_{side}"]
        soft = own[f"eye_white_{side}"] & ~np.isnan(edge_alpha)
        out = soft & (np.nan_to_num(edge_alpha) < 0.5)
        ew[..., :3][soft] = rgb[soft]  # (the drawn blend: exactly as drawn at rest)
        ew[..., 3][soft] = 255
        ew[..., 3][out] = 0
        layers["face"][..., :3][out] = rgb[out]
        layers["face"][..., 3][out] = 255
        # next to the skin it blends smoothly from white into the skin's colour, across 1.2 px
        # in from its (rounded) outline: that edge shows when the iris moves away from it, as
        # a clean curve, not the drawing's pixel steps and the odd speck of the iris's colour
        # (but where the iris's soft edge lies over a red dot, it keeps the red: as drawn)
        opaque = ew[..., 3] == 255
        sdf = np.where(opaque, ndimage.distance_transform_edt(opaque) - 0.5,
                       0.5 - ndimage.distance_transform_edt(~opaque)).astype(np.float32)
        sdf = cv2.GaussianBlur(sdf, (0, 0), 1.2)
        upper = (owner >= 0) & (owner < IDX[f"eye_white_{side}"]) & ~own[f"iris_{side}"]  # lashes, hair
        near_upper = ndimage.binary_dilation(upper, structure=disk(2))
        t = np.clip(sdf / 1.2, 0, 1)[..., None]
        ramp = (face_img * (1 - t) + white_fill[side] * t).astype(np.float32)
        col = ew[..., :3].astype(np.float32)
        odd = (np.linalg.norm(col - white_fill[side], axis=-1) > 40) & (np.linalg.norm(col - face_img, axis=-1) > 40)
        dot = col[..., 0] - col[..., 2] > 60
        fix = opaque & ~near_upper & ~dot & ((sdf < 1.2) | odd)
        ew[..., :3][fix] = np.clip(ramp[fix] + 0.5, 0, 255).astype(np.uint8)

    # every layer reaches 3 px in under the layers above it, so two soft edges side by side
    # never let the background show through between them (the iris and eye white have
    # their own designed shapes). Along the character's outline (where fills are not
    # allowed) a layer still reaches 2 px in under what fully covers it, continuing its own
    # outline: two outlines meeting there otherwise leave a hairline crack once the layers
    # are resampled, and at rest nothing of it shows.
    above = np.zeros(shape, bool)
    opaque_above = np.zeros(shape, bool)
    for name in TOP_FIRST:
        if name not in layers:
            continue
        layer = layers[name]
        if not name.startswith(("iris_", "eye_white_")):
            full_alpha = layer[..., 3] == 255
            ring = ndimage.binary_dilation(full_alpha, structure=disk(3)) & above & ~full_alpha & fill_allowed(IDX[name])
            lip = ndimage.binary_dilation(full_alpha, structure=disk(2)) & opaque_above & ~full_alpha & ~ring
            if name == "face":
                ring &= h["in_box"]
                lip &= h["in_box"]
            if ring.any():
                lum = layer[..., :3].astype(np.float32).mean(-1)
                known = full_alpha & (lum > LINE_LUM)  # paint, not outlines
                layer[..., :3] = smooth_fill(layer[..., :3], known if known.any() else full_alpha, ring, blur=1)
                # ... except right where this part's outline meets the outline of the part on top
                # (dark on dark: a hard cut between them, nothing to unmix). The first pixel under
                # it carries on the outline, so the cut's scaled-up blend shows dark on dark,
                # not a hairline of the paint further in (a red seam along a lash wing).
                ink = full_alpha & (lum <= LINE_LUM)
                meet = ring & ndimage.binary_dilation(ink) & (h["lum"] <= LINE_LUM)  # (4-neighbours)
                # ... and past the part's own outline: where that outline lies between the ring
                # and the paint (its edge running along under the part on top), the ring carries
                # the outline on, not the paint from the far side of it. Uncovered, the outline
                # just gets a little wider; with the paint, a strip of skin or hair showed outside
                # it (the ear's tip under the tassel).
                if ink.any() and known.any():
                    d_ink = ndimage.distance_transform_edt(~ink)
                    meet |= ring & (d_ink + 1.5 <= ndimage.distance_transform_edt(~known))
                if meet.any():
                    layer[..., :3][meet] = nearest_fill(layer[..., :3], ink)[meet]
                layer[..., 3][ring] = 255
            if lip.any():
                fill = smooth_fill(layer[..., :3], full_alpha, lip, blur=1)
                # (not where it differs from the part on top, next to where the drawing shows a
                # part further down: scaled up, it shows through there, as a light hairline of
                # the ear's skin along the side lock's edge, say. Outline under outline stays.)
                off = np.linalg.norm(fill.astype(np.float32) - rgb, axis=-1) > 40
                lip &= ~(off & near_lower(IDX[name]))
                layer[..., :3][lip] = fill[lip]
                layer[..., 3][lip] = 255
        above |= layer[..., 3] > 0
        opaque_above |= layer[..., 3] == 255

    # Hidden edges that moving parts uncover. A part's hidden fill ends somewhere under the
    # parts on top; where those move relative to it (a side lock swinging, the chin rising
    # as the head tilts back) that end shows. Under the side locks and the chin the fill
    # stops a few px past what the drawing shows of the part, and the hidden edges get a
    # drawn outline (see to_canvas), so they read as the part's own contour: the cheek
    # under a side lock, the collar's top edge under the chin, the back hair's edge. The
    # outline only goes where opaque parts cover it at rest, so the rest pose is unchanged.
    band_px = int(np.ceil(CONTOUR_WIDTH + CONTOUR_ROUND))

    def end_under(name, covers, depth=CONTOUR_WIDTH + 3.5):
        """Cut `name`'s hidden fill under `covers` back to `depth` px past its own pixels;
        returns the area around the new edge."""
        lay = layers[name]
        under_c = (lay[..., 3] > 0) & ~own[name] & np.isin(owner, [IDX[c] for c in covers])
        lay[..., 3][under_c & (ndimage.distance_transform_edt(~own[name]) > depth)] = 0
        return ndimage.binary_dilation(under_c, iterations=2)

    end_face = end_under("face", ("hair_side_R", "hair_side_L"))
    end_torso = end_under("torso", ("face",))
    # (1.5 px in from the edge of what covers it, so the outline does not show through that
    # edge's soft rim at rest. Not right up to the edge where the drawing is dark there:
    # tried, and the wider band of ink that then slid out from under the cover's outline
    # read as a doubled line, under the chin and the side locks.)
    cover = np.zeros(shape, bool)
    covered = {}
    for name in TOP_FIRST:
        if name in layers:
            covered[name] = ndimage.distance_transform_edt(cover) > 1.5
            cover |= layers[name][..., 3] == 255

    def outline_hidden_edge(name, region=None):
        lay = layers[name]
        solid_m = lay[..., 3] == 255
        edge = solid_m & ~ndimage.binary_erosion(solid_m, structure=disk(band_px), border_value=1)
        zone = edge & ~own[name] & covered[name]
        if region is not None:
            zone &= region
        if hints is not None and zone.any():
            hints.setdefault(name, {})["contour"] = zone

    outline_hidden_edge("face", end_face)
    outline_hidden_edge("torso", end_torso)
    outline_hidden_edge("neck", ndimage.binary_dilation(neck_sides, iterations=band_px))  # (and its rim)
    outline_hidden_edge("hair_back")
    outline_hidden_edge("tail", ndimage.binary_dilation(tail_root, iterations=band_px))
    for n, m in ear_root.items():
        # the swept-on root, and the ear's edges that run along under the lock. (Not along
        # the tassel: an outline there, uncovered as the tassel swings off, made the tassel's
        # outline look doubled.)
        cut = own[n] & ndimage.binary_dilation(own[f"hair_side_{n[-1]}"], iterations=2)
        outline_hidden_edge(n, ndimage.binary_dilation(m | cut, iterations=band_px + 1))

    return {n: layers[n] for n in layers if layers[n][..., 3].any()}


CUT_RAMP = 1.6  # canvas px over which a cut edge (opaque right next to clear) fades out
CONTOUR_WIDTH = 3.5  # sheet px: the outline drawn along a hidden edge (see to_canvas)
CONTOUR_ROUND = 1.5  # sheet px: how much those edges are rounded off


@dataclass
class Stroke:
    """A drawn line of varying width: discs of radius `half_width` swept along the
    centre line `points` (sheet px, x/y per row), so its ends are round. Its signed
    distance (> 0 inside) is exact at any scale: drawn at canvas size it has no pixel steps."""

    points: np.ndarray
    half_width: np.ndarray

    def sdf(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        out = np.full(np.broadcast(x, y).shape, -np.inf, np.float32)
        for i in range(0, len(self.points), 32):
            p, hw = self.points[i : i + 32], self.half_width[i : i + 32]
            d = np.hypot(x[..., None] - p[:, 0], y[..., None] - p[:, 1])
            out = np.maximum(out, (hw - d).max(-1))
        return out

    def box(self, scale: float = 1.0, pad: float = 2.0):
        """Pixel bounds (x0, y0, x1, y1, end exclusive) covering the stroke at `scale`."""
        r = float(self.half_width.max()) + pad
        lo, hi = (self.points.min(0) - r) * scale, (self.points.max(0) + r) * scale
        return int(np.floor(lo[0])), int(np.floor(lo[1])), int(np.ceil(hi[0])) + 1, int(np.ceil(hi[1])) + 1

    def mask(self, shape) -> np.ndarray:
        """The pixels (of a sheet-sized image) whose centre lies inside the stroke."""
        out = np.zeros(shape, bool)
        x0, y0, x1, y1 = self.box()
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, shape[1]), min(y1, shape[0])
        gy, gx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        out[y0:y1, x0:x1] = self.sdf(gx, gy) > 0
        return out


def to_canvas(layer: np.ndarray, smooth=None, round_off: float = 0.0, stroke: Stroke | None = None,
              contour: np.ndarray | None = None) -> Image.Image:
    """Scale a sheet-sized layer up onto the canvas.

    Soft edges (the drawing's anti-aliasing) are resampled as they are. Cut edges, where
    an opaque pixel sits right next to a clear one (hidden fills, the iris and eye white
    shapes, drawn shapes), would come out as a blurred pixel staircase that shows once a
    moving part uncovers them; inside `smooth` (a sheet mask, or True for everywhere) they
    are redrawn along a smooth contour of the shape's signed distance field instead.
    `round_off` (sheet px) also smooths that contour itself, rounding off one-pixel bumps
    and notches of the shape. A `stroke` layer is drawn from the stroke's exact outline
    in the layer's (single) colour.

    `contour` (a sheet mask) is where the shape's cut edge gets a drawn outline: an ink
    band CONTOUR_WIDTH wide along the inside of the edge, at canvas resolution (both its
    sides are smooth curves), so a hidden edge that a moving part uncovers looks drawn,
    not cut out. Around it the edge is also rounded off by CONTOUR_ROUND (only there:
    rounding off thins out narrow bits of a shape, which elsewhere may show at rest)."""
    if stroke is not None:
        return stroke_to_canvas(layer, stroke)
    if contour is not None and not contour.any():
        contour = None

    # premultiply before resampling so transparent pixels do not darken edges
    pm = layer.astype(np.float32) / 255.0
    pm[..., :3] *= pm[..., 3:4]
    h, w = pm.shape[:2]
    bw, bh = int(w * SCALE), int(h * SCALE)
    big = cv2.resize(pm, (bw, bh), interpolation=cv2.INTER_CUBIC)
    # no ringing: every output pixel stays within the range of the 2x2 source pixels it lies
    # between (bicubic overshoot next to a red line on white came out as a cyan speck)
    lo = np.minimum(pm, np.concatenate([pm[1:], pm[-1:]], 0))
    hi = np.maximum(pm, np.concatenate([pm[1:], pm[-1:]], 0))
    lo = np.minimum(lo, np.concatenate([lo[:, 1:], lo[:, -1:]], 1))
    hi = np.maximum(hi, np.concatenate([hi[:, 1:], hi[:, -1:]], 1))
    sx = np.clip(np.floor((np.arange(bw) + 0.5) * w / bw - 0.5).astype(int), 0, w - 1)
    sy = np.clip(np.floor((np.arange(bh) + 0.5) * h / bh - 0.5).astype(int), 0, h - 1)
    big = np.clip(big, lo[sy][:, sx], hi[sy][:, sx])
    a = big[..., 3]
    color = np.where(a[..., None] > 1e-4, np.clip(big[..., :3] / np.maximum(a, 1e-4)[..., None], 0, 1), 0)
    alpha8 = layer[..., 3]
    opaque, clear = alpha8 == 255, alpha8 == 0
    cross = ndimage.generate_binary_structure(2, 1)
    cut = (opaque & ndimage.binary_dilation(clear, cross)) | (clear & ndimage.binary_dilation(opaque, cross))
    zone = ndimage.binary_dilation(cut, cross, iterations=2 if round_off or contour is not None else 1)
    zone &= ~((alpha8 > 0) & ~opaque)  # (not the soft edges)
    if smooth is None:
        zone[:] = False
    elif smooth is not True:
        zone &= smooth
    sdf_big = None
    if zone.any() or contour is not None:
        sdf = np.where(opaque, ndimage.distance_transform_edt(opaque) - 0.5,
                       0.5 - ndimage.distance_transform_edt(~opaque)).astype(np.float32)
        if round_off:
            sdf = cv2.GaussianBlur(sdf, (0, 0), round_off)
        if contour is not None:
            near = ndimage.binary_dilation(contour, iterations=int(np.ceil(CONTOUR_WIDTH)) + 2)
            wr = cv2.GaussianBlur(near.astype(np.float32), (0, 0), 1.0)
            sdf = sdf * (1 - wr) + cv2.GaussianBlur(sdf, (0, 0), CONTOUR_ROUND) * wr
        sdf_big = cv2.resize(sdf, (bw, bh), interpolation=cv2.INTER_CUBIC)
    if zone.any():
        a_cut = np.clip(0.5 + sdf_big * SCALE / CUT_RAMP, 0, 1)
        zone = cv2.GaussianBlur(zone.astype(np.float32), (0, 0), 0.6)
        wgt = np.clip(cv2.resize(zone, (bw, bh), interpolation=cv2.INTER_LINEAR) * 1.5, 0, 1)
        # the colour just past the old edge: the layer's own colours carried outwards
        near = nearest_fill(layer[..., :3], alpha8 > 0).astype(np.float32) / 255.0
        near = np.clip(cv2.resize(near, (bw, bh), interpolation=cv2.INTER_LINEAR), 0, 1)
        t = np.clip(a / 0.1, 0, 1)[..., None]
        color = np.where(wgt[..., None] > 0, color * t + near * (1 - t), color)
        a = wgt * a_cut + (1 - wgt) * a
    if contour is not None:
        band = np.clip(0.5 + (CONTOUR_WIDTH - sdf_big) * SCALE / CUT_RAMP, 0, 1)
        cz = cv2.GaussianBlur(contour.astype(np.float32), (0, 0), 0.6)
        band *= np.clip(cv2.resize(cz, (bw, bh), interpolation=cv2.INTER_LINEAR) * 1.5, 0, 1)
        ink = np.asarray(OUTLINE_COLOR, np.float32) / 255.0
        color = color * (1 - band[..., None]) + ink * band[..., None]
    out = np.zeros((CANVAS, CANVAS, 4), np.float32)
    ox, oy = round(OFFSET[0]), round(OFFSET[1])
    out[oy : oy + bh, ox : ox + bw, :3] = color[: CANVAS - oy, : CANVAS - ox]
    out[oy : oy + bh, ox : ox + bw, 3] = a[: CANVAS - oy, : CANVAS - ox]
    out[..., :3] *= out[..., 3:4] > 0
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8), "RGBA")


def stroke_to_canvas(layer: np.ndarray, stroke: Stroke) -> Image.Image:
    """Draw a stroke layer on the canvas from its exact outline: each canvas pixel's alpha
    comes from the stroke's signed distance at that pixel (the same CUT_RAMP edge as a
    smoothed cut edge), in the layer's colour."""
    h, w = layer.shape[:2]
    bw, bh = int(w * SCALE), int(h * SCALE)
    fx, fy = bw / w, bh / h  # (exactly as cv2.resize maps the other layers)
    ox, oy = round(OFFSET[0]), round(OFFSET[1])
    x0, y0, x1, y1 = stroke.box(SCALE)
    x0, y0 = max(x0, 0, -ox), max(y0, 0, -oy)
    x1, y1 = min(x1, bw, CANVAS - ox), min(y1, bh, CANVAS - oy)
    out = np.zeros((CANVAS, CANVAS, 4), np.uint8)
    if x1 <= x0 or y1 <= y0:
        return Image.fromarray(out, "RGBA")
    gy, gx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    sdf = stroke.sdf((gx + 0.5) / fx - 0.5, (gy + 0.5) / fy - 0.5)
    a = np.clip(0.5 + sdf * SCALE / CUT_RAMP, 0, 1)
    paint = layer[..., :3][layer[..., 3] > 0]
    color = np.median(paint, axis=0) if len(paint) else np.zeros(3)
    box = out[oy + y0 : oy + y1, ox + x0 : ox + x1]
    box[..., :3] = np.where(a[..., None] > 0, np.round(color), 0).astype(np.uint8)
    box[..., 3] = (a * 255 + 0.5).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def smooth_zone(name, layers, extra=None):
    """Where `name`'s cut edges may be redrawn smoothly (see to_canvas): well under the
    opaque parts drawn above it, so the rest pose stays exactly as drawn, plus `extra`.
    Everywhere on the open mouth: drawn here, and hidden at rest."""
    if name.startswith("mouth_inner"):
        return True
    if name not in IDX:
        return extra
    shape = layers[name].shape[:2]
    cover = np.zeros(shape, bool)
    for other in TOP_FIRST[: IDX[name]]:
        if other in layers:
            cover |= layers[other][..., 3] == 255
    zone = ndimage.binary_erosion(cover, structure=disk(2))
    return zone if extra is None else zone | extra


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sheet", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    hints: dict = {}
    layers = make_layers(args.sheet, hints)
    for name, layer in layers.items():
        hint = hints.get(name, {})
        zone = smooth_zone(name, layers, hint.get("smooth"))
        # (the eye white's outline is the iris's clip edge: rounded off a little more)
        round_off = 1.2 if name.startswith("eye_white_") else 0.0
        to_canvas(layer, zone, round_off, stroke=hint.get("stroke"), contour=hint.get("contour")).save(
            args.out / f"{name}.png")
    print(f"wrote {len(layers)} layers to {args.out}")


if __name__ == "__main__":
    main()
