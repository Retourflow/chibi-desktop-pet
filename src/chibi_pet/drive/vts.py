"""VTube Studio as the face-tracking source, through its plugin WebSocket API.

VTube Studio (VTS) does the tracking (webcam or phone). We only read its
tracking values; our own window draws the model. VTS cannot load our model,
but it does not need to: the tracking values are there whatever model VTS shows.

Protocol (from the official API docs, github.com/DenchiSoft/VTubeStudio):

    connect ws://localhost:8001            (the user must turn on "Allow Plugin API access" in VTS)
    AuthenticationTokenRequest             first run only: VTS shows a popup, the user clicks Allow,
                                           we save the token to a file
    AuthenticationRequest + token          every session
    InputParameterListRequest              all tracking values (FaceAngleX, MouthOpen, ...);
                                           the docs advise against 60+ per second, we poll ~30
    FaceFoundRequest                       is a face visible right now

Pieces:
    VTSClient       background thread: connect, authenticate, poll, reconnect. A missing or slow
                    VTS never blocks the 60 fps window; it reads `snapshot()`.
    Mapping         VTS values -> our rig parameters (assets/vts_mapping.json)
    TrackingDriver  on the window side: smooths the ~30 Hz samples to 60 fps and fades between
                    face tracking and the mouse/idle animation when the face is found or lost

Check what your VTS sends (names, ranges, directions):
    python -m chibi_pet.drive.vts --dump
"""

from __future__ import annotations

import argparse
import itertools
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .idle import approach

try:
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect as ws_connect
except ImportError:  # pragma: no cover - only needed when face tracking is used
    ws_connect = None
    WebSocketException = Exception

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MAPPING = PROJECT_ROOT / "assets" / "vts_mapping.json"
DEFAULT_TOKEN_FILE = PROJECT_ROOT / ".vts_token"

PLUGIN_NAME = "Chibi Desktop Pet"  # shown in the VTS permission popup (3-32 characters)
PLUGIN_DEVELOPER = "Retourflow"
ERROR_USER_DENIED = 50  # VTS ErrorID.UserDeniedAPIAccess


# -- mapping -------------------------------------------------------------------


@dataclass
class Channel:
    param: str
    inputs: tuple[str, ...]
    in_range: tuple[float, float]
    out_range: tuple[float, float]

    def apply(self, values: dict[str, float]) -> float | None:
        got = [values[n] for n in self.inputs if n in values]
        if not got:
            return None
        x = sum(got) / len(got)
        a, b = self.in_range
        t = min(max((x - a) / (b - a), 0.0), 1.0)
        lo, hi = self.out_range
        return lo + (hi - lo) * t


class Mapping:
    def __init__(self, channels: list[Channel]):
        self.channels = channels

    @classmethod
    def load(cls, path: str | Path = DEFAULT_MAPPING) -> "Mapping":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Channel(c["param"], tuple(c["inputs"]), tuple(c["in"]), tuple(c["out"]))
                    for c in data["channels"]])

    def apply(self, values: dict[str, float]) -> dict[str, float]:
        out = {}
        for ch in self.channels:
            v = ch.apply(values)
            if v is not None:
                out[ch.param] = v
        return out


# -- client --------------------------------------------------------------------


class VTSError(RuntimeError):
    def __init__(self, error_id: int, message: str):
        super().__init__(f"VTS error {error_id}: {message}")
        self.error_id = error_id


class AuthDenied(RuntimeError):
    pass


# status values, shown in the tray tooltip
OFF, WAITING, ASK_USER, NO_FACE, TRACKING, DENIED = "off", "waiting", "ask_user", "no_face", "tracking", "denied"


@dataclass
class Snapshot:
    status: str
    face_found: bool
    values: dict[str, float] = field(default_factory=dict)
    age: float = float("inf")  # seconds since the values were received


