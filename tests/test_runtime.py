import numpy as np
from PIL import Image

from chibi_pet.importer import build_model, load_layers
from chibi_pet.render.soft import load_atlas, render
from chibi_pet.rig.model import load_model
from chibi_pet.rig.runtime import Rig


def _make_model(tmp_path):
    src = tmp_path / "layers"
    src.mkdir()
    shapes = {
        "a_face": ((20, 20, 80, 80), (250, 220, 210, 255)),
        "b_white": ((30, 40, 50, 55), (255, 255, 255, 255)),
        "c_iris": ((35, 35, 60, 60), (40, 160, 60, 255)),
    }
    for name, (box, color) in shapes.items():
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        img.paste(color, box)
        img.save(src / f"{name}.png")
    canvas, layers = load_layers(src)
    # rename to the names the spec uses
    for l in layers:
        l.name = l.name.split("_", 1)[1]
    build_model(layers, canvas, tmp_path / "model", cell=8)
    return load_model(tmp_path / "model")


SPEC = {
    "parameters": [{"id": "ParamAngleX", "min": -30, "max": 30}, {"id": "Open", "min": 0, "max": 1, "default": 1}],
    "deformers": [
        {"id": "head", "type": "turn3d", "center": {"part": "face", "at": [0.5, 0.5]}},
        {"id": "ghost", "type": "rotation", "parent": "head", "origin": {"part": "missing_part", "at": [0, 0]}},
    ],
    "draw_order": ["face", "white", "iris", "not_in_art"],
    "parts": {
        "face": {"deformer": "head", "depth": {"type": "sphere", "center": {"part": "face", "at": [0.5, 0.5]},
                                               "radius": {"part": "face", "w": 0.6}}},
        "white": {"deformer": "ghost", "depth": "face",
                  "keyforms": [{"params": ["Open"], "keys": [[0, 1]],
                                "forms": [{"scale": [1, 0.1], "opacity": 0}, {}]}]},
        "iris": {"deformer": "head", "clip": "white"},
        "not_in_art": {"deformer": "head"},
    },
}


def test_rest_pose_matches_mesh_and_tolerates_missing_parts(tmp_path):
    model = _make_model(tmp_path)
    rig = Rig(model, SPEC)
    assert rig.missing == ["not_in_art"]
    items = {i.name: i for i in rig.evaluate()}
    for name, item in items.items():
        np.testing.assert_allclose(item.vertices, model.parts[name].vertices, atol=1e-9)
    # the "ghost" deformer was skipped, so "white" falls back to its parent "head"
    rig.set_values({"ParamAngleX": 30})
    moved = {i.name: i for i in rig.evaluate()}
    assert not np.allclose(moved["white"].vertices, model.parts["white"].vertices)


def test_clip_mask_and_opacity_in_soft_renderer(tmp_path):
    model = _make_model(tmp_path)
    rig = Rig(model, SPEC)
    atlas = load_atlas(model.atlas_path)
    img = render(rig.evaluate(), atlas, model.canvas)
    # iris pixel inside the white: green; iris pixel outside the white (y=37): clipped -> face colour
    assert img[45, 40, 1] > img[45, 40, 0]
    assert img[37, 40, 0] > 0.9
    rig.set_values({"Open": 0.0})  # closing the white also hides the iris it clips
    img = render(rig.evaluate(), atlas, model.canvas)
    assert img[45, 40, 0] > 0.9
