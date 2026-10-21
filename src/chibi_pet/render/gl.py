"""GPU renderer (moderngl, OpenGL 3.3) that draws DrawItems into an offscreen image.

Same rules as the CPU renderer in soft.py, done by the GPU:

- the atlas is uploaded premultiplied, so bilinear filtering never darkens edges
- blending is "premultiplied over": out = src + dst * (1 - src.a)
- clip masks: before drawing a clipped part (e.g. the iris), its mask part
  (the eye white) is drawn into a separate single-channel mask texture; the
  clipped part's fragment shader multiplies by that mask at the same screen pixel

We render offscreen (into a framebuffer) and read the pixels back, instead of
drawing straight into a window. A transparent, always-on-top OpenGL window is
fragile on Windows; a plain transparent Qt widget that paints an image is not.
Reading back a ~600x600 image each frame costs about 1.4 MB of copying, which
is fine at 60 fps.
"""

from __future__ import annotations

import numpy as np

try:
    import moderngl
except ImportError:  # pragma: no cover - only needed for real-time rendering
    moderngl = None

VERTEX_SHADER = """
#version 330
uniform vec2 u_scale;   // canvas px -> output px
uniform vec2 u_offset;  // output px
uniform vec2 u_size;    // output size in px
in vec2 in_pos;         // canvas px
in vec2 in_uv;
out vec2 v_uv;
void main() {
    vec2 px = in_pos * u_scale + u_offset;
    vec2 ndc = px / u_size * 2.0 - 1.0;
    gl_Position = vec4(ndc.x, -ndc.y, 0.0, 1.0);  // y down on screen
    v_uv = in_uv;
}
"""

FRAGMENT_SHADER = """
#version 330
uniform sampler2D u_atlas;
uniform sampler2D u_mask;
uniform float u_opacity;
uniform bool u_use_mask;
uniform bool u_mask_pass;
uniform vec2 u_size;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec4 c = texture(u_atlas, v_uv);  // premultiplied
    if (u_mask_pass) {
        f_color = vec4(c.a * u_opacity);  // coverage only
        return;
    }
    if (u_use_mask) {
        // the framebuffer is stored bottom-up, gl_FragCoord is in that space too
        c *= texture(u_mask, gl_FragCoord.xy / u_size).r;
    }
    f_color = c * u_opacity;
}
"""


class GLRenderer:
    def __init__(self, model, atlas_rgba: np.ndarray, size: tuple[int, int], ctx=None):
        if moderngl is None:
            raise RuntimeError("moderngl is not installed (pip install moderngl)")
        self.ctx = ctx or moderngl.create_standalone_context()
        self.model = model
        atlas = atlas_rgba.astype(np.float32) / 255.0
        atlas[..., :3] *= atlas[..., 3:4]
        atlas_u8 = (atlas * 255 + 0.5).astype(np.uint8)
        h, w = atlas_u8.shape[:2]
        self.atlas = self.ctx.texture((w, h), 4, atlas_u8.tobytes())
        self.atlas.build_mipmaps()
        self.atlas.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        self.prog = self.ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER)
        self.prog["u_atlas"].value = 0
        self.prog["u_mask"].value = 1

        # one vertex buffer per part: positions change every frame, uvs never do
        self._parts = {}
        for name, mesh in model.parts.items():
            n = len(mesh.vertices)
            pos = self.ctx.buffer(reserve=n * 8, dynamic=True)
            uv = self.ctx.buffer(mesh.uvs.astype("f4").tobytes())
            ibo = self.ctx.buffer(mesh.triangles.astype("i4").tobytes())
            vao = self.ctx.vertex_array(self.prog, [(pos, "2f", "in_pos"), (uv, "2f", "in_uv")], ibo)
            self._parts[name] = (pos, vao)
        self.size = (0, 0)
        self.resize(size)

    def resize(self, size: tuple[int, int]) -> None:
        size = (max(1, int(size[0])), max(1, int(size[1])))
        if size == self.size:
            return
        self.size = size
        self.color = self.ctx.texture(size, 4)
        self.fbo = self.ctx.framebuffer(color_attachments=[self.color])
        self.mask = self.ctx.texture(size, 1)
        self.mask_fbo = self.ctx.framebuffer(color_attachments=[self.mask])
        self.prog["u_size"].value = size

    def _draw(self, item, scale, offset):
        pos, vao = self._parts[item.name]
        pos.write(item.vertices.astype("f4").tobytes())
        self.prog["u_scale"].value = (scale, scale)
        self.prog["u_offset"].value = tuple(offset)
        self.prog["u_opacity"].value = float(item.opacity)
        vao.render(moderngl.TRIANGLES)

    def render(self, items, scale: float = 1.0, offset=(0.0, 0.0)) -> np.ndarray:
        """Draw items, return premultiplied RGBA uint8 (h, w, 4), top row first."""
        by_name = {it.name: it for it in items}
        ctx = self.ctx
        ctx.enable(moderngl.BLEND)
        self.atlas.use(0)
        self.fbo.use()
        self.fbo.clear(0.0, 0.0, 0.0, 0.0)
        for it in items:
            if it.opacity <= 1e-3:
                continue
            if it.clip:
                # draw the mask part's coverage into the mask texture
                self.mask_fbo.use()
                self.mask_fbo.clear(0.0)
                ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
                mask_item = by_name.get(it.clip)
                if mask_item is not None:
                    self.prog["u_mask_pass"].value = True
                    self.prog["u_use_mask"].value = False
                    self._draw(mask_item, scale, offset)
                self.prog["u_mask_pass"].value = False
                self.fbo.use()
                self.mask.use(1)
            self.prog["u_use_mask"].value = bool(it.clip)
            self.prog["u_mask_pass"].value = False
            ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
            self._draw(it, scale, offset)
        w, h = self.size
        data = np.frombuffer(self.fbo.read(components=4, alignment=1), dtype=np.uint8)
        return data.reshape(h, w, 4)[::-1]  # GL rows are bottom-up

    def release(self) -> None:
        self.ctx.release()
