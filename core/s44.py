"""IHO S-44 (Edition 6.0.0, 2020) — Standards for Hydrographic Surveys: minimum bathymetry standards.

  TVU (95 %) = √(a² + (b·d)²)       THU (95 %) = c + p·d       d = depth (m)

Checks provided (each per sounding, summarised as % within the limit; S-44 asks for 95 %):
  a-priori      the soundings' own TPU (TVU / THU) against the limits of an order
  a-posteriori  residuals to a reference (CUBE / median surface, TIN or DEM) against the TVU limit
  crosslines    main-scheme surface vs. independent crossline soundings (difference statistics)
  coverage      share of the survey area with soundings at a given grid size (holidays)
Feature detection cannot be proven from statistics; only whether the sounding density could resolve the
stated feature size is reported. Results support — never replace — the surveyor's assessment.
"""
from __future__ import annotations

import numpy as np

# order: (TVU a, TVU b, THU c, THU p, coverage %, feature size m or None)
ORDERS = {
    "exclusive": (0.15, 0.0075, 1.0, 0.0, 200.0, 0.5),
    "special": (0.25, 0.0075, 2.0, 0.0, 100.0, 1.0),
    "1a": (0.50, 0.013, 5.0, 0.05, 100.0, 2.0),
    "1b": (0.50, 0.013, 5.0, 0.05, 5.0, None),
    "2": (1.00, 0.023, 20.0, 0.10, 5.0, None),
}
ORDER_LABEL = {"exclusive": "Exclusive Order", "special": "Special Order", "1a": "Order 1a", "1b": "Order 1b",
               "2": "Order 2"}
EDITION = "IHO S-44 Edition 6.0.0 (2020)"


def allowed_tvu(order, depth):
    a, b = ORDERS[order][:2]
    d = np.asarray(depth, float)
    return np.sqrt(a * a + (b * d) ** 2)


def allowed_thu(order, depth):
    c, p = ORDERS[order][2:4]
    return c + p * np.asarray(depth, float)


def feature_size(order, depth):
    """Feature detection size (m): 1a → 2 m to 40 m depth, 10 % of depth beyond."""
    f = ORDERS[order][5]
    if f is None:
        return np.full(np.shape(depth), np.nan)
    d = np.asarray(depth, float)
    if order == "1a":
        return np.where(d > 40.0, 0.1 * d, f)
    return np.full(d.shape, f)


def _pct(mask):
    return float(100.0 * np.mean(mask)) if len(mask) else float("nan")


def best_order(frac_by_order, need=95.0):
    for o in ("exclusive", "special", "1a", "1b", "2"):
        v = frac_by_order.get(o)
        if v is not None and np.isfinite(v) and v >= need:
            return o
    return None


# ---------------------------------------------------------------- checks
def apriori(depth, tvu95=None, thu95=None):
    """% of soundings whose own TPU (95 %) is within the limits, per order."""
    out = {}
    for o in ORDERS:
        r = {}
        if tvu95 is not None:
            r["tvu_pct"] = _pct(tvu95 <= allowed_tvu(o, depth))
        if thu95 is not None:
            r["thu_pct"] = _pct(thu95 <= allowed_thu(o, depth))
        out[o] = r
    return out


def _cell_medians(p, lo, cell, min_points=3):
    """Median Z of the occupied plan cells only (no dense grid — corridors of tens of km stay small).
    Returns sorted cell keys, medians, number of columns used in the key."""
    ij = np.floor((p[:, :2] - lo) / cell).astype(np.int64)
    nx = int(ij[:, 0].max()) + 2
    key = ij[:, 1] * nx + ij[:, 0]
    order = np.lexsort((p[:, 2], key))
    ks = key[order]
    zs = p[order, 2]
    st = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    cnt = np.diff(np.r_[st, len(ks)])
    mid = st + cnt // 2
    med = np.where(cnt % 2 == 1, zs[mid], 0.5 * (zs[np.maximum(mid - 1, st)] + zs[mid]))
    ok = cnt >= min_points
    return ks[st][ok], med[ok], nx


