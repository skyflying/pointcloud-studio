"""Earthworks design: dredge / fill to a level with side slopes, cable trench along a route, slope maps.

Sign convention everywhere: Δ = design − existing  →  negative = cut (dredge), positive = fill.
Side slopes are 1 : n  (vertical : horizontal); slopes are generated with a Euclidean distance transform,
i.e. constant slope in every direction (rounded corners, as built by a dredger / excavator).
"""
from __future__ import annotations

import numpy as np

from core.surface import Dem
from core.volume import polygon_mask, polyline_corridor_mask, sample_on_grid, surface_bounds


def _report(progress, f):
    if progress:
        progress(min(max(f, 0.0), 1.0))


def _grid_for(surface, bbox, cell, margin):
    """Grid aligned with a DEM (or free for a TIN) covering bbox + margin, clipped to the surface."""
    sx0, sy0, sx1, sy1 = surface_bounds(surface)
    x0, y0, x1, y1 = bbox
    x0, y0 = max(sx0, x0 - margin), max(sy0, y0 - margin)
    x1, y1 = min(sx1, x1 + margin), min(sy1, y1 + margin)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("The design area does not overlap the surface")
    if isinstance(surface, Dem) and abs(cell - surface.cell) < 1e-12:
        gx0 = surface.x0 + np.floor((x0 - surface.x0) / cell) * cell
        gtop = surface.ytop - np.floor((surface.ytop - y1) / cell) * cell
    else:
        gx0, gtop = x0, y1
    nc = max(1, int(np.ceil((x1 - gx0) / cell)))
    nr = max(1, int(np.ceil((gtop - y0) / cell)))
    if nr * nc > 60_000_000:
        raise ValueError(f"Design grid too large ({nc} × {nr}); use a larger cell size")
    xs = gx0 + (np.arange(nc) + 0.5) * cell
    ys = gtop - (np.arange(nr) + 0.5) * cell
    return gx0, gtop, nr, nc, xs, ys


def _stats(S, G, cell, footprint):
    ok = np.isfinite(S) & np.isfinite(G)
    dz = np.where(ok, G - S, np.nan)
    a = cell * cell
    cut = np.where(ok & (dz < 0), -dz, 0.0)
    fill = np.where(ok & (dz > 0), dz, 0.0)
    return dz, {
        "cut_volume": float(cut.sum() * a), "fill_volume": float(fill.sum() * a),
        "net_volume": float((fill - cut).sum() * a),
        "cut_area": float((cut > 1e-9).sum() * a), "fill_area": float((fill > 1e-9).sum() * a),
        "footprint_area": float((footprint & ok).sum() * a),
        "affected_area": float(((cut > 1e-9) | (fill > 1e-9)).sum() * a),
        "max_cut": float(cut.max()) if cut.size else 0.0, "max_fill": float(fill.max()) if fill.size else 0.0,
        "missing_in_footprint": float((footprint & ~ok).sum() * a),
    }


def _apply_slopes(S, F, level, dist, mode, cut_n, fill_n):
    """Design surface G from the existing surface S, footprint F (bool), design level (scalar or grid)."""
    G = S.copy()
    lev = np.broadcast_to(level, S.shape)
    if mode in ("level", "cut"):
        inside_cut = F & (S > lev)
        G[inside_cut] = lev[inside_cut]
        if cut_n > 0:
            slope = lev + dist / max(cut_n, 1e-9)
            out = ~F & (S > slope)
            G[out] = slope[out]
    if mode in ("level", "fill"):
        inside_fill = F & (S < lev)
        G[inside_fill] = lev[inside_fill]
        if fill_n > 0:
            slope = lev - dist / max(fill_n, 1e-9)
            out = ~F & (S < slope)
            G[out] = slope[out]
    return G


