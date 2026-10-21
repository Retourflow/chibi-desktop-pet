"""Remembered settings (size, face tracking, always-on-top, dozing, window position).

Stored as JSON in `.pet_settings.json` next to the project (inside D:\\live2d pet
on the user's PC), not in the user profile on C:. A missing or broken file just
means defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = PROJECT_ROOT / ".pet_settings.json"

DEFAULTS = {
    "size": "中",
    "on_top": True,
    "tracking": False,
    "doze_minutes": 3,  # 0 = never doze
    "pos": None,  # [x, y] of the window's top-left corner, None = bottom-right of the screen
}


class Settings:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        self.data = dict(DEFAULTS)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data[key]

    def set(self, key, value) -> None:
        if self.data.get(key) == value:
            return
        self.data[key] = value
        self.save()

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass  # settings are a convenience; never crash the pet over them
