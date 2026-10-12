"""Land analyses — terrain products, surface models from a classified cloud, change detection.

Every function returns a result dict that the Land dialog shows generically:
    {"kind", "title", "summary": [(label, text)], "tables": {name: [row dicts]},
     "layers": [(name, Dem)], "features": {name: [features]}, "chart": {...} (optional)}
"""
from __future__ import annotations

import numpy as np

from core.surface import Dem


def _report(progress, f):
    if progress is not None:
        progress(float(min(max(f, 0.0), 1.0)))


def _f(v, d=2, unit=""):
    return "—" if v is None or not np.isfinite(v) else f"{v:,.{d}f}{unit}"


def surface_grid(surface, cell=None, bounds=None):
    """(Z, x0, ytop, cell) of a DEM (its own grid) or a TIN (rasterised)."""
    from core.scour import sample_grid
    from core.volume import surface_bounds
    if isinstance(surface, Dem) and (cell is None or abs(cell - surface.cell) < 1e-12) and bounds is None:
        return surface.z.astype(np.float64), surface.x0, surface.ytop, surface.cell
    cell = cell or (surface.cell if isinstance(surface, Dem) else 1.0)
    x0, y0, x1, y1 = bounds or surface_bounds(surface)
    nc, nr = max(2, int(np.ceil((x1 - x0) / cell))), max(2, int(np.ceil((y1 - y0) / cell)))
    if nr * nc > 60_000_000:
        raise ValueError(f"{nr * nc:,} cells — use a larger cell size")
    return sample_grid(surface, x0, y1, cell, nr, nc), x0, y1, cell


def layer(z, x0, ytop, cell, values, title, palette="Turbo", symmetric=False, rng=None):
    v = values[np.isfinite(values)]
    if rng is None:
        if len(v) == 0:
            rng = (0.0, 1.0)
        elif symmetric:
            m = float(max(abs(np.percentile(v, 2)), abs(np.percentile(v, 98)), 1e-6))
            rng = (-m, m)
        else:
            rng = (float(np.percentile(v, 2)), float(np.percentile(v, 98)) + 1e-9)
    return Dem(np.where(np.isfinite(values), z, np.nan).astype(np.float32), x0, ytop, cell, "analysis",
               cvals=values.astype(np.float32), crange=rng, cpalette=palette, meta={"title": title})