class VTSClient:
    def __init__(self, port: int = 8001, host: str = "localhost", token_file: str | Path = DEFAULT_TOKEN_FILE,
                 poll_hz: float = 30.0, connect=None):
        self.url = f"ws://{host}:{port}"
        self.token_file = Path(token_file)
        self.poll_dt = 1.0 / poll_hz
        self._connect = connect or ws_connect
        if self._connect is None:
            raise RuntimeError("the 'websockets' package is not installed (pip install websockets)")
        self._lock = threading.Lock()
        self._values: dict[str, float] = {}
        self._stamp = 0.0
        self._face = False
        self.status = OFF
        self.last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws = None
        self._ids = itertools.count()

    # public, called from the window thread ----------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.status = WAITING
        self._thread = threading.Thread(target=self._run, name="vts-client", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)
        self.status = OFF

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> Snapshot:
        with self._lock:
            age = time.monotonic() - self._stamp if self._stamp else float("inf")
            return Snapshot(self.status, self._face, dict(self._values), age)

    # background thread ---------------------------------------------------------

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                # proxy=None: VTS runs on this PC; a system proxy (e.g. a VPN tool) must not be used
                with self._connect(self.url, open_timeout=3, close_timeout=1, proxy=None) as ws:
                    self._ws = ws
                    self._authenticate(ws)
                    backoff = 1.0
                    self._poll(ws)
            except AuthDenied as e:
                self.status, self.last_error = DENIED, str(e)
                backoff = 10.0
            except (OSError, TimeoutError, WebSocketException, VTSError) as e:
                # VTS not running, API access turned off, or the connection dropped
                if self.status != DENIED:
                    self.status = WAITING
                self.last_error = f"{type(e).__name__}: {e}"
            finally:
                self._ws = None
                with self._lock:
                    self._face = False
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 10.0)

    def _request(self, ws, message_type: str, data: dict | None = None, timeout: float = 5.0) -> dict:
        rid = f"pet-{next(self._ids)}"
        ws.send(json.dumps({"apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0",
                            "requestID": rid, "messageType": message_type, "data": data or {}}))
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no answer to {message_type}")
            msg = json.loads(ws.recv(timeout=left))
            if msg.get("requestID") != rid:
                continue  # an event or an old answer
            if msg.get("messageType") == "APIError":
                d = msg.get("data", {})
                raise VTSError(d.get("errorID", -1), d.get("message", ""))
            return msg.get("data", {})

    def _authenticate(self, ws) -> None:
        who = {"pluginName": PLUGIN_NAME, "pluginDeveloper": PLUGIN_DEVELOPER}
        token = self.token_file.read_text(encoding="utf-8").strip() if self.token_file.exists() else ""
        if token:
            r = self._request(ws, "AuthenticationRequest", {**who, "authenticationToken": token})
            if r.get("authenticated"):
                return
        # first run, or the token was revoked: VTS pops up a dialog and waits for the user
        self.status = ASK_USER
        try:
            r = self._request(ws, "AuthenticationTokenRequest", who, timeout=120.0)
        except VTSError as e:
            if e.error_id == ERROR_USER_DENIED:
                raise AuthDenied("access denied in VTube Studio") from e
            raise
        token = r["authenticationToken"]
        self.token_file.write_text(token, encoding="utf-8")
        r = self._request(ws, "AuthenticationRequest", {**who, "authenticationToken": token})
        if not r.get("authenticated"):
            raise AuthDenied(r.get("reason", "authentication failed"))

    def _poll(self, ws) -> None:
        next_face_check = 0.0
        face = False
        while not self._stop.is_set():
            t0 = time.monotonic()
            r = self._request(ws, "InputParameterListRequest")
            values = {p["name"]: float(p["value"])
                      for p in r.get("defaultParameters", []) + r.get("customParameters", [])}
            if t0 >= next_face_check:
                face = bool(self._request(ws, "FaceFoundRequest").get("found"))
                next_face_check = t0 + 0.5
            with self._lock:
                self._values, self._stamp, self._face = values, time.monotonic(), face
            self.status = TRACKING if face else NO_FACE
            self._stop.wait(max(0.0, self.poll_dt - (time.monotonic() - t0)))


