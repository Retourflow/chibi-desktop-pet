"""Build a runtime model (atlas.png + model.json) from art layers.

    python -m chibi_pet.importer <art.psd | layer_folder> <out_dir> [--cell 32]

model.json format (version 1):
{
  "version": 1,
  "canvas": [W, H],
  "atlas": {"file": "atlas.png", "size": [aw, ah]},
  "parts": [
    {"name": "face", "group": "head", "visible": true,
     "rect": [x, y, w, h],                 # crop rectangle on the canvas
     "vertices": [[x, y], ...],            # canvas pixel coordinates
     "uvs": [[u, v], ...],                 # 0..1 in the atlas, v down
     "triangles": [[a, b, c], ...]}
  ]
}
Parts are listed in source layer order (back to front for PSD).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import atlas as atlas_mod
from .layers import Layer, load_layers
from .mesh import grid_mesh

MODEL_VERSION = 1


def build_model(
    layers: list[Layer],
    canvas: tuple[int, int],
    out_dir: str | Path,
    cell: int = 32,
    cell_overrides: dict[str, int] | None = None,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cell_overrides = cell_overrides or {}

    names = [layer.name for layer in layers]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate layer names: {sorted(dupes)}")

    sizes = [layer.size for layer in layers]
    atlas_size, placements = atlas_mod.pack(sizes)
    atlas_img = atlas_mod.compose([layer.image for layer in layers], atlas_size, placements)
    Image.fromarray(atlas_img, "RGBA").save(out_dir / "atlas.png", optimize=True)

    aw, ah = atlas_size
    parts = []
    for layer, place in zip(layers, placements):
        local, tris = grid_mesh(layer.image[..., 3], cell_overrides.get(layer.name, cell))
        ox, oy = layer.offset
        verts = local + np.array([ox, oy], dtype=np.float32)
        uvs = (local + np.array([place.x, place.y], dtype=np.float32)) / np.array([aw, ah], np.float32)
        w, h = layer.size
        parts.append(
            {
                "name": layer.name,
                "group": layer.group,
                "visible": layer.visible,
                "rect": [ox, oy, w, h],
                "vertices": np.round(verts, 2).tolist(),
                "uvs": np.round(uvs, 6).tolist(),
                "triangles": tris.tolist(),
            }
        )

    model = {
        "version": MODEL_VERSION,
        "canvas": list(canvas),
        "atlas": {"file": "atlas.png", "size": [aw, ah]},
        "parts": parts,
    }
    (out_dir / "model.json").write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    return model


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build atlas.png + model.json from art layers")
    ap.add_argument("source", help="layered .psd file, or folder of full-canvas .png layers")
    ap.add_argument("out_dir")
    ap.add_argument("--cell", type=int, default=32, help="mesh grid cell size in canvas pixels")
    args = ap.parse_args(argv)
    canvas, layers = load_layers(args.source)
    model = build_model(layers, canvas, args.out_dir, cell=args.cell)
    n_tri = sum(len(p["triangles"]) for p in model["parts"])
    print(f"{len(model['parts'])} parts, {n_tri} triangles, atlas {model['atlas']['size']}")


if __name__ == "__main__":
    main()