def _sparse_bilinear(keys, vals, nx, lo, cell, xy):
    """Bilinear interpolation between the centres of occupied cells; missing corners are left out and the
    weights renormalised (so single-cell gaps are bridged, empty areas give NaN)."""
    fx = (xy[:, 0] - lo[0]) / cell - 0.5
    fy = (xy[:, 1] - lo[1]) / cell - 0.5
    x0 = np.floor(fx).astype(np.int64)
    y0 = np.floor(fy).astype(np.int64)
    tx, ty = fx - x0, fy - y0
    acc = np.zeros(len(xy))
    wsum = np.zeros(len(xy))
    for dx, dy, w in ((0, 0, (1 - tx) * (1 - ty)), (1, 0, tx * (1 - ty)), (0, 1, (1 - tx) * ty), (1, 1, tx * ty)):
        cx, cy = x0 + dx, y0 + dy
        ok = (cx >= 0) & (cy >= 0) & (cx < nx)
        k = cy * nx + cx
        j = np.clip(np.searchsorted(keys, k), 0, max(len(keys) - 1, 0))
        hit = ok & (len(keys) > 0) & (keys[j] == k) if len(keys) else np.zeros(len(xy), bool)
        acc[hit] += vals[j[hit]] * w[hit]
        wsum[hit] += w[hit]
    out = np.full(len(xy), np.nan)
    good = wsum > 1e-9
    out[good] = acc[good] / wsum[good]
    return out


def local_reference(xyz, idx, cell, min_points=3):
    """Median surface of plan cells, interpolated between cell centres (bilinear) — slope removed.
    Sparse: only occupied cells are stored. Returns z_ref for the points (NaN where no reference)."""
    p = xyz[idx]
    lo = p[:, :2].min(axis=0)
    keys, med, nx = _cell_medians(p, lo, cell, min_points)
    return _sparse_bilinear(keys, med, nx, lo, cell, p[:, :2])


def aposteriori(depth, residual, orders=ORDERS):
    out = {}
    r = np.abs(np.asarray(residual, float))
    ok = np.isfinite(r)
    for o in orders:
        lim = allowed_tvu(o, depth)
        out[o] = {"tvu_pct": _pct((r <= lim)[ok]) if ok.any() else float("nan")}
    return out


def residual_stats(res):
    r = np.asarray(res, float)
    r = r[np.isfinite(r)]
    if not len(r):
        return {"n": 0}
    return {"n": int(len(r)), "mean": float(r.mean()), "std": float(r.std()), "median": float(np.median(r)),
            "p95_abs": float(np.percentile(np.abs(r), 95)), "max_abs": float(np.abs(r).max())}


def crosslines(main_xyz, main_idx, cross_xyz, cross_idx, cell, min_points=3):
    """Differences crossline − main-scheme surface (median cells of the main lines, interpolated)."""
    p = main_xyz[main_idx]
    lo = p[:, :2].min(axis=0)
    keys, med, nx = _cell_medians(p, lo, cell, min_points)
    q = cross_xyz[cross_idx]
    zm = _sparse_bilinear(keys, med, nx, lo, cell, q[:, :2])
    return q[:, 2] - zm


def coverage(xyz, idx, cell, min_points=1, close_cells=2):
    """Coverage of the surveyed area at `cell` (m): the area is the occupied cells closed by
    `close_cells` (so gaps inside the survey count as holidays, the outside does not).
    Returns dict(pct, cells, filled, holidays, grid (bool filled), area mask, x0, ytop, cell)."""
    from scipy import ndimage
    p = xyz[idx]
    lo = p[:, :2].min(axis=0)
    hi = p[:, :2].max(axis=0)
    nx = int(np.floor((hi[0] - lo[0]) / cell)) + 1
    ny = int(np.floor((hi[1] - lo[1]) / cell)) + 1
    if float(nx) * ny > 40_000_000:
        return _coverage_tiled(p, lo, nx, ny, cell, min_points, close_cells)
    ij = np.floor((p[:, :2] - lo) / cell).astype(np.int64)
    cnt = np.zeros((ny, nx), np.int32)
    np.add.at(cnt, (ij[:, 1], ij[:, 0]), 1)
    filled = cnt >= min_points
    area = ndimage.binary_closing(cnt > 0, np.ones((3, 3), bool), iterations=max(close_cells, 1))
    area = ndimage.binary_fill_holes(area | filled)
    hol = area & ~filled
    lab, nh = ndimage.label(hol)
    return {"pct": float(100.0 * filled[area].mean()) if area.any() else float("nan"),
            "cells": int(area.sum()), "filled": int((filled & area).sum()), "holidays": int(nh),
            "holiday_cells": int(hol.sum()), "count": cnt, "area": area, "x0": float(lo[0]),
            "ytop": float(lo[1] + ny * cell), "cell": float(cell)}


