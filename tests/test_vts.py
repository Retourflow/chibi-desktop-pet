import socket
import time

import pytest

pytest.importorskip("websockets")

from chibi_pet.drive.vts import (DENIED, NO_FACE, TRACKING, WAITING, Mapping, Snapshot, TrackingDriver,
                                 VTSClient, blend)

from fake_vts import FakeVTS


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_mapping_ranges_and_averaging():
    m = Mapping.load()
    out = m.apply({"FaceAngleX": 15, "MouthSmile": 1.0, "EyeLeftX": 0.2, "EyeRightX": 0.6, "FaceAngleY": 99})
    assert out["ParamAngleX"] == pytest.approx(15)
    assert out["ParamBodyAngleX"] == pytest.approx(5)  # -30..30 -> -10..10
    assert out["ParamMouthForm"] == pytest.approx(1.0)  # 0..1 -> -1..1
    assert out["ParamEyeBallX"] == pytest.approx(0.4)  # average of both eyes
    assert out["ParamAngleY"] == pytest.approx(30)  # clamped
    assert "ParamMouthOpenY" not in out  # input missing -> parameter left alone


def test_client_authenticates_once_and_polls(tmp_path):
    token_file = tmp_path / "token"
    with FakeVTS() as vts:
        c = VTSClient(port=vts.port, host="127.0.0.1", token_file=token_file, poll_hz=50)
        c.start()
        try:
            assert wait_for(lambda: c.snapshot().status == TRACKING), c.last_error
            snap = c.snapshot()
            assert snap.face_found and snap.values["FaceAngleX"] == 12.0 and snap.age < 1.0
            assert token_file.read_text() == vts.token
            vts.values["FaceAngleX"] = -20.0
            assert wait_for(lambda: c.snapshot().values.get("FaceAngleX") == -20.0)
            vts.face_found = False
            assert wait_for(lambda: c.snapshot().status == NO_FACE, timeout=3)
        finally:
            c.stop()
        # a second session reuses the saved token: no new popup in VTS
        c2 = VTSClient(port=vts.port, host="127.0.0.1", token_file=token_file, poll_hz=50)
        c2.start()
        try:
            assert wait_for(lambda: c2.snapshot().values != {})
        finally:
            c2.stop()
        assert vts.token_requests == 1


def test_client_reports_denied(tmp_path):
    with FakeVTS(deny=True) as vts:
        c = VTSClient(port=vts.port, host="127.0.0.1", token_file=tmp_path / "token")
        c.start()
        try:
            assert wait_for(lambda: c.status == DENIED), c.last_error
        finally:
            c.stop()


def test_client_waits_when_vts_is_not_running(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens on this port now
    c = VTSClient(port=port, host="127.0.0.1", token_file=tmp_path / "token")
    c.start()
    try:
        time.sleep(0.5)
        assert c.status == WAITING and c.running
    finally:
        c.stop()
    assert not c.running


def test_driver_fades_in_and_out():
    d = TrackingDriver(Mapping.load())
    live = Snapshot(TRACKING, True, {"FaceAngleX": 30.0, "MouthOpen": 1.0}, age=0.01)
    for _ in range(60):  # one second at 60 fps
        values, w = d.update(live, 1 / 60)
    assert w > 0.99 and values["ParamAngleX"] == pytest.approx(30, abs=0.1)
    lost = Snapshot(NO_FACE, False, live.values, age=0.01)
    for _ in range(60):
        values, w = d.update(lost, 1 / 60)
    assert w < 0.01


def test_blend():
    base = {"ParamAngleX": 10.0, "ParamEyeLOpen": 1.0}
    tracked = {"ParamAngleX": 20.0, "ParamMouthOpenY": 1.0}
    out = blend(base, tracked, 0.5)
    assert out["ParamAngleX"] == 15.0 and out["ParamEyeLOpen"] == 1.0 and out["ParamMouthOpenY"] == 0.5
    assert blend(base, tracked, 0.0) is base
