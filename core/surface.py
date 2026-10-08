"""Surface generation: grid thinning, TIN (Delaunay 2.5D) and DEM gridding.

Coordinates stay in float64 world units; triangulation runs on XY shifted to the
data centre for numerical robustness.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

THIN_MODES = {"median": "Median Z", "max": "Max Z (shallowest)", "min": "Min Z (deepest)", "first": "First point"}
DEM_METHODS = {
    "tin": "Linear (TIN)",
    "mean": "Mean",
    "median": "Median",
    "min": "Min Z (deepest)",
    "max": "Max Z (shallowest)",
    "count": "Point count",
    "shoal": "Shoalest depth (true position)",
    "idw": "IDW (inverse distance)",
}


def _report(progress, f):
    if progress is not None:
        progress(min(max(f, 0.0), 1.0))


def nice_number(x):
    """Round to 1, 2, 2.5 or 5 × 10^n."""
    if x <= 0 or not math.isfinite(x):
        return 1.0
    mag = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= x * 0.999:
            return m * mag
    return 10 * mag


def mean_spacing(xyz, idx):
    """Rough average point spacing from bbox area / count."""
    if len(idx) < 2:
        return 1.0
    xy = xyz[idx[:: max(1, len(idx) // 200_000)], :2]
    ext = xy.max(axis=0) - xy.min(axis=0)
    area = max(ext[0] * ext[1], 1e-9)
    return math.sqrt(area / len(idx))


# ---------------------------------------------------------------- thinning
def _group_pick(key, z, mode):
    """For each unique key return the index (into key) of the representative point."""
    if mode == "first":
        order = np.argsort(key, kind="stable")
    else:
        order = np.lexsort((z, key))
    ks = key[order]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    ends = np.r_[starts[1:], len(ks)]
    if mode in ("first", "min"):
        return order[starts], ks[starts]
    if mode == "max":
        return order[ends - 1], ks[starts]
    return order[(starts + ends - 1) // 2], ks[starts]          # median


def thin_grid(xyz, idx, cell, mode="median"):
    """Keep one real point per grid cell."""
    if cell <= 0 or len(idx) == 0:
        return idx
    p = xyz[idx]
    x0, y0 = p[:, 0].min(), p[:, 1].min()
    ix = ((p[:, 0] - x0) // cell).astype(np.int64)
    iy = ((p[:, 1] - y0) // cell).astype(np.int64)
    key = iy * (int(ix.max()) + 1) + ix
    pick, _ = _group_pick(key, p[:, 2], mode)
    return idx[np.sort(pick)]


# ---------------------------------------------------------------- TIN
@dataclass
class Tin:
    vertices: np.ndarray            # (N, 3) float64 world coordinates
    triangles: np.ndarray           # (M, 3) int32
    max_edge: float
    source_count: int
    thin_cell: float = 0.0
    version: int = 0                # edit version of the point cloud it was built from
    meta: dict = field(default_factory=dict)


def build_tin(xyz, idx, max_edge=0.0, thin_cell=0.0, thin_mode="median", progress=None):
    from scipy.spatial import Delaunay
    src_n = len(idx)
    if thin_cell > 0:
        idx = thin_grid(xyz, idx, thin_cell, thin_mode)
    _report(progress, 0.1)
    if len(idx) < 3:
        raise ValueError("At least 3 points are needed to build a TIN")
    v = xyz[idx].copy()
    centre = v[:, :2].mean(axis=0)
    tri = Delaunay(v[:, :2] - centre, qhull_options="Qbb Qc Qz Q12")
    _report(progress, 0.8)
    f = tri.simplices.astype(np.int32)
    if max_edge and max_edge > 0:
        xy = v[:, :2]
        lim = max_edge * max_edge
        keep = np.ones(len(f), dtype=bool)
        for a, b in ((0, 1), (1, 2), (2, 0)):
            d = xy[f[:, a]] - xy[f[:, b]]
            keep &= (d * d).sum(axis=1) <= lim
        f = f[keep]
    if len(f) == 0:
        raise ValueError("No triangles left — increase the maximum edge length")
    _report(progress, 1.0)
    return Tin(v, f, max_edge, src_n, thin_cell)


# ---------------------------------------------------------------- DEM
@dataclass
class Dem:
    z: np.ndarray                   # (rows, cols) float32, NaN = no data; row 0 = north
    x0: float                       # left edge
    ytop: float                     # top edge
    cell: float
    method: str
    version: int = 0
    meta: dict = field(default_factory=dict)
    tx: np.ndarray | None = None    # shoal method: true X of the sounding chosen per cell (NaN = none)
    ty: np.ndarray | None = None

    @property
    def has_true_position(self):
        return self.tx is not None

    @property
    def shape(self):
        return self.z.shape

    def centers(self):
        r, c = self.z.shape
        xs = self.x0 + (np.arange(c) + 0.5) * self.cell
        ys = self.ytop - (np.arange(r) + 0.5) * self.cell
        return xs, ys


def grid_extent(xmin, ymin, xmax, ymax, cell, snap=True):
    if snap:
        x0, ytop = math.floor(xmin / cell) * cell, math.ceil(ymax / cell) * cell
        x1, ybot = math.ceil(xmax / cell) * cell, math.floor(ymin / cell) * cell
    else:
        x0, ytop, x1, ybot = xmin, ymax, xmax, ymin
    ncols = max(1, int(math.ceil((x1 - x0) / cell - 1e-9)))
    nrows = max(1, int(math.ceil((ytop - ybot) / cell - 1e-9)))
    if not snap or x1 == xmax:
        ncols = max(ncols, int((xmax - x0) // cell) + 1)
        nrows = max(nrows, int((ytop - ymin) // cell) + 1)
    return x0, ytop, nrows, ncols


def rasterize_tin(V, F, x0, ytop, cell, nrows, ncols, progress=None, chunk=500_000):
    """Linear interpolation of a TIN at grid cell centres (vectorized per triangle)."""
    out = np.full(nrows * ncols, np.nan, dtype=np.float32)
    col = (V[:, 0] - x0) / cell - 0.5          # vertex position in cell-centre index space
    row = (ytop - V[:, 1]) / cell - 0.5
    zv = V[:, 2]
    eps = 1e-9
    for s in range(0, len(F), chunk):
        f = F[s:s + chunk]
        cx, ry, zz = col[f], row[f], zv[f]
        c0 = np.clip(np.ceil(cx.min(1)), 0, ncols - 1).astype(np.int64)
        c1 = np.clip(np.floor(cx.max(1)), -1, ncols - 1).astype(np.int64)
        r0 = np.clip(np.ceil(ry.min(1)), 0, nrows - 1).astype(np.int64)
        r1 = np.clip(np.floor(ry.max(1)), -1, nrows - 1).astype(np.int64)
        w = c1 - c0 + 1
        h = r1 - r0 + 1
        ok = (w > 0) & (h > 0) & (cx.max(1) >= 0) & (ry.max(1) >= 0)
        cnt = np.where(ok, w * h, 0)
        total = int(cnt.sum())
        if total:
            t = np.repeat(np.arange(len(f)), cnt)
            off = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
            cc = c0[t] + off % w[t]
            rr = r0[t] + off // w[t]
            x1, x2, x3 = cx[t, 0], cx[t, 1], cx[t, 2]
            y1, y2, y3 = ry[t, 0], ry[t, 1], ry[t, 2]
            det = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
            good = np.abs(det) > 1e-12
            det = np.where(good, det, 1.0)
            l1 = ((y2 - y3) * (cc - x3) + (x3 - x2) * (rr - y3)) / det
            l2 = ((y3 - y1) * (cc - x3) + (x1 - x3) * (rr - y3)) / det
            l3 = 1.0 - l1 - l2
            inside = good & (l1 >= -eps) & (l2 >= -eps) & (l3 >= -eps)
            z = l1 * zz[t, 0] + l2 * zz[t, 1] + l3 * zz[t, 2]
            out[(rr * ncols + cc)[inside]] = z[inside]
        _report(progress, (s + len(f)) / len(F))
    return out.reshape(nrows, ncols)


def fill_holes(z, max_cells):
    """Fill interior NoData holes up to max_cells with the nearest valid value."""
    from scipy import ndimage
    nan = np.isnan(z)
    if not nan.any() or max_cells <= 0:
        return z, 0
    lab, n = ndimage.label(nan)
    if n == 0:
        return z, 0
    sizes = np.bincount(lab.ravel())
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    fillable = sizes <= max_cells
    fillable[0] = False
    fillable[border] = False
    mask = fillable[lab]
    if not mask.any():
        return z, 0
    _, (ri, ci) = ndimage.distance_transform_edt(nan, return_indices=True)
    z = z.copy()
    z[mask] = z[ri[mask], ci[mask]]
    return z, int(mask.sum())


def build_dem(xyz, idx, cell, method="tin", tin=None, max_edge=0.0, idw_power=2.0,
              idw_radius=0.0, idw_k=12, fill=0, snap=True, shoal_high=True, progress=None):
    p = xyz[idx]
    if len(p) == 0:
        raise ValueError("No points to grid")
    xmin, ymin = p[:, 0].min(), p[:, 1].min()
    xmax, ymax = p[:, 0].max(), p[:, 1].max()
    x0, ytop, nr, nc = grid_extent(xmin, ymin, xmax, ymax, cell, snap)
    meta = {"source_points": len(idx)}
    tx = ty = None

    if method == "tin":
        if tin is None:
            tin = build_tin(xyz, idx, max_edge=max_edge,
                            progress=lambda f: _report(progress, 0.6 * f))
            meta["tin_built"] = True
        z = rasterize_tin(tin.vertices, tin.triangles, x0, ytop, cell, nr, nc,
                          progress=lambda f: _report(progress, 0.6 + 0.35 * f))
    elif method == "idw":
        from scipy.spatial import cKDTree
        radius = idw_radius if idw_radius > 0 else 3 * cell
        tree = cKDTree(p[:, :2])
        xs = x0 + (np.arange(nc) + 0.5) * cell
        ys = ytop - (np.arange(nr) + 0.5) * cell
        z = np.full(nr * nc, np.nan, dtype=np.float32)
        rows_per = max(1, 400_000 // nc)
        for r in range(0, nr, rows_per):
            gx, gy = np.meshgrid(xs, ys[r:r + rows_per])
            q = np.column_stack([gx.ravel(), gy.ravel()])
            d, i = tree.query(q, k=idw_k, distance_upper_bound=radius, workers=-1)
            d = np.atleast_2d(d.reshape(len(q), -1))
            i = np.atleast_2d(i.reshape(len(q), -1))
            valid = np.isfinite(d)
            iv = np.where(valid, i, 0)
            wgt = np.where(valid, 1.0 / np.maximum(d, 1e-9) ** idw_power, 0.0)
            ws = wgt.sum(axis=1)
            val = np.where(ws > 0, (wgt * p[iv, 2]).sum(axis=1) / np.where(ws > 0, ws, 1), np.nan)
            z[r * nc:r * nc + len(q)] = val
            _report(progress, 0.95 * min(1.0, (r + rows_per) / nr))
        z = z.reshape(nr, nc)
    elif method == "shoal":
        # Shoal-biased gridding: per cell keep the shoalest REAL sounding and its true XY.
        # shoal_high=True  → Z is elevation (up +, depths negative): shoalest = max Z
        # shoal_high=False → Z is a positive depth:                  shoalest = min Z
        col = np.clip(((p[:, 0] - x0) // cell).astype(np.int64), 0, nc - 1)
        row = np.clip(((ytop - p[:, 1]) // cell).astype(np.int64), 0, nr - 1)
        key = row * nc + col
        pick, keys = _group_pick(key, p[:, 2], "max" if shoal_high else "min")
        z = np.full(nr * nc, np.nan, dtype=np.float32)
        tx = np.full(nr * nc, np.nan)
        ty = np.full(nr * nc, np.nan)
        z[keys] = p[pick, 2]
        tx[keys] = p[pick, 0]
        ty[keys] = p[pick, 1]
        z, tx, ty = z.reshape(nr, nc), tx.reshape(nr, nc), ty.reshape(nr, nc)
        meta["shoal_high"] = bool(shoal_high)
        meta["soundings"] = int(len(keys))
        _report(progress, 0.95)
    else:
        col = np.clip(((p[:, 0] - x0) // cell).astype(np.int64), 0, nc - 1)
        row = np.clip(((ytop - p[:, 1]) // cell).astype(np.int64), 0, nr - 1)
        key = row * nc + col
        z = np.full(nr * nc, np.nan, dtype=np.float32)
        if method in ("mean", "count"):
            cnt = np.bincount(key, minlength=nr * nc)
            if method == "count":
                z = cnt.astype(np.float32)
                z[cnt == 0] = np.nan
            else:
                s = np.bincount(key, weights=p[:, 2], minlength=nr * nc)
                has = cnt > 0
                z[has] = s[has] / cnt[has]
        else:
            pick, keys = _group_pick(key, p[:, 2], method)
            z[keys] = p[pick, 2]
        z = z.reshape(nr, nc)
        _report(progress, 0.95)

    filled = 0
    if fill > 0 and method != "count":
        z, filled = fill_holes(z, fill)
    meta["filled_cells"] = filled          # filled cells keep NaN true position (no real sounding)
    _report(progress, 1.0)
    return Dem(z.astype(np.float32), float(x0), float(ytop), float(cell), method, meta=meta, tx=tx, ty=ty)
