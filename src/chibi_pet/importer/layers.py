"""Load art layers from a layered PSD or from a folder of full-canvas PNGs.

Both inputs end up as the same thing: a list of `Layer` objects, each holding
the cropped RGBA pixels of one part plus where that crop sits on the canvas.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# Layers inside a group with this name are reference drawings, not model parts.
REFERENCE_GROUP = "ref"


@dataclass
class Layer:
    name: str
    image: np.ndarray  # (h, w, 4) uint8, straight (non-premultiplied) alpha
    offset: tuple[int, int]  # (x, y) of the crop's top-left corner on the canvas
    group: str | None = None
    visible: bool = True

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.image.shape[:2]
        return w, h


def crop_to_alpha(rgba: np.ndarray, offset=(0, 0)) -> tuple[np.ndarray, tuple[int, int]] | None:
    """Crop an RGBA array to the bounding box of its non-transparent pixels."""
    alpha = rgba[..., 3]
    ys, xs = np.nonzero(alpha)
    if len(xs) == 0:
        return None
    x0, x1 = xs.min(), xs.max() + 1
    y0, y1 = ys.min(), ys.max() + 1
    return rgba[y0:y1, x0:x1].copy(), (offset[0] + int(x0), offset[1] + int(y0))


def load_png_folder(folder: Path) -> tuple[tuple[int, int], list[Layer]]:
    """Each `<name>.png` in the folder is one full-canvas layer. Order = file name order.

    All PNGs must share the same size, which becomes the canvas size.
    """
    files = sorted(p for p in folder.glob("*.png"))
    if not files:
        raise FileNotFoundError(f"no .png layers found in {folder}")
    canvas = None
    layers: list[Layer] = []
    for f in files:
        img = Image.open(f).convert("RGBA")
        if canvas is None:
            canvas = img.size
        elif img.size != canvas:
            raise ValueError(f"{f.name}: size {img.size} differs from canvas {canvas}")
        cropped = crop_to_alpha(np.asarray(img))
        if cropped is None:
            continue  # fully transparent layer
        pixels, offset = cropped
        layers.append(Layer(name=f.stem, image=pixels, offset=offset))
    return canvas, layers


def load_psd(path: Path) -> tuple[tuple[int, int], list[Layer]]:
    """Read every pixel layer of a PSD. Layers in the `ref` group are skipped."""
    from psd_tools import PSDImage  # imported lazily: only needed for PSD input

    psd = PSDImage.open(path)
    layers: list[Layer] = []
    for layer in psd.descendants():
        if layer.is_group():
            continue
        group = layer.parent.name if layer.parent is not None and layer.parent is not psd else None
        if group == REFERENCE_GROUP:
            continue
        pil = layer.topil()
        if pil is None:
            continue
        rgba = np.asarray(pil.convert("RGBA"))
        cropped = crop_to_alpha(rgba, offset=(layer.left, layer.top))
        if cropped is None:
            continue
        pixels, offset = cropped
        layers.append(
            Layer(name=layer.name, image=pixels, offset=offset, group=group, visible=layer.visible)
        )
    return (psd.width, psd.height), layers


def load_layers(path: str | Path) -> tuple[tuple[int, int], list[Layer]]:
    path = Path(path)
    if path.is_dir():
        return load_png_folder(path)
    if path.suffix.lower() == ".psd":
        return load_psd(path)
    raise ValueError(f"unsupported layer source: {path} (expected a .psd file or a folder of .png)")
