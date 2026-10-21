import math

import numpy as np
import pytest

from chibi_pet.rig.deformers import BendDeformer, RotationDeformer, Turn3DDeformer, rotate
from chibi_pet.rig.keyform import KeyformGroup, key_weights
from chibi_pet.rig.physics import PendulumChain, PhysicsInput, PhysicsOutput


def test_rotate_is_counter_clockwise_on_screen():
    # +90 deg turns "right" (1, 0) into "up" (0, -1) when y points down
    np.testing.assert_allclose(rotate(np.array([1.0, 0.0]), 90), [0, -1], atol=1e-12)


def test_key_weights_interpolate_and_clamp():
    keys = np.array([0.0, 1.0, 3.0])
    assert key_weights(keys, -5) == [(0, 1.0)]
    assert key_weights(keys, 9) == [(2, 1.0)]
    (i0, w0), (i1, w1) = key_weights(keys, 2.0)
    assert (i0, i1) == (1, 2) and w0 == pytest.approx(0.5) and w1 == pytest.approx(0.5)


def test_keyform_bilinear_blend():
    n = 1
    offsets = np.zeros((2, 2, n, 2))
    offsets[1, 0, 0] = [10, 0]  # param a at max
    offsets[0, 1, 0] = [0, 20]  # param b at max
    offsets[1, 1, 0] = [10, 20]
    g = KeyformGroup(["a", "b"], [np.array([0.0, 1.0]), np.array([0.0, 1.0])], offsets, np.ones((2, 2)))
    off, op = g.evaluate({"a": 0.5, "b": 0.25})
    np.testing.assert_allclose(off[0], [5, 5])
    assert op == pytest.approx(1.0)


def test_rotation_deformer():
    d = RotationDeformer("r", None, origin=np.array([10.0, 10.0]), angle={"p": 1.0},
                         translate={"t": np.array([2.0, 0.0])})
    pts = np.array([[20.0, 10.0]])
    out = d.apply(pts, np.zeros(1), {"p": 90.0, "t": 1.0})
    np.testing.assert_allclose(out[0], [12.0, 0.0], atol=1e-9)


def test_turn3d_identity_at_zero_and_parallax():
    d = Turn3DDeformer("h", None, center=np.array([0.0, 0.0]), yaw_scale=1.0)
    pts = np.array([[0.0, 0.0], [0.0, 0.0]])
    z = np.array([0.0, 10.0])
    np.testing.assert_allclose(d.apply(pts, z, {}), pts)
    out = d.apply(pts, z, {"ParamAngleX": 30.0})
    assert out[0, 0] == pytest.approx(0.0)
    assert out[1, 0] == pytest.approx(10 * math.sin(math.radians(30)))  # deeper point moves more


def test_bend_identity_and_root_fixed():
    d = BendDeformer("b", None, root=np.array([0.0, 0.0]), tip=np.array([0.0, 100.0]),
                     segments=[{"s1": 1.0}, {"s2": 1.0}])
    pts = np.array([[0.0, 0.0], [5.0, 50.0], [0.0, 100.0], [0.0, -20.0]])
    np.testing.assert_allclose(d.apply(pts, np.zeros(4), {}), pts)
    out = d.apply(pts, np.zeros(4), {"s1": 20.0, "s2": 20.0})
    np.testing.assert_allclose(out[0], [0, 0], atol=1e-9)  # root stays
    np.testing.assert_allclose(out[3], [0, -20], atol=1e-9)  # above the root: untouched
    assert out[2, 0] > 10  # positive angle swings a hanging tip to the right
    # length along the chain is preserved (approximately)
    assert np.hypot(*out[2]) == pytest.approx(100, rel=0.05)


def _chain(**kw):
    return PendulumChain("c", [PhysicsInput("x", "x", 1.0), PhysicsInput("tilt", "angle", 1.0)],
                         [PhysicsOutput("o0", 0), PhysicsOutput("o1", 1)], segments=2, **kw)


def test_physics_rest_is_stable():
    ch = _chain()
    for _ in range(120):
        out = ch.step({}, 1 / 60)
    assert abs(out["o0"]) < 1e-6 and abs(out["o1"]) < 1e-6


def test_physics_swings_then_settles():
    ch = _chain(damping=0.15)
    out = ch.step({"x": 1.0}, 1 / 60)  # jerk the root to the right
    assert out["o0"] < -1  # the chain lags behind: it points back to the left
    for _ in range(600):
        out = ch.step({"x": 1.0}, 1 / 60)
    assert abs(out["o0"]) < 0.5


def test_physics_cancels_head_roll():
    ch = _chain(damping=0.3)
    for _ in range(900):
        out = ch.step({"tilt": 20.0}, 1 / 60)
    assert out["o0"] == pytest.approx(-20.0, abs=0.5)  # hangs straight down in the world
