"""Cut rough placeholder layers out of the three-view reference sheet.

This is NOT the real art. It is a quick, crude split of the low-resolution
front view so the importer / rig / renderer can be developed and previewed
before the redrawn PSD (docs/LAYER_SPEC.md) exists. Expect jagged cut lines,
smeared fills behind moved parts, and a blurry upscale.

    python tools/make_placeholder.py <reference_sheet.png> <out_folder>

Output: one full-canvas PNG per part (2048x2048), named like the PSD layers,
which `python -m chibi_pet.importer <out_folder> <model_dir>` accepts.

The generated images are derived from the user's character art, so they are
not committed to the repository.
"""

from __future__ import annotations

import argparse
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

POLY = {
    "antler_R": [(148, 55), (175, 38), (185, 8), (215, 6), (242, 28), (247, 75), (234, 96), (232, 112), (214, 112), (190, 112), (172, 100), (150, 78)],
    "antler_L": [(338, 95), (343, 58), (358, 14), (390, 6), (406, 38), (426, 48), (440, 70), (422, 100), (402, 110), (392, 112), (366, 112), (352, 110)],
    "clip_R_ring": [(60, 228), (102, 226), (106, 300), (60, 302)],
    "clip_R_plate": [(115, 245), (152, 245), (152, 293), (115, 293)],
    "clip_L": [(399, 230), (438, 230), (438, 282), (399, 282)],
    "tassel_R": [(54, 296), (100, 296), (100, 354), (54, 354)],
    "ear_R": [(90, 300), (112, 290), (130, 292), (130, 338), (120, 347), (102, 325)],
    "ear_L": [(434, 290), (480, 298), (474, 316), (444, 330), (434, 322)],
    "arm_R": [(225, 390), (242, 392), (247, 410), (238, 432), (226, 452), (212, 472), (203, 500), (202, 526), (182, 532), (150, 527), (138, 502), (146, 478), (166, 464), (181, 444), (194, 418), (208, 400)],
    "arm_L": [(318, 386), (336, 390), (353, 404), (366, 424), (381, 444), (399, 463), (416, 477), (425, 500), (417, 524), (395, 532), (370, 527), (350, 506), (340, 481), (330, 456), (321, 431), (313, 408)],
    "hair_front": [(100, 255), (104, 214), (120, 178), (146, 148), (176, 122), (212, 104), (262, 95), (312, 96), (362, 107), (402, 134), (432, 170), (452, 212), (460, 255), (456, 268), (430, 262), (406, 266), (387, 293), (370, 272), (345, 250), (322, 233), (305, 280), (300, 293), (250, 293), (240, 262), (230, 240), (205, 258), (186, 288), (178, 264), (150, 252), (122, 262)],
    "hair_side_R": [(150, 260), (178, 264), (186, 290), (196, 315), (206, 345), (217, 380), (205, 397), (172, 392), (150, 372), (143, 332), (138, 292)],
    "hair_side_L": [(387, 293), (406, 266), (430, 270), (440, 300), (444, 340), (434, 385), (398, 397), (366, 388), (374, 352), (382, 322)],
    "face": [(186, 290), (205, 258), (230, 240), (240, 262), (250, 293), (300, 293), (305, 280), (322, 233), (345, 250), (370, 272), (387, 293), (382, 330), (364, 357), (322, 372), (280, 377), (240, 372), (212, 360), (197, 338)],
    "brow_R": [(214, 240), (246, 240), (246, 256), (214, 256)],
    "brow_L": [(313, 242), (343, 242), (343, 256), (313, 256)],
    "eye_R": [(180, 296), (196, 284), (226, 283), (252, 290), (258, 312), (252, 334), (226, 338), (200, 336), (186, 320)],
    "eye_L": [(298, 291), (322, 282), (352, 282), (378, 288), (386, 306), (376, 326), (352, 336), (326, 336), (305, 324)],
    "mouth_omega": [(265, 335), (295, 335), (295, 352), (265, 352)],
    "tail": [(4, 470), (40, 426), (92, 450), (116, 464), (134, 484), (144, 506), (152, 528), (168, 536), (178, 562), (180, 600), (164, 613), (120, 611), (94, 596), (60, 560), (14, 546), (18, 518)],
    "torso": [(262, 370), (298, 370), (312, 384), (334, 390), (346, 420), (347, 468), (386, 500), (402, 560), (401, 590), (386, 602), (366, 602), (363, 652), (346, 664), (298, 664), (290, 612), (270, 612), (266, 656), (250, 664), (198, 664), (194, 612), (170, 602), (158, 580), (164, 540), (174, 500), (205, 470), (212, 420), (222, 392), (246, 384)],
}
CIRCLE = {"flower_R": ((198, 134), 30), "flower_L": ((378, 134), 29)}
IRIS = {"R": ((226, 313), 23, 21), "L": ((336, 312), 22, 21)}  # centre, rx, ry
EYE_WHITE = {"R": ((225, 314), 27, 19), "L": ((342, 311), 30, 19)}


