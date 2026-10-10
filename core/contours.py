"""Contour lines from a DEM / TIN (contourpy), with smoothing, simplification and major/minor levels."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from core.surface import Dem, Tin, grid_extent, rasterize_tin


@dataclass
class ContourSet:
    lines: list                     # each (n, 3) array: x, y, z (z = contour elevation)
    levels: np.ndarray              # elevation of each line
    major: np.ndarray               # bool per line (index contour)
    interval: float
    base: float = 0.0
    index_every: int = 5
    meta: dict = field(default_factory=dict)

    @property
    def n_levels(self):
        return len(np.unique(self.levels))

    def bbox(self):
        if not self.lines:
            return np.zeros(3), np.zeros(3)
        allp = np.vstack(self.lines)
        return allp.min(axis=0), allp.max(axis=0)


def _report(progress, f):
    if progress:
        progress(min(max(f, 0.0), 1.0))


def simplify(pts, tol):
    """Douglas–Peucker (iterative) in plan view."""
    if tol <= 0 or len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i, :2], pts[j, :2]
        seg = pts[i + 1:j, :2]
        d = b - a
        L = np.hypot(*d)
        dist = np.hypot(*(seg - a).T) if L == 0 else np.abs(d[0] * (seg[:, 1] - a[1]) - d[1] * (seg[:, 0] - a[0])) / L
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return pts[keep]


def _smooth(z, sigma):
    """Gaussian smoothing that respects NoData (normalised convolution)."""
    from scipy import ndimage
    if sigma <= 0:
        return z
    ok = np.isfinite(z)
    num = ndimage.gaussian_filter(np.where(ok, z, 0.0), sigma)
    den = ndimage.gaussian_filter(ok.astype(float), sigma)
    out = np.where(den > 1e-6, num / np.maximum(den, 1e-6), np.nan)
    out[~ok] = np.nan
    return out


def make_contours(surface, interval, base=0.0, index_every=5, cell=None, smooth=0.0, simplify_tol=0.0,
                  min_length=0.0, progress=None):
    """smooth: Gaussian sigma in CELLS (0 = off). simplify_tol: metres (0 = off). min_length: metres."""
    import contourpy
    if interval <= 0:
        raise ValueError("The contour interval must be positive")
    if isinstance(surface, Tin):
        cell = cell or 1.0
        V = surface.vertices
        lo, hi = V[:, :2].min(0), V[:, :2].max(0)
        x0, ytop, nr, nc = grid_extent(lo[0], lo[1], hi[0], hi[1], cell, snap=False)
        z = rasterize_tin(V, surface.triangles, x0, ytop, cell, nr, nc).astype(np.float64)
    else:
        z = surface.z.astype(np.float64)
        x0, ytop, cell = surface.x0, surface.ytop, surface.cell
        nr, nc = z.shape
    _report(progress, 0.2)
    z = _smooth(z, smooth)
    xs = x0 + (np.arange(nc) + 0.5) * cell
    ys = ytop - (np.arange(nr) + 0.5) * cell
    zf = z[np.isfinite(z)]
    if len(zf) == 0:
        raise ValueError("The surface has no data")
    k0 = int(np.ceil((zf.min() - base) / interval))
    k1 = int(np.floor((zf.max() - base) / interval))
    if k1 - k0 > 2000:
        raise ValueError(f"{k1 - k0 + 1} contour levels — use a larger interval")
    # contourpy wants increasing y: flip rows
    gen = contourpy.contour_generator(xs, ys[::-1], np.ma.masked_invalid(z[::-1]),
                                      line_type=contourpy.LineType.Separate)
    lines, levels, major = [], [], []
    n_lev = max(k1 - k0 + 1, 1)
    for i, k in enumerate(range(k0, k1 + 1)):
        lev = base + k * interval
        for seg in gen.lines(lev):
            seg = np.asarray(seg, dtype=np.float64)
            if len(seg) < 2:
                continue
            seg = simplify(seg, simplify_tol)
            if min_length > 0 and np.sum(np.hypot(*np.diff(seg, axis=0).T)) < min_length:
                continue
            lines.append(np.column_stack([seg, np.full(len(seg), lev)]))
            levels.append(lev)
            major.append(index_every > 0 and k % index_every == 0)
        _report(progress, 0.2 + 0.8 * (i + 1) / n_lev)
    return ContourSet(lines, np.asarray(levels, float), np.asarray(major, bool), float(interval), float(base),
                      int(index_every), meta={"cell": cell, "smooth": smooth, "simplify": simplify_tol,
                                               "min_length": min_length})
