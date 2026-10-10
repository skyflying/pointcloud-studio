"""Point cloud classification: ground filters (SMRF, PMF, CSF, grid minimum) and above-ground classes
(vegetation by height above ground, buildings by local planarity). Pure NumPy / SciPy, grid based.

All functions take xyz (N×3, Z up) and idx (indices to classify) and return boolean / class arrays aligned
with idx. ASPRS classes: 2 ground · 3/4/5 low/medium/high vegetation · 6 building · 1 unclassified."""
from __future__ import annotations

import numpy as np
from scipy import ndimage

GROUND_METHODS = {
    "smrf": "SMRF — Simple Morphological Filter (Pingel 2013)",
    "pmf": "PMF — Progressive Morphological Filter (Zhang 2003)",
    "csf": "CSF — Cloth Simulation Filter (Zhang 2016)",
    "gridmin": "Grid minimum + height threshold",
}

GROUND_HELP = {
    "smrf": "Openings with growing windows on a minimum-elevation grid; cells that rise faster than the slope "
            "threshold are objects. The final ground test allows for the local slope. Robust general choice "
            "(the default method of PDAL).",
    "pmf": "Morphological openings with exponentially growing windows; the elevation-difference threshold grows "
           "with the window size and the terrain slope. Good on flat to moderately rolling terrain.",
    "csf": "The cloud is turned upside down and a cloth is dropped onto it; points close to the settled cloth are "
           "ground. Few, intuitive parameters; handles steep slopes well (softer cloth on steep terrain; a stiffer, "
           "coarser cloth — rigidness 3, 2 m+ — where large flat roofs could hold the cloth up).",
    "gridmin": "Lowest point per cell, interpolated to a reference surface; points within the height threshold "
               "(plus slope × cell on slopes) are ground. Simple and fast — flat or gently sloping terrain, "
               "beaches, bathymetry. The cell must be larger than the largest object.",
}


def _report(progress, f):
    if progress:
        progress(min(max(f, 0.0), 1.0))


# ---------------------------------------------------------------- grid helpers
def _grid(xy, cell):
    lo = xy.min(axis=0) - cell
    nx = int(np.ceil((xy[:, 0].max() - lo[0]) / cell)) + 2
    ny = int(np.ceil((xy[:, 1].max() - lo[1]) / cell)) + 2
    ix = ((xy[:, 0] - lo[0]) / cell).astype(np.int64)
    iy = ((xy[:, 1] - lo[1]) / cell).astype(np.int64)
    return lo, nx, ny, ix, iy


def _min_grid(z, ix, iy, nx, ny):
    g = np.full(ny * nx, np.inf)
    np.minimum.at(g, iy * nx + ix, z)
    g = g.reshape(ny, nx)
    g[np.isinf(g)] = np.nan
    return g


def _inpaint(g):
    """Fill NaN cells from the nearest valid cell."""
    ok = np.isfinite(g)
    if ok.all() or not ok.any():
        return g
    _, (iy, ix) = ndimage.distance_transform_edt(~ok, return_indices=True)
    return g[iy, ix]


def _bilinear(g, lo, cell, x, y):
    """Grid value at points (cell centres at lo + (i + 0.5)·cell)."""
    ny, nx = g.shape
    fx = (x - lo[0]) / cell - 0.5
    fy = (y - lo[1]) / cell - 0.5
    x0 = np.clip(np.floor(fx).astype(np.int64), 0, nx - 2)
    y0 = np.clip(np.floor(fy).astype(np.int64), 0, ny - 2)
    tx = np.clip(fx - x0, 0, 1)
    ty = np.clip(fy - y0, 0, 1)
    return ((1 - ty) * ((1 - tx) * g[y0, x0] + tx * g[y0, x0 + 1])
            + ty * ((1 - tx) * g[y0 + 1, x0] + tx * g[y0 + 1, x0 + 1]))


def _disk(r):
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r


# ---------------------------------------------------------------- ground filters
def smrf(xyz, idx, cell=1.0, slope=0.15, window=18.0, threshold=0.5, scalar=1.25, progress=None):
    p = xyz[idx]
    lo, nx, ny, ix, iy = _grid(p[:, :2], cell)
    zmin = _inpaint(_min_grid(p[:, 2], ix, iy, nx, ny))
    obj = np.zeros_like(zmin, dtype=bool)
    last = zmin.copy()
    rmax = max(1, int(np.ceil(window / cell)))
    for r in range(1, rmax + 1):
        opened = ndimage.grey_opening(zmin, footprint=_disk(r))
        obj |= (last - opened) > slope * r * cell
        last = opened
        _report(progress, 0.6 * r / rmax)
    ground = np.where(obj, np.nan, zmin)
    ground = _inpaint(ground)
    gy, gx = np.gradient(ground, cell)
    gslope = np.hypot(gx, gy)
    zg = _bilinear(ground, lo, cell, p[:, 0], p[:, 1])
    sl = _bilinear(gslope, lo, cell, p[:, 0], p[:, 1])
    _report(progress, 1.0)
    return np.abs(p[:, 2] - zg) <= threshold + scalar * sl


