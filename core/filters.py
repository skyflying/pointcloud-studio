"""Automatic noise filters. Each returns candidate point indices (a subset of `idx`) — nothing is
rejected automatically; the candidates become the selection for the user to review."""
from __future__ import annotations

import numpy as np

METHODS = {
    "sor": "Statistical outlier (SOR)",
    "radius": "Radius outlier",
    "grid": "Grid median deviation",
    "surface": "Reference surface deviation",
    "range": "Depth / elevation range",
    "cube": "CUBE-style (uncertainty, multiple hypotheses)",
    "iho": "IHO S-44 TVU test",
    "local": "Robust local surface (MAD)",
    "cluster": "Isolated clusters",
    "photo": "Photogrammetry noise (SfM-MVS)",
}

HELP = {
    "sor": "For each point, the mean 3D distance to its k nearest neighbours. Points whose mean distance is more "
           "than N standard deviations above the average are outliers. Good for scattered noise (water column, vegetation returns, air).",
    "radius": "Points with fewer than the minimum number of neighbours within the search radius (3D). "
              "Good for isolated points far from the surface (water column, birds, air).",
    "grid": "A median surface is built from plan-view cells (cell medians, interpolated between cells so the "
            "slope of the seabed / ground is removed); each point is compared with that surface. Threshold in metres, or as a "
            "multiple of the local robust spread (MAD × 1.4826). The standard spike filter for MBES and LiDAR — the result line "
            "reports the data's noise level to help choose the threshold.",
    "surface": "Distance from a reference surface (TIN or DEM) — e.g. a surface built from already cleaned data. "
               "Points too far above or below it are outliers.",
    "range": "Points shallower / deeper than given limits (gross errors).",
    "cube": "After Calder & Mayer (2003): each sounding's vertical / horizontal uncertainty is propagated to the "
            "grid nodes around it; per node the soundings form depth hypotheses (sea floor, fish / water column, "
            "multipath …); the sea-floor hypothesis is chosen by density, by agreement with the neighbourhood "
            "(locale) or by a reference surface (prior). Soundings far from it — in units of their combined "
            "uncertainty — are candidates. Batch variant: close to, but not identical with CARIS / Qimera CUBE. "
            "Optionally adds the CUBE depth, uncertainty and hypothesis-count grids as layers.",
    "iho": "IHO S-44 Ed. 6.0: soundings whose residual to a reference surface (local median surface, or a TIN / "
           "DEM) exceeds the order's TVU limit √(a² + (b·d)²), and/or whose own TPU exceeds it. "
           "For the full compliance report use Analysis → IHO S-44 Check.",
    "local": "A plane through each point's k nearest neighbours (without the point itself); points farther from it "
             "than N × the neighbours' robust spread (1.4826 · MAD) are candidates. Follows slopes, sand waves and "
             "structures better than a cell median.",
    "cluster": "Points are grouped by touching voxels of the given size. Groups smaller than the minimum (and not "
               "the main surface) are candidates — water-column noise, fish, bubbles, birds, floating blobs.",
    "photo": "Tests for clouds made from photos (this program's photogrammetry or Metashape / Pix4D / "
             "RealityCapture): too few supporting image pairs ('views'), residual to the local surface with the "
             "allowance growing with distance², floating points in front of the surface the cameras saw (needs "
             "camera positions and orientations), sky-coloured points off the surface (needs RGB).",
}


def _report(progress, f):
    if progress is not None:
        progress(min(max(f, 0.0), 1.0))


def sor(xyz, idx, k=8, n_std=2.0, progress=None, chunk=500_000):
    from scipy.spatial import cKDTree
    p = xyz[idx]
    if len(p) <= k:
        return idx[:0], {}
    tree = cKDTree(p)
    mean_d = np.empty(len(p))
    for s in range(0, len(p), chunk):
        d, _ = tree.query(p[s:s + chunk], k=k + 1, workers=-1)       # first neighbour = the point itself
        mean_d[s:s + chunk] = d[:, 1:].mean(axis=1)
        _report(progress, (s + chunk) / len(p))
    mu, sd = float(mean_d.mean()), float(mean_d.std())
    thr = mu + n_std * sd
    out = mean_d > thr
    return idx[out], {"threshold": f"mean neighbour distance > {thr:.3f} m (μ {mu:.3f}, σ {sd:.3f})"}