# ================================================================ A. terrain products
def terrain_products(surface, cell=None, products=("slope", "aspect", "curvature", "roughness"), progress=None):
    """Slope (°), aspect (° from north, direction the slope faces / water runs), general curvature
    (1/100 m, + = convex ridge, − = concave hollow), terrain ruggedness (mean |Δz| to the 8 neighbours, m)."""
    Z, x0, ytop, c = surface_grid(surface, cell)
    _report(progress, 0.3)
    gr, gc = np.gradient(Z, c)                      # d/drow (south), d/dcol (east)
    slope = np.degrees(np.arctan(np.hypot(gr, gc)))
    out, rows = [], []
    a = c * c
    if "slope" in products:
        out.append(("Slope (°)", layer(Z, x0, ytop, c, slope, "slope °", "Turbo", rng=(0.0, max(5.0, float(
            np.nanpercentile(slope, 98)) if np.isfinite(slope).any() else 5.0)))))
        v = slope[np.isfinite(slope)]
        for t in (5, 10, 15, 30):
            rows.append({"product": "slope", "measure": f"area steeper than {t}°", "value": float((v > t).sum() * a),
                         "unit": "m²", "share_pct": float(100 * (v > t).mean()) if len(v) else np.nan})
        rows.append({"product": "slope", "measure": "mean / max", "value": float(v.mean()) if len(v) else np.nan,
                     "unit": "°", "share_pct": float(v.max()) if len(v) else np.nan})
    if "aspect" in products:
        asp = (np.degrees(np.arctan2(-gc, gr)) + 360.0) % 360.0         # downslope direction (east, north)
        asp = np.where(slope > 0.5, asp, np.nan)                          # flat: no aspect
        out.append(("Aspect (° from N)", layer(Z, x0, ytop, c, asp, "aspect °", "Rainbow", rng=(0.0, 360.0))))
        v = asp[np.isfinite(asp)]
        for k, nm in enumerate(("N", "NE", "E", "SE", "S", "SW", "W", "NW")):
            m = ((v - (k * 45 - 22.5)) % 360.0) < 45.0
            rows.append({"product": "aspect", "measure": f"facing {nm}", "value": float(m.sum() * a), "unit": "m²",
                         "share_pct": float(100 * m.mean()) if len(v) else np.nan})
    if "curvature" in products:
        d2r = np.gradient(gr, c, axis=0)
        d2c = np.gradient(gc, c, axis=1)
        curv = -(d2r + d2c) * 100.0
        out.append(("Curvature (1/100 m)", layer(Z, x0, ytop, c, curv, "curvature", "Diverging", symmetric=True)))
        v = curv[np.isfinite(curv)]
        rows.append({"product": "curvature", "measure": "convex area (ridges)", "value": float((v > 0.5).sum() * a),
                     "unit": "m²", "share_pct": float(100 * (v > 0.5).mean()) if len(v) else np.nan})
        rows.append({"product": "curvature", "measure": "concave area (hollows)", "value": float((v < -0.5).sum() * a),
                     "unit": "m²", "share_pct": float(100 * (v < -0.5).mean()) if len(v) else np.nan})
    if "roughness" in products:
        P = np.pad(Z, 1, mode="edge")
        acc = np.zeros_like(Z)
        cnt = np.zeros_like(Z)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == dc == 0:
                    continue
                d = np.abs(P[1 + dr:1 + dr + Z.shape[0], 1 + dc:1 + dc + Z.shape[1]] - Z)
                ok = np.isfinite(d)
                acc += np.where(ok, d, 0.0)
                cnt += ok
        tri = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
        tri = np.where(np.isfinite(Z), tri, np.nan)
        out.append(("Roughness TRI (m)", layer(Z, x0, ytop, c, tri, "ruggedness m", "Viridis")))
        v = tri[np.isfinite(tri)]
        rows.append({"product": "roughness", "measure": "mean / p95", "value": float(v.mean()) if len(v) else np.nan,
                     "unit": "m", "share_pct": float(np.percentile(v, 95)) if len(v) else np.nan})
    _report(progress, 1.0)
    area = float(np.isfinite(Z).sum() * a)
    return {"kind": "terrain", "title": "Terrain products",
            "summary": [("Grid", f"{Z.shape[1]} × {Z.shape[0]} cells of {c:g} m · {area:,.0f} m²"),
                        ("Layers", ", ".join(n for n, _ in out))],
            "tables": {"Statistics": rows}, "layers": out, "features": {}}


# ================================================================ A2. surface models from a classified cloud
REJECT = (7, 18)


