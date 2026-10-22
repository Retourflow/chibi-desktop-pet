"""Bend modes, depth recipes and two-chain blending (rig features used by the back hair,
bangs and head)."""

import numpy as np
import pytest

from chibi_pet.rig.deformers import BendDeformer
from chibi_pet.rig.runtime import _blend_weights, _depth
from chibi_pet.rig.spec import Resolver


def _bend(**kw):
    return BendDeformer("b", None, root=np.array([0.0, 0.0]), tip=np.array([0.0, 100.0]),
                        segments=[{"s1": 1.0}, {"s2": 1.0}], **kw)


def test_shear_shifts_each_cross_section_level():
    d = _bend(mode="shear")
    # two points on the same cross-section (s = 60), far apart sideways
    pts = np.array([[-40.0, 60.0], [40.0, 60.0], [0.0, -10.0], [0.0, 0.0]])
    out = d.apply(pts, np.zeros(4), {"s1": 20.0, "s2": 20.0})
    moved = out - pts
    np.testing.assert_allclose(moved[0], moved[1], atol=1e-9)  # the same shift: stays level
    assert moved[0, 0] > 5  # swung to the right
    np.testing.assert_allclose(out[2:], pts[2:], atol=1e-9)  # above and at the root: untouched
    # a plain bend turns the same cross-section instead: one end rises, the other drops
    turned = _bend().apply(pts, np.zeros(4), {"s1": 20.0, "s2": 20.0})
    assert abs((turned[0] - turned[1])[1]) > 10


def test_falloff_fades_the_bend_out_sideways():
    d = _bend(mode="shear", falloff=(10.0, 30.0))
    pts = np.array([[0.0, 80.0], [9.0, 80.0], [20.0, 80.0], [30.0, 80.0], [-45.0, 80.0]])
    moved = d.apply(pts, np.zeros(5), {"s1": 20.0, "s2": 20.0}) - pts
    full = moved[0, 0]
    assert full > 5
    assert moved[1, 0] == pytest.approx(full)  # inside the inner radius: all of it
    assert 0 < moved[2, 0] < full  # in between: part of it
    np.testing.assert_allclose(moved[3:], 0, atol=1e-9)  # at and beyond the outer radius: none


def test_bad_bend_settings_are_rejected():
    with pytest.raises(ValueError):
        _bend(mode="twist")
    with pytest.raises(ValueError):
        _bend(falloff=(30.0, 10.0))


RES = Resolver({"face": (100, 100, 200, 100), "ear": (0, 0, 10, 10)}, (400, 400))


def test_depth_bump_and_like():
    rest = np.array([[200.0, 150.0], [260.0, 150.0], [200.0, 400.0]])
    bump = {"type": "bump", "center": {"part": "face", "at": [0.5, 0.5]}, "sigma": 60.0, "amplitude": 30.0}
    z = _depth(bump, rest, RES, "face")
    assert z[0] == pytest.approx(30.0)  # highest at the centre
    assert z[1] == pytest.approx(30.0 * np.exp(-1.0))  # sigma away
    assert z[2] < 1e-6  # fades out far away
    specs = {"face": {"depth": bump}}
    np.testing.assert_allclose(_depth("face", rest, RES, "ear", specs), z)  # the face's field
    like = {"like": "face", "scale": 0.5, "offset": 2.0}
    np.testing.assert_allclose(_depth(like, rest, RES, "ear", specs), z * 0.5 + 2.0)


def test_depth_like_cycle_is_an_error():
    specs = {"a": {"depth": "b"}, "b": {"depth": "a"}}
    with pytest.raises(ValueError):
        _depth("a", np.zeros((1, 2)), RES, "x", specs)


def test_blend_weights_change_over_smoothly_between_from_and_to():
    spec = {"from": [0.0, 100.0], "to": [0.0, 200.0]}
    rest = np.array([[50.0, 50.0], [0.0, 100.0], [0.0, 150.0], [-30.0, 200.0], [0.0, 300.0]])
    w = _blend_weights(spec, rest, RES, "hair")
    np.testing.assert_allclose(w, [0.0, 0.0, 0.5, 1.0, 1.0], atol=1e-12)