def _coverage_tiled(p, lo, nx, ny, cell, min_points, close_cells, B=2048):
    """Coverage of a large extent (long corridors): only tiles with soundings are processed, each with a
    halo; holidays crossing tile borders may be counted once per tile. No map grid is returned."""
    from scipy import ndimage
    ij = np.floor((p[:, :2] - lo) / cell).astype(np.int64)
    tkey = (ij[:, 1] // B) * ((nx + B - 1) // B) + ij[:, 0] // B
    o = np.argsort(tkey, kind="stable")
    tk = tkey[o]
    st = np.flatnonzero(np.r_[True, tk[1:] != tk[:-1]])
    en = np.r_[st[1:], len(tk)]
    ntx = (nx + B - 1) // B
    tiles = {int(tk[s_]): o[s_:e_] for s_, e_ in zip(st, en)}
    h = 2 * max(close_cells, 1) + 2
    cells = filled = holes = hol_cells = 0
    for t, pts in tiles.items():
        ty, tx = divmod(t, ntx)
        x0, y0 = tx * B - h, ty * B - h
        sel = [pts]
        for dy in (-1, 0, 1):                                 # halo from the neighbouring tiles
            for dx in (-1, 0, 1):
                if (dx or dy) and (t + dy * ntx + dx) in tiles and 0 <= tx + dx < ntx:
                    sel.append(tiles[t + dy * ntx + dx])
        q = ij[np.concatenate(sel)] - [x0, y0]
        m = (q[:, 0] >= 0) & (q[:, 1] >= 0) & (q[:, 0] < B + 2 * h) & (q[:, 1] < B + 2 * h)
        q = q[m]
        cnt = np.zeros((B + 2 * h, B + 2 * h), np.int32)
        np.add.at(cnt, (q[:, 1], q[:, 0]), 1)
        fil = cnt >= min_points
        area = ndimage.binary_closing(cnt > 0, np.ones((3, 3), bool), iterations=max(close_cells, 1))
        area = ndimage.binary_fill_holes(area | fil)
        core = (slice(h, h + B), slice(h, h + B))
        a, f_ = area[core], fil[core]
        cells += int(a.sum())
        filled += int((a & f_).sum())
        hol = a & ~f_
        hol_cells += int(hol.sum())
        holes += int(ndimage.label(hol)[1])
    return {"pct": float(100.0 * filled / cells) if cells else float("nan"), "cells": cells, "filled": filled,
            "holidays": holes, "holiday_cells": hol_cells, "x0": float(lo[0]), "ytop": float(lo[1] + ny * cell),
            "cell": float(cell), "tiled": True}


def feature_density_check(order, depth_med, spacing):
    """Can the sounding density resolve the order's feature size? (≥ 3 soundings across the feature)"""
    fs = float(feature_size(order, depth_med)) if ORDERS[order][5] is not None else float("nan")
    if not np.isfinite(fs):
        return {"feature_m": None, "spacing_m": spacing, "ok": None}
    return {"feature_m": fs, "spacing_m": spacing, "ok": bool(spacing <= fs / 3.0)}


# ---------------------------------------------------------------- complete assessment
def assess(xyz, idx, order="1a", depth_positive_down=True, tvu95=None, thu95=None, reference=None,
           ref_cell=1.0, cross=None, cov_cell=None, cov_min=1, progress=None):
    """Full S-44 assessment of the points idx for one target order (the other orders are reported too).
    reference: None → local median surface (ref_cell); or z_ref per point (array).
    cross: (cross_xyz, cross_idx) for a crossline comparison. Returns a dict (see keys)."""
    from core.surface import mean_spacing
    idx = np.asarray(idx)
    z = xyz[idx, 2]
    d = -z if depth_positive_down else z
    d = np.abs(d)
    rep = {"edition": EDITION, "order": order, "points": int(len(idx)),
           "depth_min": float(np.nanmin(d)) if len(d) else float("nan"),
           "depth_max": float(np.nanmax(d)) if len(d) else float("nan"),
           "depth_median": float(np.nanmedian(d)) if len(d) else float("nan")}
    if progress:
        progress(0.05)
    rep["limits"] = {o: {"tvu_at_median": float(allowed_tvu(o, rep["depth_median"])),
                         "thu_at_median": float(allowed_thu(o, rep["depth_median"]))} for o in ORDERS}
    if tvu95 is not None or thu95 is not None:
        rep["apriori"] = apriori(d, tvu95, thu95)
    zr = reference if reference is not None else local_reference(xyz, idx, ref_cell)
    res = z - zr
    rep["residual"] = residual_stats(res)
    rep["aposteriori"] = aposteriori(d, res)
    lim = allowed_tvu(order, d)
    fail = np.isfinite(res) & (np.abs(res) > lim)
    if tvu95 is not None:
        fail_tpu = tvu95 > lim
    else:
        fail_tpu = np.zeros(len(idx), bool)
    rep["fail_residual"] = int(fail.sum())
    rep["fail_tpu"] = int(fail_tpu.sum())
    if progress:
        progress(0.5)
    if cross is not None:
        dif = crosslines(xyz, idx, cross[0], cross[1], ref_cell)
        dc = np.abs(cross[0][cross[1], 2])
        rep["crosslines"] = residual_stats(dif)
        rep["crosslines"]["by_order"] = aposteriori(dc, dif)
    if progress:
        progress(0.7)
    sp = mean_spacing(xyz, idx)
    rep["spacing"] = float(sp)
    cc = cov_cell or (feature_size(order, rep["depth_median"]) if ORDERS[order][5] else max(2 * sp, 1.0))
    cc = float(np.nan_to_num(cc, nan=max(2 * sp, 1.0)))
    cov = coverage(xyz, idx, cc, cov_min)
    rep["coverage"] = {k: v for k, v in cov.items() if not isinstance(v, np.ndarray)}
    rep["coverage"]["required_pct"] = ORDERS[order][4]
    rep["feature"] = feature_density_check(order, rep["depth_median"], sp)
    by = {o: rep["aposteriori"][o]["tvu_pct"] for o in ORDERS}
    rep["best_order_aposteriori"] = best_order(by)
    if "apriori" in rep and tvu95 is not None:
        rep["best_order_apriori"] = best_order({o: rep["apriori"][o].get("tvu_pct") for o in ORDERS})
    if progress:
        progress(1.0)
    return rep, {"fail_residual": fail, "fail_tpu": fail_tpu, "residual": res, "depth": d,
                 "coverage": cov, "limit": lim}


def map_cell(xyz, idx, cell, max_cells=40_000_000):
    """Cell size for a map layer over the points' extent, enlarged if the grid would be too large."""
    p = xyz[idx]
    ext = p[:, :2].max(axis=0) - p[:, :2].min(axis=0)
    n = (ext[0] / cell + 1) * (ext[1] / cell + 1)
    return float(cell * np.sqrt(n / max_cells)) if n > max_cells else float(cell)


def compliance_grid(xyz, idx, residual, limit, cell):
    """Per cell: % of soundings within the TVU limit (for a map layer). Returns (Z, x0, ytop)."""
    p = xyz[idx]
    lo = p[:, :2].min(axis=0)
    hi = p[:, :2].max(axis=0)
    nx = int(np.floor((hi[0] - lo[0]) / cell)) + 1
    ny = int(np.floor((hi[1] - lo[1]) / cell)) + 1
    ij = np.floor((p[:, :2] - lo) / cell).astype(np.int64)
    ok = np.isfinite(residual)
    tot = np.zeros((ny, nx))
    good = np.zeros((ny, nx))
    np.add.at(tot, (ij[ok, 1], ij[ok, 0]), 1)
    np.add.at(good, (ij[ok, 1], ij[ok, 0]), (np.abs(residual[ok]) <= limit[ok]).astype(float))
    with np.errstate(invalid="ignore", divide="ignore"):
        pct = np.where(tot > 0, 100.0 * good / tot, np.nan)
    return pct[::-1], float(lo[0]), float(lo[1] + ny * cell)        # row 0 = north (Dem convention)
