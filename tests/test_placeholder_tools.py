"""Tests for the placeholder-art tool's drawing helpers, on small synthetic layers
(no character art involved)."""

import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("scipy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import make_placeholder as mp  # noqa: E402

INK = np.array([10, 10, 10], np.uint8)
SKIN = np.array([240, 190, 180], np.uint8)
RED = np.array([200, 40, 40], np.uint8)


def _bar(shape=(60, 80)):
    """A horizontal 'ear': a bar of skin with an ink outline top and bottom, a red line
    above the bottom outline, running from x=10 to the cover that starts at x=40."""
    img = np.zeros(shape + (3,), np.uint8)
    own = np.zeros(shape, bool)
    own[20:32, 10:40] = True
    img[20:32, 10:40] = SKIN
    img[20:22, 10:40] = INK
    img[30:32, 10:40] = INK
    img[29, 10:40] = RED
    cover = np.zeros(shape, bool)
    cover[5:55, 40:70] = True
    return img, own, cover


def test_extrude_cut_sweeps_the_root_straight_on():
    img, own, cover = _bar()
    out, mask, depth = mp.extrude_cut(img, own, cover, length=12)
    rows, cols = np.nonzero(mask)
    # a straight band as wide as the root, reaching `length` px in under the cover
    assert rows.min() == 20 and rows.max() == 31
    assert cols.min() == 40 and cols.max() == 51
    assert mask[20:32, 40:52].all()
    # every line across keeps the root's colour: the outlines go on as lines
    assert (out[20:22, 40:52] == INK).all()
    assert (out[29, 40:52] == RED).all()
    assert (out[25, 40:52] == SKIN).all()
    assert depth[25, 40] == pytest.approx(1.0) and depth[25, 51] == pytest.approx(12.0)


def test_extrude_cut_leaves_a_cover_running_along_the_side_alone():
    # an ear pointing right, its root under a lock that also lies along its top (the left
    # ear under its side lock): only the root goes on, under the lock's side
    img, own, _ = _bar()
    yy, xx = np.mgrid[0:60, 0:80]
    own &= np.abs(yy - 25.5) <= (46 - xx) * 0.75  # a point at the right-hand end
    cover = np.zeros(own.shape, bool)
    cover[5:55, 0:10] = True
    cover[5:20, 0:28] = True
    _, mask, _ = mp.extrude_cut(img, own, cover, length=8)
    rows, cols = np.nonzero(mask)
    assert cols.max() < 10  # nothing above the top edge
    assert rows.min() >= 20 and rows.max() <= 31
    assert mask[20:32, 2:10].all()


def test_to_canvas_contour_draws_ink_only_along_the_marked_hidden_edge():
    layer = np.zeros((60, 60, 4), np.uint8)
    layer[10:50, 10:50, :3] = SKIN
    layer[10:50, 10:50, 3] = 255
    contour = np.zeros((60, 60), bool)
    contour[10:50, 45:50] = True  # the right-hand edge (hidden under something at rest)
    plain = np.asarray(mp.to_canvas(layer, smooth=True)).astype(int)
    drawn = np.asarray(mp.to_canvas(layer, smooth=True, contour=contour)).astype(int)
    ox, oy = round(mp.OFFSET[0]), round(mp.OFFSET[1])

    def at(img, x, y):  # sheet px -> canvas px
        return img[oy + int((y + 0.5) * mp.SCALE), ox + int((x + 0.5) * mp.SCALE)]

    # ink right inside the marked edge, CONTOUR_WIDTH wide
    assert at(drawn, 48, 30)[:3].max() < 40
    assert at(drawn, 48, 30)[3] == 255
    # untouched well inside, and along the edges that were not marked
    assert np.abs(at(drawn, 30, 30) - at(plain, 30, 30)).max() <= 1
    assert np.abs(at(drawn, 12, 30) - at(plain, 12, 30)).max() <= 1
    # the band's inner side is a smooth line: the same distance in on every row
    band = drawn[oy + int(20 * mp.SCALE) : oy + int(40 * mp.SCALE), :, :3].max(-1) < 60
    inner = [np.flatnonzero(r).min() for r in band if r.any()]
    assert max(inner) - min(inner) <= 1


def test_to_canvas_does_not_ring_next_to_a_thin_red_line():
    layer = np.zeros((20, 20, 4), np.uint8)
    layer[..., :3] = 255
    layer[..., 3] = 255
    layer[:, 10, :3] = (220, 0, 0)
    out = np.asarray(mp.to_canvas(layer)).astype(int)
    ox, oy = round(mp.OFFSET[0]), round(mp.OFFSET[1])
    patch = out[oy : oy + int(20 * mp.SCALE), ox : ox + int(20 * mp.SCALE), :3]
    # never more blue/green than red (a cyan speck): only blends of red and white
    assert (patch[..., 0] >= patch[..., 1] - 1).all()
    assert (patch[..., 0] >= patch[..., 2] - 1).all()


def test_stroke_is_drawn_from_its_exact_outline():
    pts = np.stack([np.linspace(10, 30, 81), np.full(81, 10.0)], 1)
    stroke = mp.Stroke(pts, np.full(81, 3.0))
    m = stroke.mask((30, 40))
    assert m[10, 20] and m[12, 20] and not m[14, 20]
    layer = np.zeros((30, 40, 4), np.uint8)
    layer[m, :3] = INK
    layer[m, 3] = 255
    out = np.asarray(mp.to_canvas(layer, stroke=stroke))
    ox, oy = round(mp.OFFSET[0]), round(mp.OFFSET[1])
    col = out[:, ox + int(20.5 * mp.SCALE), 3]
    rows = np.flatnonzero(col > 127) - oy
    sheet_y = (rows + 0.5) / mp.SCALE - 0.5  # (canvas row centre -> sheet px, as the upscale maps it)
    # the edge sits where the stroke's outline is (10 +- 3 sheet px), to within a canvas px
    assert abs(sheet_y.min() - 7.0) < 1 / mp.SCALE and abs(sheet_y.max() - 13.0) < 1 / mp.SCALE


def test_comes_out_marks_only_what_slides_out_over_the_background():
    # a cover (rows 10-29, cols 20-59) with hair to its right on rows 10-19 and background
    # elsewhere: what is hidden under it near the right-hand edge may come out next to the
    # hair (rows 10-19), not below it (rows 20-29); deep inside, nothing comes out
    cover = np.zeros((40, 80), bool)
    cover[10:30, 20:60] = True
    hair = np.zeros_like(cover)
    hair[10:20, 60:75] = True
    bad_end = ~cover & ~hair
    out = mp.comes_out(cover, bad_end, (5, 3))
    assert not out[15, 50:60].any()  # next to the hair: it carries the hair on
    assert out[25, 55:60].all() and not out[25, 54]  # below the hair's end: 5 px reach
    assert out[27:30, 40].all() and not out[26, 40]  # above the background below: 3 px
    assert out[10:13, 40].all()  # (above, the background too)
    assert out[15, 20:25].all() and not out[15, 25:50].any()
    assert not out[~cover].any()