def surface_models(pc, cell=1.0, veg_classes=(3, 4, 5), building_class=6, canopy_min=2.0, progress=None):
    """DSM (highest return per cell), DTM (ground class 2, triangulated), nDSM = DSM − DTM (object
    heights), CHM = highest vegetation return − DTM (canopy height)."""
    from core.surface import build_dem, grid_extent
    xyz = pc.xyz
    idx = pc.active_indices()
    if "Classification" not in pc.attrs:
        raise ValueError("The point cloud has no classification — run Classify (ground, vegetation, buildings) first")
    cls = np.asarray(pc.attrs["Classification"])
    idx = idx[~np.isin(cls[idx], REJECT)]
    g = idx[cls[idx] == 2]
    if len(g) < 3:
        raise ValueError("No ground points (class 2) — run the ground classification first")
    p = xyz[idx]
    x0, ytop, nr, nc = grid_extent(p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max(), cell, True)
    if nr * nc > 60_000_000:
        raise ValueError(f"{nr * nc:,} cells — use a larger cell size")
    _report(progress, 0.1)

    def gmax(ii):
        out = np.full(nr * nc, -np.inf)
        if len(ii):
            q = xyz[ii]
            c = np.clip(((q[:, 0] - x0) / cell).astype(np.int64), 0, nc - 1)
            r = np.clip(((ytop - q[:, 1]) / cell).astype(np.int64), 0, nr - 1)
            np.maximum.at(out, r * nc + c, q[:, 2])
        out[~np.isfinite(out)] = np.nan
        return out.reshape(nr, nc)

    dsm = gmax(idx)
    _report(progress, 0.3)
    dtm_dem = build_dem(xyz, g, cell, method="tin")
    from core.volume import sample_on_grid
    dtm = sample_on_grid(dtm_dem, x0, ytop, cell, nr, nc)
    _report(progress, 0.7)
    ndsm = np.clip(dsm - dtm, 0.0, None)
    veg = gmax(idx[np.isin(cls[idx], veg_classes)])
    chm = np.where(np.isfinite(veg), np.clip(veg - dtm, 0.0, None), np.where(np.isfinite(dtm), 0.0, np.nan))
    bld = gmax(idx[cls[idx] == building_class])
    a = cell * cell
    land = np.isfinite(dtm)
    cover = (chm >= canopy_min) & land
    bcover = np.isfinite(bld) & land
    hb = (bld - dtm)[bcover]
    _report(progress, 0.9)
    layers = [("DSM (surface)", Dem(dsm.astype(np.float32), x0, ytop, cell, "dsm", meta={"title": "DSM"})),
              ("DTM (ground)", Dem(dtm.astype(np.float32), x0, ytop, cell, "dtm", meta={"title": "DTM"})),
              ("nDSM (object height)", layer(dsm, x0, ytop, cell, ndsm, "object height m", "Viridis")),
              ("CHM (canopy height)", layer(np.where(np.isfinite(dsm), dsm, dtm), x0, ytop, cell, chm,
                                           "canopy height m", "Viridis"))]
    v = chm[cover]
    rows = [{"measure": "ground area (DTM)", "value": float(land.sum() * a), "unit": "m²"},
            {"measure": f"canopy cover (CHM ≥ {canopy_min:g} m)", "value": float(cover.sum() * a), "unit": "m²"},
            {"measure": "canopy cover", "value": float(100 * cover.sum() / max(land.sum(), 1)), "unit": "%"},
            {"measure": "canopy height mean / max", "value": float(v.mean()) if len(v) else np.nan,
             "unit": f"m (max {float(v.max()) if len(v) else np.nan:.1f})"},
            {"measure": "building footprint (class 6)", "value": float(bcover.sum() * a), "unit": "m²"},
            {"measure": "building height mean / max", "value": float(np.nanmean(hb)) if len(hb) else np.nan,
             "unit": f"m (max {float(np.nanmax(hb)) if len(hb) else np.nan:.1f})"}]
    _report(progress, 1.0)
    return {"kind": "models", "title": "Surface models",
            "summary": [("Grid", f"{nc} × {nr} cells of {cell:g} m"),
                        ("Canopy cover", f"{rows[2]['value']:.1f}% (CHM ≥ {canopy_min:g} m)"),
                        ("Buildings", f"{rows[4]['value']:,.0f} m² footprint")],
            "tables": {"Statistics": rows}, "layers": layers, "features": {}}