# -- window side -----------------------------------------------------------------


class TrackingDriver:
    """Smooth VTS samples into per-frame parameters, plus a 0..1 weight for blending.

    weight 1 = face tracking drives the head, eyes, mouth and brows;
    weight 0 = the mouse follow and idle animation do (no face, VTS closed, tracking off).
    """

    def __init__(self, mapping: Mapping, rate: float = 20.0, fade: float = 5.0, max_age: float = 0.5):
        self.mapping = mapping
        self.rate = rate  # smoothing toward new samples (1/s)
        self.fade = fade  # speed of the switch between tracking and mouse/idle (1/s)
        self.max_age = max_age  # samples older than this count as lost
        self.values: dict[str, float] = {}
        self.weight = 0.0

    def update(self, snap: Snapshot | None, dt: float) -> tuple[dict[str, float], float]:
        live = snap is not None and snap.face_found and snap.age < self.max_age and bool(snap.values)
        if live:
            for k, v in self.mapping.apply(snap.values).items():
                self.values[k] = approach(self.values.get(k, v), v, self.rate, dt)
        self.weight = approach(self.weight, 1.0 if live else 0.0, self.fade, dt)
        if self.weight < 1e-3:
            self.weight = 0.0
        return dict(self.values), self.weight


def blend(base: dict[str, float], tracked: dict[str, float], weight: float,
          defaults: dict[str, float] | None = None) -> dict[str, float]:
    """base * (1 - weight) + tracked * weight, key by key."""
    if weight <= 0.0:
        return base
    out = dict(base)
    defaults = defaults or {}
    for k, v in tracked.items():
        b = base.get(k, defaults.get(k, 0.0))
        out[k] = b + (v - b) * weight
    return out


# -- command line check -------------------------------------------------------------

WATCH = ["FaceAngleX", "FaceAngleY", "FaceAngleZ", "EyeOpenLeft", "EyeOpenRight",
         "EyeLeftX", "EyeLeftY", "MouthOpen", "MouthSmile", "Brows"]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Check the connection to VTube Studio and print its tracking values.")
    ap.add_argument("--dump", action="store_true", help="print every parameter VTS sends, then watch the main ones")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--seconds", type=float, default=20.0, help="how long to watch")
    args = ap.parse_args(argv)

    client = VTSClient(port=args.port)
    client.start()
    print(f"connecting to {client.url} ... (in VTS: Settings > 'Allow Plugin API access' must be on)", flush=True)
    t_end = time.monotonic() + 60
    shown = None
    while time.monotonic() < t_end:
        snap = client.snapshot()
        if snap.status != shown:
            shown = snap.status
            msg = {WAITING: "waiting for VTube Studio", ASK_USER: "click 'Allow' in the VTube Studio popup",
                   DENIED: "access was denied in VTube Studio", NO_FACE: "connected, but no face found",
                   TRACKING: "connected, tracking"}.get(snap.status, snap.status)
            print(f"[{snap.status}] {msg}  {client.last_error}", flush=True)
        if snap.values:
            break
        time.sleep(0.2)
    snap = client.snapshot()
    if not snap.values:
        print("no tracking values received")
        client.stop()
        return
    if args.dump:
        print("\nall parameters VTS sends:")
        for name in sorted(snap.values):
            print(f"  {name:32s} {snap.values[name]: .3f}")
    mapping = Mapping.load()
    print("\nwatching (turn your head, blink, open your mouth; Ctrl+C to stop):")
    t_end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < t_end:
            snap = client.snapshot()
            line = "  ".join(f"{n}={snap.values[n]:6.2f}" for n in WATCH if n in snap.values)
            ours = mapping.apply(snap.values)
            print(f"face={'Y' if snap.face_found else 'N'} {line} | ParamAngleX={ours.get('ParamAngleX', 0):6.1f}",
                  flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    client.stop()


if __name__ == "__main__":
    main()
