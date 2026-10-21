"""Rig runtime: parameters in, deformed meshes out.

Per frame:
  1. set parameter values (tracking, mouse, idle animation, ...)
  2. step physics (writes hair/tail/tassel bend parameters)
  3. evaluate: for every part, rest vertices + keyform offsets, then the
     deformer chain up to the root -> list of DrawItem for a renderer
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .deformers import BendDeformer, Deformer, RotationDeformer, Turn3DDeformer, rotate
from .keyform import KeyformGroup
from .model import Model
from .physics import PendulumChain, PhysicsInput, PhysicsOutput
from .spec import Resolver

log = logging.getLogger(__name__)


@dataclass
class Parameter:
    id: str
    min: float
    max: float
    default: float = 0.0

    def clamp(self, v: float) -> float:
        return min(self.max, max(self.min, v))


@dataclass
class RigPart:
    name: str
    rest: np.ndarray  # (N, 2)
    z: np.ndarray  # (N,) depth used by Turn3D deformers
    uvs: np.ndarray
    triangles: np.ndarray
    chain: list[Deformer]  # innermost first
    keyforms: list[KeyformGroup] = field(default_factory=list)
    opacity: float = 1.0
    clip: str | None = None


@dataclass
class DrawItem:
    name: str
    vertices: np.ndarray
    uvs: np.ndarray
    triangles: np.ndarray
    opacity: float
    clip: str | None


def _depth(spec: dict | None, rest: np.ndarray, res: Resolver, name: str) -> np.ndarray:
    if not spec:
        return np.zeros(len(rest))
    kind = spec.get("type", "const")
    offset = res.length(spec["offset"], name) if "offset" in spec else 0.0
    if kind == "const":
        return np.full(len(rest), res.length(spec.get("z", 0.0), name) + offset)
    if kind == "sphere":
        c = res.point(spec["center"], name)
        r = res.length(spec["radius"], name)
        dx = rest[:, 0] - c[0]
        dy = (rest[:, 1] - c[1]) * spec.get("y_scale", 1.0)
        z = np.sqrt(np.maximum(r * r - dx * dx - dy * dy, 0.0))
        return z * spec.get("z_scale", 1.0) + offset
    raise ValueError(f"{name}: unknown depth type '{kind}'")


def _form_offsets(form: dict, rest: np.ndarray, res: Resolver, name: str) -> tuple[np.ndarray, float]:
    """One keyform recipe -> vertex offsets. Recipe = scale/rotate about a pivot, then translate."""
    if not form:
        return np.zeros_like(rest), 1.0
    pivot = res.point(form.get("pivot", {"self": [0.5, 0.5]}), name)
    sx, sy = form.get("scale", [1.0, 1.0])
    v = (rest - pivot) * np.array([sx, sy])
    if "rotate" in form:
        v = rotate(v, float(form["rotate"]))
    v = v + pivot
    if "translate" in form:
        v = v + res.vector(form["translate"], name)
    return v - rest, float(form.get("opacity", 1.0))


def _build_keyform(kspec: dict, rest: np.ndarray, res: Resolver, name: str) -> KeyformGroup:
    params = kspec["params"]
    keys = [np.asarray(k, dtype=np.float64) for k in kspec["keys"]]
    shape = tuple(len(k) for k in keys)
    forms = np.empty(shape, dtype=object)
    nested = kspec["forms"]
    for idx in np.ndindex(*shape):
        f = nested
        for i in idx:
            f = f[i]
        forms[idx] = f
    offsets = np.zeros(shape + rest.shape)
    opacity = np.ones(shape)
    for idx in np.ndindex(*shape):
        offsets[idx], opacity[idx] = _form_offsets(forms[idx], rest, res, name)
    return KeyformGroup(params=params, keys=keys, offsets=offsets, opacity=opacity)


def _build_deformer(d: dict, res: Resolver) -> Deformer:
    kind = d["type"]
    common = {"id": d["id"], "parent": d.get("parent")}
    if kind == "rotation":
        return RotationDeformer(
            **common,
            origin=res.point(d["origin"]),
            angle=dict(d.get("angle", {})),
            translate={p: res.vector(v) for p, v in d.get("translate", {}).items()},
            scale={p: np.asarray(v, dtype=np.float64) for p, v in d.get("scale", {}).items()},
        )
    if kind == "turn3d":
        yaw, pitch = d.get("yaw", {}), d.get("pitch", {})
        return Turn3DDeformer(
            **common,
            center=res.point(d["center"]),
            yaw_param=yaw.get("param", "ParamAngleX"),
            yaw_scale=yaw.get("scale", 0.6),
            pitch_param=pitch.get("param", "ParamAngleY"),
            pitch_scale=pitch.get("scale", 0.4),
        )
    if kind == "bend":
        return BendDeformer(
            **common,
            root=res.point(d["root"]),
            tip=res.point(d["tip"]),
            segments=[dict(s) for s in d["segments"]],
        )
    raise ValueError(f"deformer {d['id']}: unknown type '{kind}'")


class Rig:
    def __init__(self, model: Model, spec: dict):
        self.model = model
        res = Resolver({n: p.rect for n, p in model.parts.items()}, model.canvas)

        self.params: dict[str, Parameter] = {}
        for p in spec.get("parameters", []):
            self.params[p["id"]] = Parameter(p["id"], p["min"], p["max"], p.get("default", 0.0))
        self.values: dict[str, float] = {k: p.default for k, p in self.params.items()}

        # Deformers whose anchors point at missing parts are skipped (with a warning);
        # parts using them then fall back to the nearest surviving ancestor.
        self.deformers: dict[str, Deformer] = {}
        parents: dict[str, str | None] = {}
        for d in spec.get("deformers", []):
            parents[d["id"]] = d.get("parent")
            try:
                self.deformers[d["id"]] = _build_deformer(d, res)
            except KeyError as e:
                log.warning("deformer %s skipped: %s", d["id"], e)
        self._parents = parents

        part_specs: dict = spec.get("parts", {})
        self.missing = [n for n in part_specs if n not in model.parts]
        self.unrigged = [n for n in model.order if n not in part_specs]
        for n in self.missing:
            log.info("part '%s' is in the rig spec but not in the art", n)
        for n in self.unrigged:
            log.warning("part '%s' is in the art but not in the rig spec", n)

        order = [n for n in spec.get("draw_order", []) if n in model.parts]
        order += [n for n in model.order if n not in order]

        default_def = spec.get("default_deformer")
        self.parts: list[RigPart] = []
        for name in order:
            mesh = model.parts[name]
            ps = part_specs.get(name, {})
            # Skip parts hidden in the spec, and layers hidden in the PSD that the spec
            # does not mention (rigged parts such as eye_closed may be hidden in the PSD).
            if ps.get("hidden") or (not mesh.visible and name not in part_specs):
                continue
            rest = mesh.vertices
            depth = ps.get("depth")
            if isinstance(depth, str):  # reuse another part's depth recipe, e.g. "face"
                depth = part_specs.get(depth, {}).get("depth")
            part = RigPart(
                name=name,
                rest=rest,
                z=_depth(depth, rest, res, name),
                uvs=mesh.uvs,
                triangles=mesh.triangles,
                chain=self._chain(ps.get("deformer", default_def)),
                keyforms=[_build_keyform(k, rest, res, name) for k in ps.get("keyforms", [])],
                opacity=float(ps.get("opacity", 1.0)),
                clip=ps.get("clip") if ps.get("clip") in model.parts else None,
            )
            self.parts.append(part)

        self.physics: list[PendulumChain] = []
        for ph in spec.get("physics", []):
            self.physics.append(
                PendulumChain(
                    id=ph["id"],
                    inputs=[PhysicsInput(i["param"], i.get("kind", "x"), i["weight"]) for i in ph["inputs"]],
                    outputs=[PhysicsOutput(o["param"], o["segment"], o.get("scale", 1.0)) for o in ph["outputs"]],
                    segments=ph.get("segments", 2),
                    gravity=ph.get("gravity", 30.0),
                    damping=ph.get("damping", 0.12),
                    stiffness=ph.get("stiffness", 0.0),
                    max_angle=ph.get("max_angle", 45.0),
                )
            )

    def _chain(self, deformer_id: str | None) -> list[Deformer]:
        chain = []
        seen = set()
        while deformer_id is not None:
            if deformer_id in seen:
                raise ValueError(f"deformer cycle at {deformer_id}")
            seen.add(deformer_id)
            if deformer_id in self.deformers:
                chain.append(self.deformers[deformer_id])
            elif deformer_id not in self._parents:
                raise KeyError(f"unknown deformer '{deformer_id}'")
            deformer_id = self._parents.get(deformer_id)
        return chain

    # -- per frame -------------------------------------------------------

    def set_values(self, values: dict[str, float]) -> None:
        for k, v in values.items():
            p = self.params.get(k)
            self.values[k] = p.clamp(v) if p else v

    def reset(self) -> None:
        self.values = {k: p.default for k, p in self.params.items()}
        for ph in self.physics:
            ph.reset()

    def step_physics(self, dt: float) -> None:
        for ph in self.physics:
            self.set_values(ph.step(self.values, dt))

    def evaluate(self) -> list[DrawItem]:
        items = []
        for part in self.parts:
            pts = part.rest.copy()
            opacity = part.opacity
            for kf in part.keyforms:
                off, op = kf.evaluate(self.values)
                pts += off
                opacity *= op
            for d in part.chain:
                pts = d.apply(pts, part.z, self.values)
            items.append(DrawItem(part.name, pts, part.uvs, part.triangles, opacity, part.clip))
        return items