# ================================================================ B. change detection
def dod(A, B, cell=None, sigma_a=0.05, sigma_b=0.05, t=1.96, polygons=(), years=None, progress=None):
    """DEM of difference B − A with a level of detection LoD = t·√(σa² + σb²): only changes beyond the
    LoD count as erosion (−) or deposition (+). Volumes over the whole overlap and per polygon."""
    from core.volume import polygon_mask, surface_bounds
    a0 = surface_bounds(A)
    b0 = surface_bounds(B)
    bx = (max(a0[0], b0[0]), max(a0[1], b0[1]), min(a0[2], b0[2]), min(a0[3], b0[3]))
    if bx[2] <= bx[0] or bx[3] <= bx[1]:
        raise ValueError("The two surfaces do not overlap")
    cell = cell or max(getattr(A, "cell", 1.0), getattr(B, "cell", 1.0))
    ZA, x0, ytop, c = surface_grid(A, cell, bx)
    _report(progress, 0.3)
    ZB, *_ = surface_grid(B, cell, bx)
    _report(progress, 0.6)
    D = ZB - ZA
    lod = t * float(np.hypot(sigma_a, sigma_b))
    ok = np.isfinite(D)
    ero = ok & (D < -lod)
    dep = ok & (D > lod)
    a = c * c
    xs = x0 + (np.arange(D.shape[1]) + 0.5) * c
    ys = ytop - (np.arange(D.shape[0]) + 0.5) * c

    def stats(m, name):
        r = {"area": name, "compared_m2": float((ok & m).sum() * a),
             "erosion_m3": float(abs(D[ero & m].sum()) * a), "deposition_m3": float(D[dep & m].sum() * a),
             "erosion_m2": float((ero & m).sum() * a), "deposition_m2": float((dep & m).sum() * a),
             "below_lod_pct": float(100 * (ok & m & ~ero & ~dep).sum() / max((ok & m).sum(), 1)),
             "raw_net_m3": float(D[ok & m].sum() * a)}
        r["net_m3"] = r["deposition_m3"] - r["erosion_m3"]
        if years:
            r["net_m3_per_year"] = r["net_m3"] / years
        return r

    rows = [stats(np.ones_like(ok), "whole overlap")]
    feats = []
    for name, ring in polygons:
        m = polygon_mask(xs, ys, ring)
        rows.append(stats(m, name))
        feats.append({"type": "Polygon", "parts": [ring], "attrs": {"NAME": name, "NET_M3": rows[-1]["net_m3"],
                                                                   "EROS_M3": rows[-1]["erosion_m3"],
                                                                   "DEPO_M3": rows[-1]["deposition_m3"]}})
    shown = np.where(ok, np.where(ero | dep, D, 0.0), np.nan)
    _report(progress, 1.0)
    w = rows[0]
    return {"kind": "dod", "title": "Change detection (DoD)",
            "summary": [("Level of detection", f"±{lod:.3f} m (t = {t:g}, σ {sigma_a:g} / {sigma_b:g} m)"),
                        ("Erosion", f"{w['erosion_m3']:,.1f} m³ on {w['erosion_m2']:,.0f} m²"),
                        ("Deposition", f"{w['deposition_m3']:,.1f} m³ on {w['deposition_m2']:,.0f} m²"),
                        ("Net (beyond LoD)", f"{w['net_m3']:+,.1f} m³" + (f" · {w['net_m3_per_year']:+,.1f} m³/yr"
                                                                          if years else "")),
                        ("Below LoD", f"{w['below_lod_pct']:.1f}% of the compared area")],
            "tables": {"Volumes": rows}, "features": {"areas": feats} if feats else {},
            "layers": [("Change B − A (beyond LoD)", layer(ZB, x0, ytop, c, shown, "change m", "Diverging",
                                                           symmetric=True))]}


