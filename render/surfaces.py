"""Mesh layers (TIN / DEM) for the 3D view: elevation colouring × hillshade."""
from __future__ import annotations

import numpy as np
from vispy import scene
from vispy.visuals.filters import WireframeFilter

from render.colormap import map_scalar

LIGHT = np.array([-0.5, 0.5, 0.7071])  # from NW, 45° altitude
LIGHT = LIGHT / np.linalg.norm(LIGHT)
AMBIENT = 0.38


def tin_mesh(tin, offset):
    return (tin.vertices - offset).astype(np.float32), tin.triangles, tin.vertices[:, 2].astype(np.float32)


def dem_mesh(dem, offset):
    nr, nc = dem.z.shape
    xs, ys = dem.centers()
    X, Y = np.meshgrid(xs, ys)
    if dem.has_true_position:              # surface passes through the true sounding positions
        real = np.isfinite(dem.tx)
        X = np.where(real, dem.tx, X)
        Y = np.where(real, dem.ty, Y)
    z = dem.z
    valid = np.isfinite(z)
    zf = np.where(valid, z, np.nanmean(z) if valid.any() else 0.0)
    V = np.column_stack([X.ravel() - offset[0], Y.ravel() - offset[1], zf.ravel() - offset[2]]).astype(np.float32)
    ids = np.arange(nr * nc).reshape(nr, nc)
    a, b, c, d = ids[:-1, :-1], ids[:-1, 1:], ids[1:, :-1], ids[1:, 1:]
    va, vb, vc, vd = valid[:-1, :-1], valid[:-1, 1:], valid[1:, :-1], valid[1:, 1:]
    t1 = np.column_stack([a[va & vc & vb], c[va & vc & vb], b[va & vc & vb]])
    t2 = np.column_stack([b[vb & vc & vd], c[vb & vc & vd], d[vb & vc & vd]])
    F = np.vstack([t1, t2]).astype(np.int32)
    return V, F, zf.ravel().astype(np.float32)


def hillshade(V, F, z_scale):
    """Per-vertex shade factor (ambient … 1) using area-weighted vertex normals."""
    P = V.astype(np.float64)
    P[:, 2] *= z_scale
    e1 = P[F[:, 1]] - P[F[:, 0]]
    e2 = P[F[:, 2]] - P[F[:, 0]]
    fn = np.cross(e1, e2)
    fn[fn[:, 2] < 0] *= -1                       # all normals point up
    n = np.zeros((len(V), 3))
    for k in range(3):
        for j in range(3):
            n[:, j] += np.bincount(F[:, k], weights=fn[:, j], minlength=len(V))
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    lam = np.clip(n @ LIGHT, 0, 1)
    return (AMBIENT + (1 - AMBIENT) * lam).astype(np.float32)


class SurfaceLayer:
    def __init__(self, parent, name):
        self.name = name
        self.mesh = scene.visuals.Mesh(parent=parent)
        self.mesh.order = -1
        self.wire = WireframeFilter(width=1.0, color=(0.05, 0.06, 0.08, 0.35), enabled=False)
        self.mesh.attach(self.wire)
        self.mesh.visible = False
        self.V = self.F = self.z = self.shade = None
        self.opacity = 1.0
        self.shading = True
        self._set_gl()

    def _set_gl(self):
        # push the surface slightly back so points lying on it stay visible
        self.mesh.set_gl_state("translucent" if self.opacity < 1 else "opaque", depth_test=True,
                               polygon_offset_fill=True, polygon_offset=(1.0, 1.0),
                               cull_face=False)

    def set_data(self, V, F, z, scale, z_scale):
        self.V, self.F, self.z = V, F, z
        self.update_shade(z_scale, upload=False)
        self.recolor(scale)

    def update_shade(self, z_scale, scale=None, upload=True):
        if self.V is None:
            return
        self.shade = hillshade(self.V, self.F, z_scale) if self.shading else np.ones(len(self.V), np.float32)
        if upload and scale is not None:
            self.recolor(scale)

    def recolor(self, scale):
        if self.V is None:
            return
        c = map_scalar(self.z, scale)
        c[:, :3] *= self.shade[:, None]
        c[:, 3] = self.opacity
        self.mesh.set_data(vertices=self.V, faces=self.F, vertex_colors=c)

    def set_opacity(self, a, scale):
        self.opacity = float(a)
        self._set_gl()
        self.recolor(scale)

    def clear(self):
        self.V = self.F = self.z = self.shade = None
        self.mesh.visible = False