def pmf(xyz, idx, cell=1.0, max_window=33.0, slope=0.15, initial_dh=0.15, max_dh=2.5, progress=None):
    p = xyz[idx]
    lo, nx, ny, ix, iy = _grid(p[:, :2], cell)
    z = _inpaint(_min_grid(p[:, 2], ix, iy, nx, ny))
    flag = np.zeros(len(p), dtype=bool)
    k, w_prev, dh = 0, 1, initial_dh
    windows = []
    while True:
        w = 2 * (2 ** k) + 1
        if w * cell > max_window:
            break
        windows.append(w)
        k += 1
    for i, w in enumerate(windows):
        opened = ndimage.grey_opening(z, size=(w, w))
        zp = opened[iy, ix]
        flag |= (p[:, 2] - zp) > dh
        dh = min(slope * (w - w_prev) * cell + initial_dh, max_dh)
        w_prev = w
        z = opened
        _report(progress, (i + 1) / max(len(windows), 1))
    return ~flag


def csf(xyz, idx, cloth=1.0, rigidness=2, iterations=500, threshold=0.5, time_step=0.65, progress=None):
    """Cloth simulation on the inverted cloud. rigidness 1 (soft, steep terrain) … 3 (stiff, flat terrain)."""
    p = xyz[idx]
    lo, nx, ny, ix, iy = _grid(p[:, :2], cloth)
    inv = -p[:, 2]
    # collision height of each cloth particle = highest inverted point below it (i.e. −lowest z)
    h = np.full(ny * nx, -np.inf)
    np.maximum.at(h, iy * nx + ix, inv)
    h = h.reshape(ny, nx)
    has = np.isfinite(h)
    hfill = np.where(has, h, -np.inf)
    top = float(inv.max()) + 1.0
    c = np.full((ny, nx), top)
    movable = np.ones((ny, nx), dtype=bool)
    disp = time_step * time_step * 1.0            # gravity step (cloth units)
    passes = {1: 1, 2: 2, 3: 3}.get(int(rigidness), 2)
    for it in range(int(iterations)):
        c = np.where(movable, c - disp, c)
        hit = movable & (c <= hfill)
        c = np.where(hit, hfill, c)
        movable &= ~hit
        for _ in range(passes):                     # internal forces: pull towards the neighbour average
            pad = np.pad(c, 1, mode="edge")
            avg = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:]) / 4.0
            c = np.where(movable, c + 0.5 * (avg - c), c)
        c = np.where(movable & (c < hfill), hfill, c)
        if it % 25 == 0:
            _report(progress, 0.95 * it / iterations)
            if not movable.any():
                break
    cz = _bilinear(c, lo, cloth, p[:, 0], p[:, 1])
    _report(progress, 1.0)
    return np.abs(inv - cz) <= threshold


def grid_minimum(xyz, idx, cell=5.0, threshold=0.5, progress=None):
    p = xyz[idx]
    lo, nx, ny, ix, iy = _grid(p[:, :2], cell)
    zmin = _inpaint(_min_grid(p[:, 2], ix, iy, nx, ny))
    zmin = ndimage.uniform_filter(zmin, 3)                  # smooth the stair steps of the cell minima
    gy, gx = np.gradient(zmin, cell)
    slope = _bilinear(np.hypot(gx, gy), lo, cell, p[:, 0], p[:, 1])
    zg = _bilinear(zmin, lo, cell, p[:, 0], p[:, 1])
    _report(progress, 1.0)
    # on slopes the lowest point of a cell lies below its centre: allow slope × cell on top of the threshold
    return (p[:, 2] - zg) <= threshold + slope * cell


def ground(xyz, idx, method, params, progress=None):
    fn = {"smrf": smrf, "pmf": pmf, "csf": csf, "gridmin": grid_minimum}[method]
    return fn(xyz, np.asarray(idx, np.int64), progress=progress, **params)


# ---------------------------------------------------------------- above ground
def height_above_ground(xyz, idx, ground_idx, cell=1.0):
    """HAG of points idx from a ground surface (TIN rasterised from ground_idx, nearest fill)."""
    from core.surface import grid_extent, rasterize_tin
    from scipy.spatial import Delaunay
    g = xyz[ground_idx]
    p = xyz[idx]
    allxy = np.vstack([g[:, :2], p[:, :2]])
    lo, hi = allxy.min(0), allxy.max(0)
    x0, ytop, nr, nc = grid_extent(lo[0], lo[1], hi[0], hi[1], cell, snap=False)
    tri = Delaunay(g[:, :2])
    surf = _inpaint(rasterize_tin(g, tri.simplices.astype(np.int64), x0, ytop, cell, nr, nc).astype(np.float64))
    # grid rows run north → south: convert to the bilinear helper's south → north layout
    surf_sn = surf[::-1]
    lo_g = np.array([x0, ytop - nr * cell])
    return p[:, 2] - _bilinear(surf_sn, lo_g, cell, p[:, 0], p[:, 1])


