"""Surface comparison & volumes on a common grid.  Δ = A − B  (positive: A above B = fill / accretion)."""
from __future__ import annotations

import numpy as np

from core.surface import Dem, Tin, grid_extent, rasterize_tin


def surface_bounds(s):
    if isinstance(s, Dem):
        nr, nc = s.z.shape
        return s.x0, s.ytop - nr * s.cell, s.x0 + nc * s.cell, s.ytop
    lo, hi = s.vertices[:, :2].min(0), s.vertices[:, :2].max(0)
    return lo[0], lo[1], hi[0], hi[1]


def sample_on_grid(s, x0, ytop, cell, nr, nc):
    """Surface values at the centres of a grid (NaN where undefined)."""
    if isinstance(s, Tin):
        return rasterize_tin(s.vertices, s.triangles, x0, ytop, cell, nr, nc).astype(np.float64)
    if (abs(s.x0 - x0) < 1e-9 and abs(s.ytop - ytop) < 1e-9 and abs(s.cell - cell) < 1e-12
            and s.z.shape == (nr, nc)):
        return s.z.astype(np.float64)
    from core.filters import surface_z
    xs = x0 + (np.arange(nc) + 0.5) * cell
    ys = ytop - (np.arange(nr) + 0.5) * cell
    X, Y = np.meshgrid(xs, ys)
    z = surface_z(s, X.ravel(), Y.ravel()).reshape(nr, nc)
    # surface_z falls back to the nearest cell at edges; outside the surface it must stay NaN
    bx0, by0, bx1, by1 = surface_bounds(s)
    z[(X < bx0) | (X > bx1) | (Y < by0) | (Y > by1)] = np.nan
    return z


def polyline_corridor_mask(xs, ys, line_xy, half_width):
    """Cells whose centre is within half_width of the polyline."""
    X, Y = np.meshgrid(xs, ys)
    P = np.column_stack([X.ravel(), Y.ravel()])
    best = np.full(len(P), np.inf)
    for a, b in zip(line_xy[:-1], line_xy[1:]):
        d = b - a
        L2 = max(float(d @ d), 1e-12)
        t = np.clip(((P - a) @ d) / L2, 0, 1)
        dist = np.hypot(*(P - (a + t[:, None] * d)).T)
        best = np.minimum(best, dist)
    return (best <= half_width).reshape(len(ys), len(xs))


def polygon_mask(xs, ys, poly_xy):
    """Even-odd point-in-polygon for cell centres (vectorised over cells, loop over edges)."""
    X, Y = np.meshgrid(xs, ys)
    inside = np.zeros(X.shape, dtype=bool)
    P = np.asarray(poly_xy, float)
    if len(P) >= 3 and np.allclose(P[0], P[-1]):
        P = P[:-1]
    n = len(P)
    for i in range(n):
        x1, y1 = P[i]
        x2, y2 = P[(i + 1) % n]
        cond = (y1 > Y) != (y2 > Y)
        xint = x1 + (Y - y1) * (x2 - x1) / ((y2 - y1) if y2 != y1 else 1e-12)
        inside ^= cond & (X < xint)
    return inside