def dredge_design(surface, region, level, mode="level", overdredge=0.0, cut_n=3.0, fill_n=3.0, cell=None,
                  progress=None):
    """region: ("polygon", xy) | ("corridor", line_xy, half_width).  level: design ELEVATION (Z up).
    mode: 'level' (dredge + fill), 'cut' (dredge only), 'fill' (fill only).
    cut_n / fill_n: horizontal per 1 vertical (0 = vertical walls)."""
    from scipy import ndimage
    cell = cell or (surface.cell if isinstance(surface, Dem) else 1.0)
    rxy = np.asarray(region[1], float)
    half = region[2] if region[0] == "corridor" else 0.0
    bbox = (rxy[:, 0].min() - half, rxy[:, 1].min() - half, rxy[:, 0].max() + half, rxy[:, 1].max() + half)
    # slope margin from the local relief around the area
    pc_ = max(cell, 1.0)
    g = _grid_for(surface, bbox, pc_, 0)
    probe = sample_on_grid(surface, g[0], g[1], pc_, g[2], g[3])
    relief = float(np.nanmax(np.abs(probe - level))) if np.isfinite(probe).any() else 0.0
    margin = relief * max(cut_n, fill_n) + 3 * cell
    x0, ytop, nr, nc, xs, ys = _grid_for(surface, bbox, cell, margin)
    _report(progress, 0.15)
    S = sample_on_grid(surface, x0, ytop, cell, nr, nc)
    F = polygon_mask(xs, ys, rxy) if region[0] == "polygon" else polyline_corridor_mask(xs, ys, rxy, half)
    if not F.any():
        raise ValueError("The design footprint is smaller than one grid cell — use a smaller cell size")
    _report(progress, 0.35)
    # distance from the footprint EDGE (edt measures to the nearest footprint cell centre → minus half a cell)
    dist = np.maximum(ndimage.distance_transform_edt(~F, sampling=cell) - cell / 2, 0.0)
    G = _apply_slopes(S, F, level, dist, mode, cut_n, fill_n)
    dz, st = _stats(S, G, cell, F)
    _report(progress, 0.7)
    if overdredge > 0 and mode in ("level", "cut"):
        G_od = _apply_slopes(S, F, level - overdredge, dist, "cut", cut_n, fill_n)
        _, st_od = _stats(S, G_od, cell, F)
        st["cut_volume_overdredge"] = st_od["cut_volume"]
    st.update(cell=cell, level=level, mode=mode, overdredge=overdredge, cut_n=cut_n, fill_n=fill_n)
    _report(progress, 1.0)
    return {"S": S.astype(np.float32), "G": G.astype(np.float32), "dz": dz.astype(np.float32),
            "x0": float(x0), "ytop": float(ytop), "cell": float(cell), "stats": st}


