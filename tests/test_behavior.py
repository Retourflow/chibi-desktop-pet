import random

import pytest

from chibi_pet.app.settings import DEFAULTS, Settings
from chibi_pet.drive.behavior import BODY, HEAD, Dozer, LipSync, ReactionPlayer, classify_click

FACE = (100.0, 100.0, 200.0, 160.0)  # x, y, w, h


def run(layer, seconds, dt=1 / 60):
    out = []
    for _ in range(int(seconds / dt)):
        out.append(layer.update(dt))
    return out


def test_classify_click():
    assert classify_click((150, 50), FACE) == (HEAD, -1)  # hair above the face
    assert classify_click((250, 200), FACE) == (HEAD, 1)
    assert classify_click((260, 300), FACE) == (BODY, 1)  # below the chin
    assert classify_click((120, 400), FACE) == (BODY, -1)


@pytest.mark.parametrize("kind", ["pat", "poke", "wake"])
def test_reactions_fade_in_and_end(kind):
    r = ReactionPlayer()
    r.play(kind, side=1)
    frames = run(r, 2.0)
    weights = [w for _, w in frames]
    assert max(weights) == pytest.approx(1.0)
    assert weights[0] < 0.5 and weights[-1] == 0.0 and not r.active
    for values, _ in frames:
        for k, v in values.items():
            if "Open" in k or "MouthForm" in k or "EyeBall" in k or "Brow" in k:
                assert -1.0 <= v <= 1.0, (k, v)


def test_pat_is_a_squint_and_poke_leans_away():
    r = ReactionPlayer()
    r.play("pat")
    values, _ = run(r, 0.3)[-1]
    assert values["ParamEyeLOpen"] < 0.5 and values["ParamMouthForm"] > 0.5
    r.play("poke", side=1)  # poked on the right -> body tilts counter-clockwise (+)
    values, _ = run(r, 0.05)[-1]
    assert values["ParamBodyAngleZ"] > 0


def test_patting_again_does_not_restart_from_zero():
    r = ReactionPlayer()
    r.play("pat")
    run(r, 1.0)
    r.play("pat")
    _, w = r.update(1 / 60)
    assert w == pytest.approx(1.0)


def test_dozer_falls_asleep_and_wakes():
    d = Dozer(after_seconds=2.0, rng=random.Random(1))
    frames = run(d, 1.5)
    assert frames[-1][1] == 0.0 and not d.asleep
    d.activity()  # mouse moved: timer restarts
    run(d, 1.5)
    assert not d.asleep
    frames = run(d, 6.0)
    assert d.asleep and frames[-1][1] > 0.95 and frames[-1][0]["ParamEyeLOpen"] == 0.0
    d.activity()  # moving the mouse does not wake a sleeping pet
    assert d.asleep
    assert d.wake() is True and not d.asleep
    frames = run(d, 1.0)
    assert frames[-1][1] == 0.0


def test_dozer_off():
    d = Dozer(after_seconds=0)
    run(d, 10.0)
    assert not d.asleep


def test_lipsync_vowels_and_release():
    ls = LipSync()
    for _ in range(20):
        ls.feed_vowel("a")
        values, w = ls.update(1 / 60)
    assert values["ParamMouthOpenY"] > 0.9 and values["ParamMouthA"] == 1.0 and w > 0.9
    for _ in range(20):
        ls.feed_vowel("i")
        values, _ = ls.update(1 / 60)
    assert values["ParamMouthForm"] > 0.9 and values["ParamMouthI"] == 1.0 and values["ParamMouthA"] == 0.0
    frames = run(ls, 1.5)  # input stops: the mouth closes and the layer lets go
    assert frames[-1][1] == 0.0
    with pytest.raises(ValueError):
        ls.feed_vowel("x")


def test_settings_roundtrip_and_bad_file(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(path)
    assert s.data == DEFAULTS
    s.set("size", "大")
    s.set("pos", [10, 20])
    s2 = Settings(path)
    assert s2["size"] == "大" and s2["pos"] == [10, 20] and s2["tracking"] is False
    path.write_text("{ not json", encoding="utf-8")
    assert Settings(path).data == DEFAULTS
