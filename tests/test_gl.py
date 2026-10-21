import numpy as np
import pytest

from chibi_pet.render.soft import load_atlas, render
from chibi_pet.rig.runtime import Rig

from test_runtime import SPEC, _make_model

moderngl = pytest.importorskip("moderngl")


def _context():
    try:
        return moderngl.create_standalone_context()
    except Exception as e:  # no GPU / display in this environment
        pytest.skip(f"no OpenGL context: {e}")


def test_gl_matches_soft_renderer(tmp_path):
    from PIL import Image

    from chibi_pet.render.gl import GLRenderer

    model = _make_model(tmp_path)
    rig = Rig(model, SPEC)
    rig.set_values({"ParamAngleX": 15})
    items = rig.evaluate()
    gl = GLRenderer(model, np.asarray(Image.open(model.atlas_path).convert("RGBA")), model.canvas, ctx=_context())
    out = gl.render(items).astype(np.float32) / 255.0
    ref = render(items, load_atlas(model.atlas_path), model.canvas)
    assert np.abs(out - ref).mean() < 0.02
    # clip mask: iris pixel above the eye white is hidden on the GPU too
    assert out[37, 40, 0] > 0.9