def poly_mask(points, shape):
    img = Image.new("L", (shape[1], shape[0]), 0)
    ImageDraw.Draw(img).polygon(points, fill=255)
    return np.asarray(img) > 0


def ellipse_mask(center, rx, ry, shape):
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    return ((xx - center[0]) / rx) ** 2 + ((yy - center[1]) / ry) ** 2 <= 1.0


def classify(rgb):
    r, g, b = [rgb[..., i].astype(int) for i in range(3)]
    lum = (r + g + b) / 3
    hairlike = (lum > 28) & (lum < 120) & (b >= r - 5) & (g >= r - 10)  # dark teal hair
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


def make_layers(sheet_path: Path):
    sheet = Image.open(sheet_path).convert("RGB")
    rgb = np.asarray(sheet.crop(FRONT_BOX)).copy()
    shape = rgb.shape[:2]
    alpha = character_alpha(rgb)
    hairlike, _ = classify(rgb)
    claimed = np.zeros(shape, bool)
    masks: dict[str, np.ndarray] = {}

    def claim(name, m):
        m = m & alpha & ~claimed
        masks[name] = m
        claimed[m] = True
        return m

    def tidy(m):
        m = ndimage.binary_closing(m, iterations=2)
        return ndimage.binary_fill_holes(m)

    # front-most accessories: keep their own colours, leave hair around them
    for name, (c, r) in CIRCLE.items():
        claim(name, tidy(ellipse_mask(c, r, r, shape) & ~hairlike))
    for name in ("tassel_R", "clip_L"):
        claim(name, tidy(poly_mask(POLY[name], shape) & ~hairlike))
    claim("clip_R", tidy((poly_mask(POLY["clip_R_ring"], shape) | poly_mask(POLY["clip_R_plate"], shape)) & ~hairlike))
    for name in ("antler_R", "antler_L"):
        claim(name, tidy(poly_mask(POLY[name], shape) & ~hairlike))
    for name in ("arm_R", "arm_L"):
        claim(name, poly_mask(POLY[name], shape))
    for name in ("brow_R", "brow_L"):
        r, g = rgb[..., 0].astype(int), rgb[..., 1].astype(int)
        claim(name, poly_mask(POLY[name], shape) & (g - r > 25) & (rgb.mean(-1) < 170))

    face_poly = poly_mask(POLY["face"], shape)
    # eyes: split into white / iris / lashes
    eye_parts = {}
    for side in ("R", "L"):
        ep = poly_mask(POLY[f"eye_{side}"], shape) & face_poly
        c, rx, ry = IRIS[side]
        iris = ellipse_mask(c, rx, ry, shape) & ep
        wc, wrx, wry = EYE_WHITE[side]
        white = ellipse_mask(wc, wrx, wry, shape) & ep
        r, g, b = [rgb[..., i].astype(int) for i in range(3)]
        lum = (r + g + b) / 3
        dark_or_red = (lum < 110) | ((r > 150) & (g < 110) & (b < 110))
        lash = ep & dark_or_red & ~(iris & (lum > 40))
        # drop tiny specks (the red cheek dots sit inside the eye polygon)
        lab, n = ndimage.label(lash)
        sizes = ndimage.sum(lash, lab, range(1, n + 1))
        lash = np.isin(lab, 1 + np.flatnonzero(sizes >= 25))
        eye_parts[side] = dict(iris=iris & ~lash, white=white, lash=ndimage.binary_dilation(lash) & ep)
        masks[f"iris_{side}"] = eye_parts[side]["iris"]
        masks[f"eyelash_{side}"] = eye_parts[side]["lash"]
        claimed[ep] = True
    claim("mouth_omega", poly_mask(POLY["mouth_omega"], shape) & face_poly & (rgb[..., 0].astype(int) - rgb[..., 1] > 40))
    claim("ear_R", tidy(poly_mask(POLY["ear_R"], shape) & ~hairlike))
    claim("ear_L", tidy(poly_mask(POLY["ear_L"], shape) & ~hairlike))

    # Hair vs. skin. In the flat art the bangs and side hair are painted straight onto
    # the face, so cutting by polygon alone leaves hair lines in the face layer and
    # skin in the hair layers. Once the head turns those layers move by different
    # amounts and the leftovers show up as doubled lines ("ghosts"). So split by
    # colour as well: skin goes to the face, hair and its dark outlines go to the hair.
    r, g, b = [rgb[..., i].astype(int) for i in range(3)]
    lum = (r + g + b) / 3
    outline = lum < 60
    skinlike = (lum > 175) & (r >= g) & (r >= b) & (r - b < 70)
    yy = np.mgrid[: shape[0], : shape[1]][0]
    bangs = poly_mask(POLY["hair_front"], shape)
    sides = poly_mask(POLY["hair_side_R"], shape) | poly_mask(POLY["hair_side_L"], shape)
    # strand tips and their outlines hanging over the upper face
    strands = face_poly & (hairlike | outline) & (yy < 300)
    cand = (bangs | strands) & alpha & ~claimed & ~skinlike
    lab, _ = ndimage.label(cand)
    touching = np.unique(lab[bangs & cand])
    hf = np.isin(lab, touching[touching > 0])
    # the polygon is cut a little inside the silhouette; the outer outline of the top
    # of the head belongs to the bangs layer too, otherwise it stays behind on the back
    # hair and shows up as a second outline when the head turns
    top_rim = ndimage.binary_dilation(bangs, iterations=8) & ~bangs & (yy < 255)
    hf |= top_rim & alpha & ~claimed & ~skinlike & (hairlike | outline)
    # anti-aliased edge pixels (half hair, half skin) stay with the hair
    hf |= ndimage.binary_dilation(hf) & ~skinlike & alpha & ~claimed & (bangs | face_poly) & (yy < 300)
    claim("hair_front", hf)
    for side in ("R", "L"):
        claim(f"hair_side_{side}", poly_mask(POLY[f"hair_side_{side}"], shape) & ~skinlike)
    # skin that sat inside the hair polygons, right next to the face, belongs to the face.
    # It must stay inside the face's bounding box: the rig spec positions the whole
    # head relative to the face rect, so the face must not grow.
    forehead = poly_mask([(204, 200), (370, 200), (370, 296), (204, 296)], shape)
    ys, xs = np.nonzero(face_poly | forehead)
    in_box = np.zeros(shape, bool)
    in_box[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1] = True
    near_face = ndimage.binary_dilation(face_poly, iterations=10)
    skin_extra = (bangs | sides) & skinlike & alpha & ~claimed & in_box & near_face
    claim("face", face_poly | skin_extra)
    claim("tail", poly_mask(POLY["tail"], shape))
    claim("torso", poly_mask(POLY["torso"], shape))
    claim("hair_back", np.ones(shape, bool))

    layers: dict[str, np.ndarray] = {}

    def rgba(color_img, m):
        out = np.zeros(shape + (4,), np.uint8)
        out[..., :3] = color_img
        out[..., 3] = (m * 255).astype(np.uint8)
        return out

    for name, m in masks.items():
        if name.startswith(("iris_", "eyelash_")):
            continue
        layers[name] = rgba(rgb, m)

    skin = np.median(rgb[masks["face"] & ~hairlike & (rgb.mean(-1) > 200)], axis=0).astype(np.uint8)

    # face: fill eyes, mouth, and the forehead hidden under the bangs with skin
    # extend the face up under the bangs (forehead), not sideways past the cheek outline
    face_full = ndimage.binary_closing(face_poly | skin_extra | forehead, iterations=2)
    face_full = ndimage.binary_fill_holes(face_full) & in_box
    face_img = rgb.copy()
    face_img[face_full & ~masks["face"]] = skin  # under eyes, mouth, brows and hair: plain skin
    layers["face"] = rgba(face_img, face_full)

    # eyes
    for side in ("R", "L"):
        p = eye_parts[side]
        white_img = np.full_like(rgb, 250)
        white_img[..., 2] = 252
        layers[f"eye_white_{side}"] = rgba(white_img, p["white"])
        layers[f"iris_{side}"] = rgba(rgb, p["iris"] & alpha)
        layers[f"eyelash_{side}"] = rgba(rgb, p["lash"] & alpha)
        # closed eye: a soft downward arc near the lower lid
        (cx, cy), rx, ry = EYE_WHITE[side]
        img = Image.new("L", (shape[1], shape[0]), 0)
        ImageDraw.Draw(img).arc([cx - rx - 2, cy - ry, cx + rx + 2, cy + ry * 0.9], 25, 155, fill=255, width=3)
        m = np.asarray(img) > 0
        closed = np.zeros(shape + (4,), np.uint8)
        closed[..., :3] = (30, 22, 26)
        closed[..., 3] = (m * 255).astype(np.uint8)
        layers[f"eye_closed_{side}"] = closed

    # neck: drawn under the chin so looking up does not open a hole
    img = Image.new("RGBA", (shape[1], shape[0]), (0, 0, 0, 0))
    shade = tuple(int(c * 0.9) for c in skin)
    ImageDraw.Draw(img).polygon([(262, 318), (298, 318), (303, 390), (257, 390)], fill=shade + (255,))
    layers["neck"] = np.asarray(img).copy()

    # mouth interior (drawn, not cut): dark red rounded shape, opened by the rig
    img = Image.new("RGBA", (shape[1], shape[0]), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([268, 338, 292, 356], fill=(150, 40, 60, 255), outline=(60, 20, 30, 255), width=2)
    d.ellipse([273, 348, 287, 356], fill=(225, 110, 120, 255))  # tongue
    layers["mouth_inner"] = np.asarray(img).copy()

    # fill holes behind moved parts so turning / swinging does not show gaps
    def inpaint(name, region):
        img = layers[name]
        hole = (region & (img[..., 3] == 0)).astype(np.uint8)
        known = img[..., 3] > 0
        color = cv2.inpaint(np.ascontiguousarray(img[..., :3]), (~known).astype(np.uint8) * 255, 7, cv2.INPAINT_TELEA)
        img[..., :3] = np.where(hole[..., None] > 0, color, img[..., :3])
        img[..., 3] = np.maximum(img[..., 3], hole * 255)

    inpaint("torso", poly_mask(POLY["torso"], shape) & alpha)  # under the arms
    head = poly_mask(POLY["hair_front"], shape) | face_poly | poly_mask(POLY["hair_side_R"], shape) | poly_mask(POLY["hair_side_L"], shape)
    # back hair continues behind everything on the head and the upper body. Start from
    # the silhouette without the parts that stick out of it (antlers, ears, tail, ...);
    # filling holes then covers what sat on top of the hair without growing past it.
    sticking_out = np.zeros(shape, bool)
    for n in ("antler_R", "antler_L", "flower_R", "flower_L", "tail", "arm_R", "arm_L",
              "ear_R", "ear_L", "clip_R", "clip_L", "tassel_R"):
        sticking_out |= masks[n]
    behind = (alpha & ~sticking_out) | head | poly_mask([(150, 380), (420, 380), (420, 560), (150, 560)], shape)
    back_fill = ndimage.binary_fill_holes(behind) & ~masks["tail"]
    hb = layers["hair_back"]
    newly = back_fill & (hb[..., 3] == 0)
    hb[newly, :3] = HAIR_COLOR
    hb[newly, 3] = 255
    # inside the head, whatever was left over (bits of eyes, outlines) would peek out
    # when the face moves; replace it with plain hair colour
    inside_head = ndimage.binary_dilation(head | face_full, iterations=2) & (hb[..., 3] > 0)
    hb[inside_head, :3] = HAIR_COLOR
    # holes in the bangs where accessories were cut out
    inpaint("hair_front", bangs & ~face_full)
    return layers


def to_canvas(layer: np.ndarray) -> Image.Image:
    img = Image.fromarray(layer, "RGBA")
    # premultiply before resampling so transparent pixels do not darken edges
    pm = np.asarray(img, dtype=np.float32) / 255.0
    pm[..., :3] *= pm[..., 3:4]
    w, h = img.size
    big = cv2.resize(pm, (int(w * SCALE), int(h * SCALE)), interpolation=cv2.INTER_CUBIC)
    big = np.clip(big, 0, 1)
    a = big[..., 3:4]
    big[..., :3] = np.where(a > 1e-4, big[..., :3] / np.maximum(a, 1e-4), 0)
    out = np.zeros((CANVAS, CANVAS, 4), np.float32)
    ox, oy = int(round(OFFSET[0])), int(round(OFFSET[1]))
    bh, bw = big.shape[:2]
    out[oy : oy + bh, ox : ox + bw] = big[: CANVAS - oy, : CANVAS - ox]
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8), "RGBA")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sheet", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    layers = make_layers(args.sheet)
    for name, layer in layers.items():
        to_canvas(layer).save(args.out / f"{name}.png")
    print(f"wrote {len(layers)} layers to {args.out}")


if __name__ == "__main__":
    main()
