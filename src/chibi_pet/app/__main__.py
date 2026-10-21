"""Run the desktop pet.

    python -m chibi_pet.app <model_dir> [--rig assets/rig/chibi.rig.json] [--size 中] [--vts]
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
    ap.add_argument("--size", default=None, choices=["小", "中", "大", "特大"], help="default: last used")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--vts", action="store_true", help="start with VTube Studio face tracking on (default: last used)")
    ap.add_argument("--vts-port", type=int, default=8001)
    args = ap.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from .pet_window import PetWindow

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # closing happens from the tray menu
    win = PetWindow(args.model_dir, args.rig, size=args.size, fps=args.fps,
                    tracking=True if args.vts else None, vts_port=args.vts_port)
    app.aboutToQuit.connect(win.shutdown)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
