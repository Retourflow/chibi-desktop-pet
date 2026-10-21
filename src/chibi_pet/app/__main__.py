"""Run the desktop pet.

    python -m chibi_pet.app <model_dir> [--rig assets/rig/chibi.rig.json] [--size 中]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_RIG = Path(__file__).resolve().parents[3] / "assets" / "rig" / "chibi.rig.json"


def main(argv=None):
    ap = argparse.ArgumentParser(description="chibi desktop pet")
    ap.add_argument("model_dir", help="folder with atlas.png + model.json (from chibi_pet.importer)")
    ap.add_argument("--rig", default=str(DEFAULT_RIG))
    ap.add_argument("--size", default="中", choices=["小", "中", "大", "特大"])
    ap.add_argument("--fps", type=int, default=60)
    args = ap.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from .pet_window import PetWindow

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # closing happens from the tray menu
    win = PetWindow(args.model_dir, args.rig, size=args.size, fps=args.fps)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
