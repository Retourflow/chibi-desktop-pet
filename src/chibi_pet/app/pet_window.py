"""The desktop pet window: frameless, transparent, always on top.

Each frame (about 60 per second):
  idle animation + mouse follow  ->  rig parameters
  face tracking (VTube Studio)   ->  blended over them while a face is tracked
  dozing, click reaction, lip sync -> blended on top (drive/behavior.py)
  physics step                   ->  hair / tail / tassel parameters
  rig.evaluate()                 ->  deformed meshes
  GLRenderer.render()            ->  RGBA image (offscreen GPU)
  paintEvent                     ->  the image is painted into the transparent window

Mouse: left-drag on the character moves the window; a left click without
dragging pats the head or pokes the body (or wakes the pet if it is dozing).
Right-click or the tray icon opens the menu: size, face tracking, dozing,
always-on-top, quit. Settings are remembered in .pet_settings.json.

Clicks on fully transparent pixels are ignored here; on Windows, translucent
top-level Qt windows are layered windows, which let those clicks fall through
to whatever is underneath (to be verified on the user's PC).
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from ..drive.behavior import HEAD, Dozer, LipSync, ReactionPlayer, classify_click
from ..drive.idle import IdleAnimator
from ..drive.mouse import MouseFollow
from ..drive.vts import ASK_USER, DENIED, NO_FACE, TRACKING, WAITING, Mapping, TrackingDriver, VTSClient, blend
from ..render.gl import GLRenderer
from ..rig.model import load_model
from ..rig.runtime import Rig
from ..rig.spec import load_spec
from .settings import Settings

SIZES = {"小": 0.18, "中": 0.25, "大": 0.32, "特大": 0.42}  # canvas px -> logical screen px
DOZE_CHOICES = {"1 分钟": 1, "3 分钟": 3, "10 分钟": 10, "不打瞌睡": 0}
MARGIN = 0.08  # extra room around the character for swinging hair / tail
DRAG_THRESHOLD = 5  # px the mouse must move before a press becomes a drag instead of a click
VTS_STATUS = {  # tray tooltip text for each face-tracking state
    WAITING: "面捕：等待 VTube Studio（需要打开 VTS，并在设置里开启 API）",
    ASK_USER: "面捕：请在 VTube Studio 弹窗里点「允许」",
    DENIED: "面捕：VTube Studio 拒绝了访问",
    NO_FACE: "面捕：已连接，没检测到脸",
    TRACKING: "面捕：追踪中",
}


class PetWindow(QWidget):
    def __init__(self, model_dir: str | Path, rig_path: str | Path, size: str | None = None, fps: int = 60,
                 tracking: bool | None = None, vts_port: int = 8001, settings: Settings | None = None):
        super().__init__(None)
        self.settings = settings or Settings()
        on_top = bool(self.settings["on_top"])
        self.setWindowTitle("chibi pet")
        flags = Qt.FramelessWindowHint | Qt.Tool
        if on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)

        self.model = load_model(model_dir)
        self.rig = Rig(self.model, load_spec(rig_path))
        atlas = np.asarray(Image.open(self.model.atlas_path).convert("RGBA"))

        # Area to render: union of all parts at rest, plus a margin.
        rects = np.array([p.rect for p in self.model.parts.values()], dtype=np.float64)
        x0, y0 = rects[:, 0].min(), rects[:, 1].min()
        x1, y1 = (rects[:, 0] + rects[:, 2]).max(), (rects[:, 1] + rects[:, 3]).max()
        m = MARGIN * max(x1 - x0, y1 - y0)
        self.view = (x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m)
        face = self.model.parts.get("face")
        self.face_rect = tuple(face.rect) if face else (x0, y0, x1 - x0, (y1 - y0) / 3)
        fr = self.face_rect
        self.head_canvas = (fr[0] + fr[2] / 2, fr[1] + fr[3] / 2)

        self.gl = GLRenderer(self.model, atlas, (1, 1))
        self.defaults = {k: p.default for k, p in self.rig.params.items()}
        self.idle = IdleAnimator()
        self.mouse = MouseFollow()
        self.vts_port = vts_port
        self.vts: VTSClient | None = None
        self.tracking = TrackingDriver(Mapping.load())
        self._vts_status = None
        self.dozer = Dozer(after_seconds=60.0 * float(self.settings["doze_minutes"]))
        self.reaction = ReactionPlayer()
        self.lipsync = LipSync()  # hook for TTS: self.lipsync.feed_level(x) / feed_vowel("a")

        size = size or self.settings["size"]
        self.scale = SIZES.get(size, SIZES["中"])
        self._apply_size()

        self._frame = None  # last rendered RGBA (kept alive while QImage uses it)
        self._image = None
        self._press: QPoint | None = None  # global position of a left press on the character
        self._drag_from: QPoint | None = None
        self._dragging = False
        self._last_cursor = None
        self._last = time.perf_counter()
        self._stats = [self._last, 0, 0.0]  # report start, frames, seconds spent in tick()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)  # coarse timers may round 16 ms up and cap the frame rate
        self.timer.timeout.connect(self.tick)
        self.timer.start(int(1000 / fps))
        self._make_tray()
        if tracking is None:
            tracking = bool(self.settings["tracking"])
        if tracking:
            self.track_action.setChecked(True)  # starts the client through set_tracking
        self._place()

    # -- geometry ------------------------------------------------------------

    def _apply_size(self):
        _, _, vw, vh = self.view
        self.resize(int(vw * self.scale), int(vh * self.scale))

    def _place(self):
        pos = self.settings["pos"]
        if pos and any(s.availableGeometry().contains(QPoint(int(pos[0]) + self.width() // 2,
                                                             int(pos[1]) + self.height() // 2))
                       for s in QApplication.screens()):
            self.move(int(pos[0]), int(pos[1]))
            return
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 40, screen.bottom() - self.height())

    def _render_params(self):
        """Scale/offset from canvas px to framebuffer px (physical pixels for sharp HiDPI)."""
        dpr = self.devicePixelRatioF()
        s = self.scale * dpr
        vx, vy, vw, vh = self.view
        size = (int(round(vw * s)), int(round(vh * s)))
        return s, (-vx * s, -vy * s), size, dpr

    def head_screen_pos(self) -> tuple[float, float]:
        vx, vy, _, _ = self.view
        p = self.mapToGlobal(QPoint(0, 0))
        return (p.x() + (self.head_canvas[0] - vx) * self.scale,
                p.y() + (self.head_canvas[1] - vy) * self.scale)

    def to_canvas(self, local: QPoint) -> tuple[float, float]:
        vx, vy, _, _ = self.view
        return vx + local.x() / self.scale, vy + local.y() / self.scale

    # -- frame loop ------------------------------------------------------------

    def tick(self):
        now = time.perf_counter()
        dt = min(now - self._last, 0.1)
        self._last = now
        self.rig.set_values(self.compute_values(dt))
        self.rig.step_physics(dt)
        items = self.rig.evaluate()

        s, offset, size, dpr = self._render_params()
        self.gl.resize(size)
        frame = np.ascontiguousarray(self.gl.render(items, scale=s, offset=offset))
        h, w = frame.shape[:2]
        image = QImage(frame.data, w, h, w * 4, QImage.Format_RGBA8888_Premultiplied)
        image.setDevicePixelRatio(dpr)
        self._frame, self._image = frame, image
        self.update()
        self._report(now)

    def compute_values(self, dt: float) -> dict[str, float]:
        """All parameter sources for this frame, blended in order (later layers win)."""
        values = self.idle.update(dt)
        cursor = QCursor.pos()
        cursor_xy = (cursor.x(), cursor.y())
        if cursor_xy != self._last_cursor:
            if self._last_cursor is not None:
                self.dozer.activity()
            self._last_cursor = cursor_xy
        follow = self.mouse.update(cursor_xy, self.head_screen_pos(), dt)
        values.update(follow)
        values["ParamAngleZ"] = values.get("ParamAngleZ", 0.0) + follow["ParamAngleX"] * 0.15

        snap = self.vts.snapshot() if self.vts else None
        tracked, w = self.tracking.update(snap, dt)
        values = blend(values, tracked, w, self.defaults)
        if w > 0.5:
            self.dozer.activity()  # someone is in front of the camera
        self._show_vts_status(snap)

        for layer in (self.dozer, self.reaction, self.lipsync):
            v, w = layer.update(dt)
            values = blend(values, v, w, self.defaults)
        return values

    def _report(self, now: float, every: float = 5.0):
        """Print the frame rate and per-frame cost to the console every few seconds."""
        start, frames, busy = self._stats
        frames += 1
        busy += time.perf_counter() - now
        if now - start >= every:
            print(f"fps {frames / (now - start):5.1f} | work per frame {busy / frames * 1000:4.1f} ms", flush=True)
            start, frames, busy = now, 0, 0.0
        self._stats = [start, frames, busy]

    def paintEvent(self, event):
        if self._image is None:
            return
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.drawImage(0, 0, self._image)
        p.end()

    # -- interaction -------------------------------------------------------------

    def _opaque_at(self, pos: QPoint) -> bool:
        if self._frame is None:
            return False
        dpr = self.devicePixelRatioF()
        x, y = int(pos.x() * dpr), int(pos.y() * dpr)
        h, w = self._frame.shape[:2]
        return 0 <= x < w and 0 <= y < h and self._frame[y, x, 3] > 20

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._opaque_at(e.position().toPoint()):
            self._press = e.globalPosition().toPoint()
            self._drag_from = self._press - self.frameGeometry().topLeft()
            self._dragging = False
            e.accept()
        else:
            e.ignore()

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        g = e.globalPosition().toPoint()
        if not self._dragging and (g - self._press).manhattanLength() > DRAG_THRESHOLD:
            self._dragging = True
        if self._dragging:
            self.move(g - self._drag_from)

    def mouseReleaseEvent(self, e):
        if self._press is None:
            return
        if self._dragging:
            self.settings.set("pos", [self.x(), self.y()])
        else:
            self.on_click(e.position().toPoint())
        self._press, self._dragging = None, False

    def on_click(self, local: QPoint):
        self.dozer.activity()
        if self.dozer.wake():
            self.reaction.play("wake")
            return
        where, side = classify_click(self.to_canvas(local), self.face_rect)
        self.reaction.play("pat" if where == HEAD else "poke", side)

    def contextMenuEvent(self, e):
        self.menu.exec(e.globalPos())

    # -- tray ----------------------------------------------------------------------

    def _make_tray(self):
        self.menu = QMenu()
        size_menu = self.menu.addMenu("大小")
        group = QActionGroup(self)
        for name, s in SIZES.items():
            act = QAction(name, self, checkable=True, checked=(s == self.scale))
            act.triggered.connect(lambda _=False, n=name: self.set_size(n))
            group.addAction(act)
            size_menu.addAction(act)
        self.track_action = QAction("面捕（VTube Studio）", self, checkable=True, checked=False)
        self.track_action.toggled.connect(self.set_tracking)
        self.menu.addAction(self.track_action)
        doze_menu = self.menu.addMenu("打瞌睡")
        doze_group = QActionGroup(self)
        for name, minutes in DOZE_CHOICES.items():
            act = QAction(name if minutes == 0 else f"闲置 {name}后", self, checkable=True,
                          checked=(minutes == self.settings["doze_minutes"]))
            act.triggered.connect(lambda _=False, m=minutes: self.set_doze(m))
            doze_group.addAction(act)
            doze_menu.addAction(act)
        top = QAction("置顶", self, checkable=True, checked=bool(self.settings["on_top"]))
        top.toggled.connect(self.set_on_top)
        self.menu.addAction(top)
        self.menu.addSeparator()
        quit_act = QAction("退出", self)
        quit_act.triggered.connect(QApplication.quit)
        self.menu.addAction(quit_act)

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._icon())
        self.tray.setToolTip("chibi pet")
        self.tray.setContextMenu(self.menu)
        self.tray.show()

    def _icon(self) -> QIcon:
        items = self.rig.evaluate()
        face = self.model.parts.get("face")
        if face is None:
            return QIcon()
        x, y, w, h = face.rect
        side = max(w, h) * 1.6
        s = 64 / side
        self.gl.resize((64, 64))
        img = self.gl.render(items, scale=s, offset=(-(x + w / 2 - side / 2) * s, -(y + h / 2 - side / 2) * s))
        img = np.ascontiguousarray(img)
        q = QImage(img.data, 64, 64, 256, QImage.Format_RGBA8888_Premultiplied).copy()
        return QIcon(QPixmap.fromImage(q))

    def set_tracking(self, on: bool):
        if on and self.vts is None:
            self.vts = VTSClient(port=self.vts_port)
            self.vts.start()
        elif not on and self.vts is not None:
            self.vts.stop()
            self.vts = None
            self.tray.setToolTip("chibi pet")
            self._vts_status = None
        self.settings.set("tracking", bool(on))

    def _show_vts_status(self, snap):
        status = snap.status if snap else None
        if status == self._vts_status:
            return
        self._vts_status = status
        text = VTS_STATUS.get(status, "")
        self.tray.setToolTip("chibi pet" + (f"\n{text}" if text else ""))
        if snap:
            print(f"[VTube Studio] {status} {self.vts.last_error if self.vts else ''}", flush=True)

    def set_doze(self, minutes: int):
        self.dozer.after = 60.0 * minutes
        if minutes == 0:
            self.dozer.wake()
        self.settings.set("doze_minutes", minutes)

    def shutdown(self):
        if self.vts is not None:
            self.vts.stop()

    def set_size(self, name: str):
        self.scale = SIZES[name]
        self._apply_size()
        self.settings.set("size", name)

    def set_scale(self, s: float):  # kept for scripts; menu uses set_size
        self.scale = s
        self._apply_size()

    def set_on_top(self, on: bool):
        flags = self.windowFlags()
        flags = flags | Qt.WindowStaysOnTopHint if on else flags & ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        self.settings.set("on_top", bool(on))