def trench_design(surface, route_xy, kp_vertices, bottom_width=1.0, slope_n=1.0, depth=1.5, ref="seabed",
                  level=None, smooth=20.0, kp_step=0.1, cell=None, progress=None):
    """Cable trench along a polyline.  ref='seabed': bottom = smoothed seabed on the centre line − depth (DOL);
    ref='level': bottom = absolute elevation `level`.  Cross-section: flat bottom of `bottom_width`, sides 1:slope_n
    up to the seabed (cut only).  Trench only between the route ends (no rounded end caps).
    kp_step (km): volume table interval."""
    from core import profile as prof
    cell = cell or (surface.cell if isinstance(surface, Dem) else 0.5)
    xy = np.asarray(route_xy, float)
    sec = prof.Section("trench", xy)
    L = sec.length
    # bottom elevation along the route
    n = max(2, int(np.ceil(L / (cell / 2))) + 1)
    ch_s = np.linspace(0, L, n)
    if ref == "seabed":
        from core.filters import surface_z
        p = sec.xy_at(sec.disp(ch_s))
        zc = surface_z(surface, p[:, 0], p[:, 1])
        ok = np.isfinite(zc)
        if not ok.any():
            raise ValueError("The route does not cross the surface")
        zc = np.interp(ch_s, ch_s[ok], zc[ok])
        k = max(1, int(round(smooth / (ch_s[1] - ch_s[0]))))
        if k > 1:                                                  # moving average (edge-corrected)
            ker = np.ones(k) / k
            num = np.convolve(zc, ker, mode="same")
            den = np.convolve(np.ones_like(zc), ker, mode="same")
            zc = num / den
        bottom_s = zc - depth
    else:
        bottom_s = np.full(n, float(level))
    relief = float(np.nanmax(bottom_s) - np.nanmin(bottom_s)) + depth + 5
    half_top = bottom_width / 2 + relief * slope_n + 2 * cell
    bbox = (xy[:, 0].min() - half_top, xy[:, 1].min() - half_top, xy[:, 0].max() + half_top, xy[:, 1].max() + half_top)
    x0, ytop, nr, nc, xs, ys = _grid_for(surface, bbox, cell, 0)
    _report(progress, 0.2)
    S = sample_on_grid(surface, x0, ytop, cell, nr, nc)
    X, Y = np.meshgrid(xs, ys)
    P = np.column_stack([X.ravel(), Y.ravel()])
    # chainage / perpendicular offset of every cell (nearest segment, no end caps)
    P0, P1 = xy[:-1], xy[1:]
    Ls = np.hypot(*(P1 - P0).T)
    D = (P1 - P0) / np.maximum(Ls, 1e-12)[:, None]
    cum = np.r_[0, np.cumsum(Ls)]
    best = np.full(len(P), np.inf)
    ch = np.full(len(P), np.nan)
    for i in range(len(P0)):
        v = P - P0[i]
        t = v @ D[i]
        perp = np.abs(v[:, 0] * D[i, 1] - v[:, 1] * D[i, 0])
        inside = (t >= 0) & (t <= Ls[i])
        better = inside & (perp < best)
        best[better] = perp[better]
        ch[better] = cum[i] + t[better]
    _report(progress, 0.6)
    bottom = np.interp(ch, ch_s, bottom_s)
    # side slope 1 : n  → the wall rises 1 m for every n m horizontally
    trench = (bottom + np.maximum(best - bottom_width / 2, 0) / slope_n if slope_n > 0
              else np.where(best <= bottom_width / 2, bottom, np.inf))
    trench = trench.reshape(nr, nc)
    G = np.where(np.isfinite(trench) & np.isfinite(S), np.minimum(S, trench), S)
    F = (best <= bottom_width / 2).reshape(nr, nc)
    dz, st = _stats(S, G, cell, F)
    # volume per KP interval
    cutv = np.where(np.isfinite(dz) & (dz < 0), -dz, 0.0).ravel() * cell * cell
    kp = (np.interp(np.nan_to_num(ch, nan=-1), cum, np.asarray(kp_vertices, float)) if kp_vertices is not None
          else ch / 1000.0)
    valid = np.isfinite(ch) & (cutv > 0)
    k0, k1 = (float(np.min(kp_vertices)), float(np.max(kp_vertices))) if kp_vertices is not None else (0.0, L / 1000)
    edges = np.arange(np.floor(k0 / kp_step) * kp_step, k1 + kp_step, kp_step)
    vols, _ = np.histogram(kp[valid], bins=edges, weights=cutv[valid])
    table = [(float(edges[i]), float(edges[i + 1]), float(vols[i])) for i in range(len(vols))]
    # nominal cross-section area on flat seabed: b·d + n·d²
    st.update(cell=cell, bottom_width=bottom_width, slope_n=slope_n, depth=depth, ref=ref, length=float(L),
              nominal_section=float(bottom_width * depth + slope_n * depth ** 2), kp_step=kp_step,
              mean_section=float(st["cut_volume"] / L) if L > 0 else 0.0)
    _report(progress, 1.0)
    return {"S": S.astype(np.float32), "G": G.astype(np.float32), "dz": dz.astype(np.float32),
            "x0": float(x0), "ytop": float(ytop), "cell": float(cell), "stats": st, "table": table,
            "bottom": (ch_s, bottom_s)}


def slope_map(surface, cell=None, thresholds=(5.0, 10.0, 15.0), progress=None):
    """Slope in degrees (central differences) on the surface's grid (TIN → rasterised)."""
    cell = cell or (surface.cell if isinstance(surface, Dem) else 1.0)
    x0, y0, x1, y1 = surface_bounds(surface)
    nc, nr = max(2, int(np.ceil((x1 - x0) / cell))), max(2, int(np.ceil((y1 - y0) / cell)))
    if isinstance(surface, Dem) and abs(cell - surface.cell) < 1e-12:
        z = surface.z.astype(np.float64)
        x0, ytop = surface.x0, surface.ytop
    else:
        ytop = y1
        z = sample_on_grid(surface, x0, ytop, cell, nr, nc)
    _report(progress, 0.5)
    gy, gx = np.gradient(z, cell)
    slope = np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)
    s = slope[np.isfinite(slope)]
    st = {"mean": float(s.mean()) if len(s) else np.nan, "max": float(s.max()) if len(s) else np.nan,
          "p95": float(np.percentile(s, 95)) if len(s) else np.nan, "cell": cell,
          "above": {float(t): float((s > t).mean()) if len(s) else 0.0 for t in thresholds}}
    _report(progress, 1.0)
    return {"slope": slope, "z": z.astype(np.float32), "x0": float(x0), "ytop": float(ytop), "cell": float(cell),
            "stats": st}
