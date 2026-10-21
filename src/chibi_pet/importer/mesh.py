"""Generate a triangle mesh for one layer.

We use a regular grid clipped to the layer's alpha: every grid cell that
touches a visible pixel becomes two triangles. This is cruder than the
hand-placed meshes a Live2D modeler would make, but it is automatic and dense
enough to deform smoothly (the deformers move vertices, the GPU interpolates
texture inside each triangle).
"""

from __future__ import annotations

import numpy as np


def grid_mesh(alpha: np.ndarray, cell: int, threshold: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Build a grid mesh covering every pixel with alpha > threshold.

    Returns (vertices, triangles):
      vertices  (N, 2) float32, in the layer's local pixel coordinates (x, y)
      triangles (M, 3) int32, indices into vertices, counter-clockwise on screen
    """
    if cell <= 0:
        raise ValueError("cell must be positive")
    h, w = alpha.shape
    nx = -(-w // cell)  # ceil division
    ny = -(-h // cell)

    # Which cells contain at least one visible pixel?
    mask = alpha > threshold
    padded = np.zeros((ny * cell, nx * cell), dtype=bool)
    padded[:h, :w] = mask
    occupied = padded.reshape(ny, cell, nx, cell).any(axis=(1, 3))  # (ny, nx)

    # Grid corner (i, j) is used if any of the up-to-4 cells around it is occupied.
    corner_used = np.zeros((ny + 1, nx + 1), dtype=bool)
    corner_used[:-1, :-1] |= occupied
    corner_used[:-1, 1:] |= occupied
    corner_used[1:, :-1] |= occupied
    corner_used[1:, 1:] |= occupied

    index = -np.ones((ny + 1, nx + 1), dtype=np.int32)
    jj, ii = np.nonzero(corner_used)  # row (y) index, column (x) index
    index[jj, ii] = np.arange(len(jj), dtype=np.int32)
    xs = np.minimum(ii * cell, w).astype(np.float32)
    ys = np.minimum(jj * cell, h).astype(np.float32)
    vertices = np.stack([xs, ys], axis=1)

    cj, ci = np.nonzero(occupied)
    a = index[cj, ci]  # top-left
    b = index[cj, ci + 1]  # top-right
    c = index[cj + 1, ci]  # bottom-left
    d = index[cj + 1, ci + 1]  # bottom-right
    tris = np.concatenate([np.stack([a, c, b], 1), np.stack([b, c, d], 1)], axis=0)
    return vertices, tris.astype(np.int32)