def m3c2(pc1, pc2, spacing=1.0, normal_scale=2.0, cyl_diameter=1.0, max_depth=5.0, reg_error=0.0,
         max_cores=60_000, progress=None):
    """Simplified M3C2 (Lague et al. 2013): distance between two clouds along local surface normals
    at core points of cloud 1, averaged over a cylinder, with a 95 % confidence level of detection
    LoD = 1.96·√(s1²/n1 + s2²/n2) + registration error. Suited to steep slopes and cliffs where a
    DEM difference fails. Normals point up (+Z): positive = surface moved out / up."""
    from scipy.spatial import cKDTree
    i1, i2 = pc1.active_indices(), pc2.active_indices()
    P1, P2 = pc1.xyz[i1], pc2.xyz[i2]
    # core points: one per spacing voxel of cloud 1
    key = np.floor((P1 - P1.min(axis=0)) / spacing).astype(np.int64)
    _, first = np.unique(key, axis=0, return_index=True)
    cores = P1[np.sort(first)]
    if len(cores) > max_cores:
        cores = cores[np.random.default_rng(0).choice(len(cores), max_cores, replace=False)]
    _report(progress, 0.1)
    t1, t2 = cKDTree(P1), cKDTree(P2)
    nb = t1.query_ball_point(cores, normal_scale / 2.0)
    N = np.zeros((len(cores), 3))
    for k, ii in enumerate(nb):
        if len(ii) >= 3:
            Q = P1[ii] - P1[ii].mean(axis=0)
            w, v = np.linalg.eigh(Q.T @ Q)
            n = v[:, 0]
            N[k] = n if n[2] >= 0 else -n
        else:
            N[k] = (0, 0, 1)
        if k % 5000 == 0:
            _report(progress, 0.1 + 0.4 * k / len(cores))
    r = cyl_diameter / 2.0
    R = float(np.hypot(r, max_depth))
    out = np.full((len(cores), 6), np.nan)                 # dist, lod, n1, n2, s1, s2

    def cyl(P, tree, k):
        ii = tree.query_ball_point(cores[k], R)
        if not ii:
            return np.empty(0)
        v = P[ii] - cores[k]
        al = v @ N[k]
        rad = np.linalg.norm(v - np.outer(al, N[k]), axis=1)
        return al[(rad <= r) & (np.abs(al) <= max_depth)]

    for k in range(len(cores)):
        a1, a2 = cyl(P1, t1, k), cyl(P2, t2, k)
        if len(a1) >= 2 and len(a2) >= 2:
            s1, s2 = a1.std(ddof=1), a2.std(ddof=1)
            out[k] = (a2.mean() - a1.mean(), 1.96 * np.sqrt(s1 * s1 / len(a1) + s2 * s2 / len(a2)) + reg_error,
                      len(a1), len(a2), s1, s2)
        if k % 5000 == 0:
            _report(progress, 0.5 + 0.45 * k / len(cores))
    d, lod = out[:, 0], out[:, 1]
    ok = np.isfinite(d)
    sig = ok & (np.abs(d) > lod)
    # 2.5D layer: mean significant distance per cell of `spacing`
    from core.surface import grid_extent
    x0, ytop, nr, nc = grid_extent(cores[:, 0].min(), cores[:, 1].min(), cores[:, 0].max(), cores[:, 1].max(),
                                   spacing, True)
    ci = np.clip(((cores[:, 0] - x0) / spacing).astype(int), 0, nc - 1)
    ri = np.clip(((ytop - cores[:, 1]) / spacing).astype(int), 0, nr - 1)
    s_d, s_z, cnt = (np.zeros(nr * nc) for _ in range(3))
    np.add.at(s_d, (ri * nc + ci)[ok], np.where(sig, d, 0.0)[ok])
    np.add.at(s_z, (ri * nc + ci)[ok], cores[ok, 2])
    np.add.at(cnt, (ri * nc + ci)[ok], 1)
    with np.errstate(invalid="ignore"):
        Dg = (s_d / cnt).reshape(nr, nc)
        Zg = (s_z / cnt).reshape(nr, nc)
    _report(progress, 1.0)
    rows = [{"x": float(c[0]), "y": float(c[1]), "z": float(c[2]), "nx": float(n[0]), "ny": float(n[1]),
             "nz": float(n[2]), "distance": float(o[0]), "lod95": float(o[1]), "n1": float(o[2]), "n2": float(o[3]),
             "significant": bool(s)} for c, n, o, s in zip(cores, N, out, sig)]
    dv = d[sig]
    return {"kind": "m3c2", "title": "Change detection (M3C2)",
            "summary": [("Core points", f"{len(cores):,} (spacing {spacing:g} m) · measured {ok.sum():,}"),
                        ("Significant change", f"{sig.sum():,} core points ({100 * sig.sum() / max(ok.sum(), 1):.1f}%)"),
                        ("Distance (significant)", f"mean {_f(dv.mean() if len(dv) else np.nan, 3)} m · "
                         f"min {_f(dv.min() if len(dv) else np.nan, 3)} · max {_f(dv.max() if len(dv) else np.nan, 3)}"),
                        ("Median LoD95", f"{_f(np.nanmedian(lod), 3)} m"),
                        ("Note", "positive = surface moved out along the normal (up / towards the viewer of a cliff)")],
            "tables": {"Core points": rows}, "features": {},
            "layers": [("M3C2 distance (significant)", layer(Zg, x0, ytop, spacing, Dg, "M3C2 m", "Diverging",
                                                             symmetric=True))]}