def radius_outlier(xyz, idx, radius=1.0, min_neighbors=3, progress=None, chunk=500_000):
    from scipy.spatial import cKDTree
    p = xyz[idx]
    tree = cKDTree(p)
    cnt = np.empty(len(p), dtype=np.int64)
    for s in range(0, len(p), chunk):
        cnt[s:s + chunk] = tree.query_ball_point(p[s:s + chunk], r=radius, return_length=True, workers=-1) - 1
        _report(progress, (s + chunk) / len(p))
    out = cnt < min_neighbors
    return idx[out], {"threshold": f"< {min_neighbors} neighbours within {radius:g} m"}


def grid_median(xyz, idx, cell=1.0, mode="abs", value=0.3, side="both", min_points=3, progress=None):
    """Deviation from a MEDIAN SURFACE: cell medians → grid → bilinear at each point (removes the seabed
    slope inside a cell). mode 'abs': |dz| > value (m); 'mad': |dz| > value × robust σ (1.4826 × MAD of dz
    in the cell)."""
    from core.surface import Dem, fill_holes
    p = xyz[idx]
    x0 = np.floor(p[:, 0].min() / cell) * cell - cell
    ytop = np.ceil(p[:, 1].max() / cell) * cell + cell
    nc = int(np.ceil((p[:, 0].max() - x0) / cell)) + 2
    nr = int(np.ceil((ytop - p[:, 1].min()) / cell)) + 2
    col = ((p[:, 0] - x0) // cell).astype(np.int64)
    row = ((ytop - p[:, 1]) // cell).astype(np.int64)
    key = row * nc + col
    order = np.lexsort((p[:, 2], key))
    ks, zs = key[order], p[order, 2]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    ends = np.r_[starts[1:], len(ks)]
    counts = ends - starts
    med = (zs[starts + (counts - 1) // 2] + zs[starts + counts // 2]) / 2
    grid = np.full(nr * nc, np.nan, np.float32)
    good = counts >= min_points
    grid[ks[starts[good]]] = med[good]
    grid = grid.reshape(nr, nc)
    grid, _ = fill_holes(grid, 10**9) if np.isnan(grid).any() and np.isfinite(grid).any() else (grid, 0)
    _report(progress, 0.4)
    zref = surface_z(Dem(grid, x0, ytop, cell, "median"), p[:, 0], p[:, 1])
    dz = p[:, 2] - zref
    noise = 1.4826 * float(np.nanmedian(np.abs(dz)))
    _report(progress, 0.7)
    if mode == "mad":
        grp = np.empty(len(p), np.int64)
        grp[order] = np.repeat(np.arange(len(starts)), counts)
        ad = np.abs(dz)
        o2 = np.lexsort((ad, grp))
        ads, g2 = ad[o2], grp[o2]
        st = np.flatnonzero(np.r_[True, g2[1:] != g2[:-1]])
        en = np.r_[st[1:], len(g2)]
        mad = (ads[st + (en - st - 1) // 2] + ads[st + (en - st) // 2]) / 2
        cnt = en - st
        global_sigma = 1.4826 * float(np.nanmedian(ad))
        sigma = np.where(cnt >= 5, 1.4826 * mad, global_sigma)          # small cells: use the global spread
        sigma = np.maximum(sigma, max(global_sigma * 0.5, 1e-3))
        thr = value * sigma[_cell_index(grp, g2, st)]
        desc = f"|dz| > {value:g} × robust σ (median surface, {cell:g} m cells)"
    else:
        thr = np.full(len(p), float(value))
        desc = f"|dz| > {value:g} m from the median surface ({cell:g} m cells)"
    desc += f" · data noise (robust σ) ≈ {noise:.3f} m"
    if side == "shoal":
        bad = dz > thr
    elif side == "deep":
        bad = dz < -thr
    else:
        bad = np.abs(dz) > thr
    bad &= np.isfinite(dz)
    _report(progress, 1.0)
    return idx[bad], {"threshold": desc}


def _cell_index(grp, g2, st):
    """Map each point's group id to the position of that group in the run-length arrays."""
    ids = g2[st]                     # group id of each run, sorted ascending
    return np.searchsorted(ids, grp)


def surface_z(surface, x, y):
    """Surface Z at XY (NaN outside). DEM: bilinear. TIN: rasterised at fine resolution, then bilinear."""
    from core.surface import Dem, Tin, grid_extent, rasterize_tin
    if isinstance(surface, Tin):
        V = surface.vertices
        e = np.sqrt(np.median(np.sum((V[surface.triangles[:, 0], :2] - V[surface.triangles[:, 1], :2]) ** 2, 1)))
        cell = max(float(e) / 2, 0.05)
        lo, hi = V[:, :2].min(0), V[:, :2].max(0)
        x0, ytop, nr, nc = grid_extent(lo[0], lo[1], hi[0], hi[1], cell, snap=False)
        while nr * nc > 60_000_000:
            cell *= 1.5
            x0, ytop, nr, nc = grid_extent(lo[0], lo[1], hi[0], hi[1], cell, snap=False)
        surface = Dem(rasterize_tin(V, surface.triangles, x0, ytop, cell, nr, nc), x0, ytop, cell, "tin")
    d = surface
    c = (x - d.x0) / d.cell - 0.5
    r = (d.ytop - y) / d.cell - 0.5
    nr, nc = d.z.shape
    c0, r0 = np.floor(c).astype(np.int64), np.floor(r).astype(np.int64)
    fc, fr = c - c0, r - r0
    z = np.full(len(x), np.nan)
    ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < nc) & (r0 + 1 < nr)
    i = np.flatnonzero(ok)
    Z = d.z
    z[i] = ((1 - fr[i]) * ((1 - fc[i]) * Z[r0[i], c0[i]] + fc[i] * Z[r0[i], c0[i] + 1])
            + fr[i] * ((1 - fc[i]) * Z[r0[i] + 1, c0[i]] + fc[i] * Z[r0[i] + 1, c0[i] + 1]))
    # fallback: nearest cell (edges of the grid, or a NoData neighbour in the 2×2 block)
    miss = ~np.isfinite(z)
    if miss.any():
        rc = np.clip(np.round(r[miss]).astype(np.int64), 0, nr - 1)
        cc = np.clip(np.round(c[miss]).astype(np.int64), 0, nc - 1)
        inside = (r[miss] > -1) & (r[miss] < nr) & (c[miss] > -1) & (c[miss] < nc)
        zz = np.where(inside, Z[rc, cc], np.nan)
        z[miss] = zz
    return z


def surface_deviation(xyz, idx, surface, above=0.5, below=0.5, progress=None):
    p = xyz[idx]
    zs = surface_z(surface, p[:, 0], p[:, 1])
    _report(progress, 0.8)
    dz = p[:, 2] - zs
    ok = np.isfinite(dz)
    out = ok & ((dz > above) | (dz < -below))
    n_out = int((~ok).sum())
    return idx[out], {"threshold": f"more than {above:g} m above or {below:g} m below the surface"
                      + (f" · {n_out:,} points outside the surface ignored" if n_out else "")}


def z_range(xyz, idx, zmin, zmax, progress=None):
    z = xyz[idx, 2]
    out = (z < zmin) | (z > zmax)
    return idx[out], {"threshold": f"Z outside [{zmin:g}, {zmax:g}]"}


def run(xyz, idx, method, params, surface=None, progress=None, pc=None, crs=None):
    idx = np.asarray(idx, dtype=np.int64)
    if len(idx) == 0:
        return idx, {"threshold": "no points in scope"}
    if method in ("cube", "iho", "local", "cluster", "photo"):
        return _run_extra(xyz, idx, method, dict(params), surface, progress, pc, crs)
    if method == "sor":
        return sor(xyz, idx, progress=progress, **params)
    if method == "radius":
        return radius_outlier(xyz, idx, progress=progress, **params)
    if method == "grid":
        return grid_median(xyz, idx, progress=progress, **params)
    if method == "surface":
        if surface is None:
            raise ValueError("No reference surface (TIN or DEM) is available")
        return surface_deviation(xyz, idx, surface, progress=progress, **params)
    if method == "range":
        return z_range(xyz, idx, progress=progress, **params)
    raise ValueError(method)


def _run_extra(xyz, idx, method, p, surface, progress, pc, crs):
    from core import noise_filters as nf
    if method == "local":
        return nf.local_mad(xyz, idx, progress=progress, **p)
    if method == "cluster":
        return nf.clusters(xyz, idx, progress=progress, **p)
    if method == "photo":
        cams = p.pop("cameras", None)
        return nf.photogrammetry(xyz, idx, pc.attrs if pc is not None else {}, cams, progress=progress, **p)
    from core import uncertainty as un
    depth = np.abs(xyz[idx, 2])
    if method == "cube":
        from core import cube
        u = un.resolve(p.pop("unc"), pc, idx, crs, depth)
        prior = None
        if p.get("disambiguation") == "prior":
            if surface is None:
                raise ValueError("Choose a reference surface (TIN / DEM) for the 'prior' disambiguation")
            prior = lambda x, y: surface_z(surface, x, y)  # noqa: E731
        add_layers = p.pop("add_layers", False)
        r = cube.estimate(xyz, idx, u["tvu"], u["thu"], prior_z=prior, progress=progress, **p)
        s = r["summary"]
        per = s["points"] / max(s["nodes"], 1)
        hint = (f" · only {per:.1f} soundings per node — a coarser node spacing gives more reliable hypotheses"
                if per < 3 else "")
        info = {"threshold": f"{s['nodes']:,} nodes at {s['resolution']:g} m, {s['multi_hyp_nodes']:,} with several "
                             f"hypotheses ({s['disambiguation']}); flagged beyond {s['k_f']:g} × combined σ · "
                             + "; ".join(u["notes"]) + hint}
        if add_layers:
            if r["grid"] is not None:
                info["layers"] = r["grid"]
            else:
                info["threshold"] += " · grid layers skipped (extent too large for a map at this resolution)"
        return r["candidates"], info
    if method == "iho":
        from core import s44
        order = p.get("order", "1a")
        mode = p.get("mode", "residual")
        lim = s44.allowed_tvu(order, depth)
        flag = np.zeros(len(idx), bool)
        notes = [f"{s44.ORDER_LABEL[order]} TVU limit {np.median(lim):.3f} m at median depth"]
        if mode in ("residual", "either"):
            if p.get("reference") == "surface":
                if surface is None:
                    raise ValueError("Choose a reference surface (TIN / DEM)")
                zr = surface_z(surface, xyz[idx, 0], xyz[idx, 1])
            else:
                zr = s44.local_reference(xyz, idx, p.get("cell", 1.0))
            res = xyz[idx, 2] - zr
            f = np.isfinite(res) & (np.abs(res) > lim)
            flag |= f
            notes.append(f"residual > limit: {int(f.sum()):,}")
        if mode in ("tpu", "either"):
            u = un.resolve({"tvu": p["unc"]["tvu"], "thu": None, "fill_order": p["unc"].get("fill_order", "1a")},
                           pc, idx, crs, depth, fill=None)
            tvu95 = u["tvu"] * un.Z95
            f = np.isfinite(tvu95) & (tvu95 > lim)
            flag |= f
            notes.append(f"own TPU > limit: {int(f.sum()):,} ({u['notes'][0]})")
        if progress:
            progress(1.0)
        return idx[flag], {"threshold": " · ".join(notes)}
    raise ValueError(method)
