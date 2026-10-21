"""Runtime view of model.json (produced by chibi_pet.importer)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class PartMesh:
    name: str
    group: str | None
    visible: bool
    rect: tuple[int, int, int, int]  # x, y, w, h on the canvas
    vertices: np.ndarray  # (N, 2) float64, canvas pixels (rest pose)
    uvs: np.ndarray  # (N, 2) float32, 0..1 in the atlas
    triangles: np.ndarray  # (M, 3) int32


@dataclass
class Model:
    canvas: tuple[int, int]
    atlas_path: Path
    atlas_size: tuple[int, int]
    parts: dict[str, PartMesh]
    order: list[str]  # source layer order, back to front


def load_model(model_dir: str | Path) -> Model:
    model_dir = Path(model_dir)
    data = json.loads((model_dir / "model.json").read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError(f"unsupported model.json version: {data.get('version')}")
    parts = {}
    order = []
    for p in data["parts"]:
        parts[p["name"]] = PartMesh(
            name=p["name"],
            group=p.get("group"),
            visible=p.get("visible", True),
            rect=tuple(p["rect"]),
            vertices=np.asarray(p["vertices"], dtype=np.float64).reshape(-1, 2),
            uvs=np.asarray(p["uvs"], dtype=np.float32).reshape(-1, 2),
            triangles=np.asarray(p["triangles"], dtype=np.int32).reshape(-1, 3),
        )
        order.append(p["name"])
    return Model(
        canvas=tuple(data["canvas"]),
        atlas_path=model_dir / data["atlas"]["file"],
        atlas_size=tuple(data["atlas"]["size"]),
        parts=parts,
        order=order,
    )
