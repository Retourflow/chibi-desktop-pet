import random

from chibi_pet.drive.idle import Blinker, IdleAnimator, approach
from chibi_pet.drive.mouse import MouseFollow


def test_approach_is_frame_rate_independent():
    a = 0.0
    for _ in range(60):
        a = approach(a, 1.0, 3.0, 1 / 60)
    b = 0.0
    for _ in range(10):
        b = approach(b, 1.0, 3.0, 1 / 10)
    assert abs(a - b) < 1e-9


def test_blinker_closes_and_reopens():
    b = Blinker(random.Random(1))
    values = [b.update(1 / 60) for _ in range(60 * 10)]
    assert min(values) == 0.0 and values[-1] <= 1.0
    closed_frames = sum(v < 0.3 for v in values)
    assert 0 < closed_frames < 60  # a few short blinks, not closed for seconds


def test_idle_values_in_range():
    idle = IdleAnimator(random.Random(2))
    for _ in range(300):
        v = idle.update(1 / 30)
        assert 0 <= v["ParamBreath"] <= 1 and 0 <= v["ParamEyeLOpen"] <= 1


def test_mouse_follow_turns_toward_cursor():
    m = MouseFollow()
    for _ in range(120):
        v = m.update((2000, -500), (500, 500), 1 / 60)
    assert v["ParamAngleX"] > 20  # cursor far right -> turn right
    assert v["ParamAngleY"] > 15  # cursor above -> look up
    assert v["ParamEyeBallX"] > 0.9