def planarity(xyz, idx, k=16, chunk=200_000):
    """Surface variation λ3 / (λ1 + λ2 + λ3) of the k-neighbour covariance (≈ 0 on a flat roof, large in a
    tree canopy) and |normal_z| (1 horizontal, 0 vertical wall)."""
    from scipy.spatial import cKDTree
    p = xyz[idx]
    tree = cKDTree(p)
    plan = np.zeros(len(p))
    nz = np.zeros(len(p))
    for s in range(0, len(p), chunk):
        _, nb = tree.query(p[s:s + chunk], k=min(k, len(p)), workers=-1)
        q = p[nb] - p[nb].mean(axis=1, keepdims=True)
        cov = np.einsum("nki,nkj->nij", q, q) / q.shape[1]
        w, v = np.linalg.eigh(cov)                       # ascending: w0 = λ3 … w2 = λ1
        plan[s:s + chunk] = w[:, 0] / np.maximum(w.sum(axis=1), 1e-12)
        nz[s:s + chunk] = np.abs(v[:, 2, 0])             # z component of the normal (smallest eigenvector)
    return plan, nz


def above_ground_classes(xyz, idx, hag, veg=(0.5, 2.0, 5.0), buildings=None, progress=None):
    """Classes for points idx (aligned): HAG < veg[0] → 1 (unclassified, near ground);
    vegetation 3 / 4 / 5 by HAG bands; buildings (dict(min_height, roughness, min_area, cell)) → 6."""
    cls = np.ones(len(idx), dtype=np.uint8)
    above = hag >= veg[0]
    cls[above & (hag < veg[1])] = 3
    cls[above & (hag >= veg[1]) & (hag < veg[2])] = 4
    cls[above & (hag >= veg[2])] = 5
    _report(progress, 0.3)
    if buildings:
        cand = np.flatnonzero(hag >= buildings.get("min_height", 2.5))
        if len(cand) >= 20:
            var, nz = planarity(xyz, np.asarray(idx)[cand], k=buildings.get("k", 16))
            # flat (low surface variation) and not steeper than a pitched roof (|n_z| ≥ 0.6 ≈ 53°)
            roof = (var <= buildings.get("roughness", 0.02)) & (nz >= 0.6)
            _report(progress, 0.8)
            # keep roof clusters of a minimum area (connected cells in plan view)
            c = buildings.get("cell", 1.0)
            pts = xyz[np.asarray(idx)[cand[roof]]]
            if len(pts):
                lo, nx, ny, ix, iy = _grid(pts[:, :2], c)
                m = np.zeros((ny, nx), dtype=bool)
                m[iy, ix] = True
                m = ndimage.binary_closing(m, iterations=1)
                lab, n = ndimage.label(m)
                sizes = ndimage.sum(m, lab, index=np.arange(1, n + 1)) * c * c
                big = np.r_[False, sizes >= buildings.get("min_area", 25.0)]
                keep = big[lab[iy, ix]]
                cls[cand[roof][keep]] = 6
                # walls and roof edges: every above-ground point inside the roof footprint (+1 cell)
                foot = ndimage.binary_dilation(big[lab], iterations=1)
                allp = xyz[np.asarray(idx)]
                jx = ((allp[:, 0] - lo[0]) / c).astype(np.int64)
                jy = ((allp[:, 1] - lo[1]) / c).astype(np.int64)
                ok = (jx >= 0) & (jy >= 0) & (jx < nx) & (jy < ny) & (hag >= veg[0] if veg else hag >= 0.5)
                inside = np.zeros(len(allp), dtype=bool)
                inside[ok] = foot[jy[ok], jx[ok]]
                cls[inside] = 6
    _report(progress, 1.0)
    return cls


def classify(xyz, idx, ground_method, ground_params, veg=None, buildings=None, existing_ground=None,
             progress=None):
    """Full run → (ground mask over idx, classes over idx). ground_method None: use existing_ground mask."""
    idx = np.asarray(idx, np.int64)
    if ground_method:
        g = ground(xyz, idx, ground_method, ground_params,
                   progress=(lambda f: progress(0.6 * f)) if progress else None)
    else:
        g = np.asarray(existing_ground, bool)
    cls = np.where(g, 2, 1).astype(np.uint8)
    if (veg or buildings) and g.sum() >= 3 and (~g).any():
        ng = idx[~g]
        hag = height_above_ground(xyz, ng, idx[g])
        cls[~g] = above_ground_classes(xyz, ng, hag, veg or (np.inf, np.inf, np.inf), buildings,
                                       progress=(lambda f: progress(0.6 + 0.4 * f)) if progress else None)
    if progress:
        progress(1.0)
    return g, cls