def compare(A, B=None, base=None, cell=None, region=None, progress=None):
    """A, B: Dem or Tin; or base (constant elevation) instead of B.
    region: None | ("polygon", xy) | ("corridor", line_xy, half_width)
    → dict with grids and statistics."""
    def rep(f):
        if progress:
            progress(f)
    ax0, ay0, ax1, ay1 = surface_bounds(A)
    if B is not None:
        bx0, by0, bx1, by1 = surface_bounds(B)
        ax0, ay0, ax1, ay1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
        if ax1 <= ax0 or ay1 <= ay0:
            raise ValueError("The two surfaces do not overlap")
    if region is not None:
        rxy = np.asarray(region[1], float)
        pad = region[2] if region[0] == "corridor" else 0.0
        ax0, ay0 = max(ax0, rxy[:, 0].min() - pad), max(ay0, rxy[:, 1].min() - pad)
        ax1, ay1 = min(ax1, rxy[:, 0].max() + pad), min(ay1, rxy[:, 1].max() + pad)
        if ax1 <= ax0 or ay1 <= ay0:
            raise ValueError("The region does not overlap the surface(s)")
    if cell is None:
        cell = A.cell if isinstance(A, Dem) else max((ax1 - ax0) / 1000, 0.1)
    snap = isinstance(A, Dem)
    if snap:   # align with A's own grid so A is used without resampling
        x0 = A.x0 + np.floor((ax0 - A.x0) / cell) * cell
        ytop = A.ytop - np.floor((A.ytop - ay1) / cell) * cell
        nc = int(np.ceil((ax1 - x0) / cell - 1e-9))
        nr = int(np.ceil((ytop - ay0) / cell - 1e-9))
    else:
        x0, ytop, nr, nc = grid_extent(ax0, ay0, ax1, ay1, cell, snap=False)
    if nr * nc > 80_000_000:
        raise ValueError(f"Grid too large ({nc} × {nr}); use a larger cell size")
    rep(0.1)
    za = sample_on_grid(A, x0, ytop, cell, nr, nc) if not (snap and cell == A.cell) else \
        sample_on_grid(Dem(A.z, A.x0, A.ytop, A.cell, ""), A.x0, A.ytop, A.cell, *A.z.shape)[
            int(round((A.ytop - ytop) / cell)):int(round((A.ytop - ytop) / cell)) + nr,
            int(round((x0 - A.x0) / cell)):int(round((x0 - A.x0) / cell)) + nc]
    rep(0.5)
    zb = sample_on_grid(B, x0, ytop, cell, nr, nc) if B is not None else np.full((nr, nc), float(base))
    rep(0.8)
    xs = x0 + (np.arange(nc) + 0.5) * cell
    ys = ytop - (np.arange(nr) + 0.5) * cell
    region_mask = np.ones((nr, nc), dtype=bool)
    if region is not None:
        if region[0] == "polygon":
            region_mask = polygon_mask(xs, ys, region[1])
        else:
            region_mask = polyline_corridor_mask(xs, ys, np.asarray(region[1], float), region[2])
    else:   # whole overlap: data cells + interior holes (gaps not connected to the grid border)
        from scipy import ndimage
        valid = np.isfinite(za) & np.isfinite(zb)
        lab, n = ndimage.label(~valid)
        border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
        holes = (lab > 0) & ~np.isin(lab, border)
        region_mask = valid | holes
    mask = region_mask & np.isfinite(za) & np.isfinite(zb)
    dz = np.where(mask, za - zb, np.nan)
    area = cell * cell
    d = dz[mask]
    fill = d[d > 0]
    cut = d[d < 0]
    stats = {
        "cell": cell, "cells": int(mask.sum()), "area": float(mask.sum() * area),
        "region_area": float(region_mask.sum() * area),
        "coverage": float(mask.sum() / max(region_mask.sum(), 1)),
        "fill_volume": float(fill.sum() * area), "fill_area": float(len(fill) * area),
        "cut_volume": float(-cut.sum() * area), "cut_area": float(len(cut) * area),
        "net_volume": float(d.sum() * area),
        "mean": float(d.mean()) if len(d) else np.nan, "std": float(d.std()) if len(d) else np.nan,
        "min": float(d.min()) if len(d) else np.nan, "max": float(d.max()) if len(d) else np.nan,
        "p05": float(np.percentile(d, 5)) if len(d) else np.nan,
        "p95": float(np.percentile(d, 95)) if len(d) else np.nan,
    }
    rep(1.0)
    return {"dz": dz.astype(np.float32), "za": za.astype(np.float32), "x0": float(x0), "ytop": float(ytop),
            "cell": float(cell), "stats": stats}
