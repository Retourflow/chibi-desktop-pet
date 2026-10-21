import json

import numpy as np
from PIL import Image

from chibi_pet.importer import build_model, load_layers
from chibi_pet.importer.atlas import pack
from chibi_pet.importer.mesh import grid_mesh


def test_grid_mesh_covers_every_visible_pixel():
    alpha = np.zeros((50, 70), np.uint8)
    alpha[10:40, 5:20] = 255
    alpha[45, 69] = 255  # a lone pixel in the far corner
    verts, tris = grid_mesh(alpha, cell=16)
    covered = np.zeros_like(alpha, dtype=bool)
    for a, b, c in tris:
        x0, y0 = verts[[a, b, c]].min(0).astype(int)
        x1, y1 = verts[[a, b, c]].max(0).astype(int)
        covered[y0:y1, x0:x1] = True
    assert covered[alpha > 0].all()
    assert verts[:, 0].max() <= 70 and verts[:, 1].max() <= 50


def test_grid_mesh_triangles_have_positive_area_and_shared_vertices():
    alpha = np.full((32, 32), 255, np.uint8)
    verts, tris = grid_mesh(alpha, cell=16)
    assert len(verts) == 9  # 3x3 corners, shared between cells
    assert len(tris) == 8
    p = verts[tris]
    area = (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0])
    assert np.all(np.abs(area) > 0)


def test_atlas_packing_has_no_overlaps():
    sizes = [(300, 200), (120, 500), (64, 64), (500, 40), (10, 10)]
    (w, h), places = pack(sizes, padding=2)
    occ = np.zeros((h, w), dtype=int)
    for (sw, sh), p in zip(sizes, places):
        assert (p.w, p.h) == (sw, sh)
        assert p.x + p.w <= w and p.y + p.h <= h
        occ[p.y : p.y + p.h, p.x : p.x + p.w] += 1
    assert occ.max() == 1


def test_png_folder_roundtrip(tmp_path):
    src = tmp_path / "layers"
    src.mkdir()
    for name, box, color in [("a_back", (10, 10, 60, 60), (255, 0, 0, 255)), ("b_front", (30, 20, 90, 40), (0, 0, 255, 128))]:
        img = Image.new("RGBA", (100, 80), (0, 0, 0, 0))
        img.paste(color, box)
        img.save(src / f"{name}.png")
    canvas, layers = load_layers(src)
    assert canvas == (100, 80)
    assert [l.name for l in layers] == ["a_back", "b_front"]
    assert layers[1].offset == (30, 20) and layers[1].size == (60, 20)

    out = tmp_path / "model"
    build_model(layers, canvas, out, cell=8)
    data = json.loads((out / "model.json").read_text())
    assert data["canvas"] == [100, 80]
    front = data["parts"][1]
    assert front["rect"] == [30, 20, 60, 20]
    verts = np.array(front["vertices"])
    assert verts[:, 0].min() == 30 and verts[:, 0].max() == 90
    # uv -> atlas pixel must land on the same colour as the source layer
    atlas = np.asarray(Image.open(out / "atlas.png"))
    aw, ah = data["atlas"]["size"]
    u, v = np.array(front["uvs"]).mean(0)
    assert tuple(atlas[int(v * ah), int(u * aw)]) == (0, 0, 255, 128)
