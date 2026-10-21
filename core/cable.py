"""Analyses along a cable route: depth of burial, free spans, rock berm as-built verification.

Conventions (as everywhere in the program): Z is elevation, positive up. A cable profile is a Route
whose `z` holds the elevation of the TOP of the cable at each vertex.

    depth of burial  DoB   = seabed − cable top          (positive = buried)
    free-span gap          = (cable top − diameter) − seabed   (positive = cable bottom above seabed)
    berm thickness error   = as-built − design           (negative = under-filled)

All results are computed at "stations" every `step` metres along the route and summarised per KP
bin (`bin_m`). Segments (non-compliant DoB, spans, under-filled berm) are contiguous runs of
stations; their ends lie half-way between stations.
"""
from __future__ import annotations

import numpy as np

from core.profile import Section, tin_section
from core.surface import Dem, Tin


EPS = 1e-4          # 0.1 mm: values within this of a threshold count as meeting it (interpolation noise)


def _report(progress, f):
    if progress is not None:
        progress(float(min(max(f, 0.0), 1.0)))


# ================================================================ stations along a route
def ch_range(route, kp_from, kp_to):
    """Chainage interval of a KP range, clipped to the route; error if it does not overlap the route."""
    k0, k1 = route.kp_range()
    lo, hi = sorted((kp_from, kp_to))
    if hi < min(k0, k1) - 1e-9 or lo > max(k0, k1) + 1e-9 or hi - lo < 1e-9:
        raise ValueError(f"KP {kp_from:g} – {kp_to:g} is outside '{route.name}' (KP {k0:.3f} – {k1:.3f})")
    a, b = (float(route.chainage_at_kp(k)) for k in (kp_from, kp_to))
    return max(0.0, min(a, b)), min(route.length, max(a, b))


def stations(route, step=1.0, kp_range=None):
    """Chainage (m along the polyline), KP (km), X, Y of stations every `step` m (both ends included)."""
    L = route.length
    c0, c1 = 0.0, L
    if kp_range is not None:
        c0, c1 = ch_range(route, *kp_range)
    n = max(2, int(np.floor((c1 - c0) / step + 1e-9)) + 1)
    ch = c0 + np.arange(n) * step
    if c1 - ch[-1] > 1e-6:
        ch = np.r_[ch, c1]
    xy = route.xy_at(ch)
    return {"ch": ch, "kp": route.kp_at(ch), "x": xy[:, 0], "y": xy[:, 1]}


def _tangents(route, ch):
    """Unit direction of the route at chainages (from the segment each lies on)."""
    c = route.cum
    i = np.clip(np.searchsorted(c, ch, side="right") - 1, 0, len(c) - 2)
    d = route.xy[i + 1] - route.xy[i]
    return d / np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-12)[:, None]


def cable_top(route, ch):
    """Cable-top elevation at chainages (NaN where the route has no Z)."""
    if route.z is None:
        return np.full(len(ch), np.nan)
    return np.interp(ch, route.cum, route.z, left=np.nan, right=np.nan)


def reported_dob(route, ch):
    if getattr(route, "dob", None) is None:
        return None
    return np.interp(ch, route.cum, route.dob, left=np.nan, right=np.nan)


# ================================================================ corridor points (fast)
def corridor(xyz, idx, route, half_width, ch_range=None, chunk=4_000_000):
    """Points within `half_width` of the route → dict(idx, ch, off, z). Off: + = right of the route.

    Uses a KD-tree of densely spaced stations instead of a point × segment loop, so it stays fast for
    long routes and tens of millions of points."""
    from scipy.spatial import cKDTree
    ds = max(min(half_width / 2.0, 0.5), 0.05)
    L = route.length
    c0, c1 = (0.0, L) if ch_range is None else (max(0.0, ch_range[0]), min(L, ch_range[1]))
    sc = np.r_[np.arange(c0, c1, ds), c1]
    sxy = route.xy_at(sc)
    tan = _tangents(route, sc)
    tree = cKDTree(sxy)
    lo = sxy.min(axis=0) - half_width - ds
    hi = sxy.max(axis=0) + half_width + ds
    out = {"idx": [], "ch": [], "off": [], "z": []}
    r = float(np.hypot(half_width, ds))
    for s in range(0, len(idx), chunk):
        ci = idx[s:s + chunk]
        p = xyz[ci, :2]
        m = (p[:, 0] >= lo[0]) & (p[:, 0] <= hi[0]) & (p[:, 1] >= lo[1]) & (p[:, 1] <= hi[1])
        if not m.any():
            continue
        ci, p = ci[m], p[m]
        d, j = tree.query(p, distance_upper_bound=r)
        ok = np.isfinite(d)
        ci, p, j = ci[ok], p[ok], j[ok]
        v = p - sxy[j]
        along = v[:, 0] * tan[j, 0] + v[:, 1] * tan[j, 1]
        off = v[:, 0] * tan[j, 1] - v[:, 1] * tan[j, 0]
        ch = sc[j] + along
        keep = (np.abs(off) <= half_width) & (ch >= c0) & (ch <= c1)
        out["idx"].append(ci[keep])
        out["ch"].append(ch[keep])
        out["off"].append(off[keep])
        out["z"].append(xyz[ci[keep], 2])
    res = {k: (np.concatenate(v) if v else np.empty(0, dtype=np.int64 if k == "idx" else np.float64))
           for k, v in out.items()}
    o = np.argsort(res["ch"], kind="stable")
    return {k: v[o] for k, v in res.items()}


def _bin_index(ch, st_ch):
    """Station index of each chainage (nearest station; stations are evenly spaced except the last)."""
    edges = np.r_[-np.inf, (st_ch[:-1] + st_ch[1:]) / 2.0, np.inf]
    return np.clip(np.searchsorted(edges, ch, side="right") - 1, 0, len(st_ch) - 1)


def _group_stat(k, z, n, fn):
    """Per-station statistic of z grouped by station index k (NaN where empty)."""
    out = np.full(n, np.nan)
    if len(k) == 0:
        return out
    o = np.argsort(k, kind="stable")
    k, z = k[o], z[o]
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    ends = np.r_[starts[1:], len(k)]
    for a, b in zip(starts, ends):
        out[k[a]] = fn(z[a:b])
    return out


# ================================================================ surface along the route
def _interp_runs(ch_src, z_src, ch):
    """Interpolate a section that may contain NaN breaks; NaN outside the covered runs."""
    out = np.full(len(ch), np.nan)
    if len(ch_src) == 0:
        return out
    good = np.isfinite(z_src) & np.isfinite(ch_src)
    br = np.flatnonzero(~good)
    bounds = np.r_[-1, br, len(z_src)]
    for a, b in zip(bounds[:-1] + 1, bounds[1:]):
        if b - a < 1:
            continue
        cs, zs = ch_src[a:b], z_src[a:b]
        o = np.argsort(cs)
        cs, zs = cs[o], zs[o]
        m = (ch >= cs[0] - 1e-6) & (ch <= cs[-1] + 1e-6)
        out[m] = np.interp(ch[m], cs, zs)
    return out


def seabed_along(obj, route, st, width=0.5, stat="median"):
    """Seabed elevation at the stations from a DEM, a TIN or a point cloud (corridor ± width)."""
    ch = st["ch"]
    if isinstance(obj, Dem):
        from core.filters import surface_z
        return surface_z(obj, st["x"], st["y"])
    if isinstance(obj, Tin):
        sec = Section(route.name, route.xy, width=width)
        cs, zs = tin_section(obj, sec)
        return _interp_runs(cs, zs, ch)
    # point cloud
    step = float(np.median(np.diff(ch))) if len(ch) > 1 else 1.0
    r = corridor(obj.xyz, obj.active_indices(), route, width, (ch[0] - step / 2, ch[-1] + step / 2))
    k = _bin_index(r["ch"], ch)
    f = {"median": np.median, "min": np.min, "max": np.max,
         "p10": lambda a: np.percentile(a, 10)}[stat]
    return _group_stat(k, r["z"], len(ch), f)


# ================================================================ segments and bins
def runs(mask):
    """[(i0, i1)] inclusive index ranges of consecutive True values."""
    m = np.asarray(mask, bool)
    if not m.any():
        return []
    d = np.diff(np.r_[0, m.astype(np.int8), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def _edges(ch):
    """Station cell edges (half-way between stations, clipped to the analysed range)."""
    mid = (ch[:-1] + ch[1:]) / 2.0
    return np.r_[ch[0], mid], np.r_[mid, ch[-1]]


def segments_from(st, mask, values=None, value_name="value", reducer="min", route=None):
    """Contiguous runs of `mask` → list of dicts with KP start/end, length and the extreme value."""
    lo, hi = _edges(st["ch"])
    out = []
    for i0, i1 in runs(mask):
        c0, c1 = lo[i0], hi[i1]
        seg = {"kp_from": float(np.interp(c0, st["ch"], st["kp"])), "kp_to": float(np.interp(c1, st["ch"], st["kp"])),
               "length": float(c1 - c0), "i0": int(i0), "i1": int(i1)}
        if route is not None:
            a, b = route.xy_at(np.array([c0, c1]))
            seg.update(x_from=float(a[0]), y_from=float(a[1]), x_to=float(b[0]), y_to=float(b[1]))
        if values is not None:
            v = values[i0:i1 + 1]
            v = v[np.isfinite(v)]
            if len(v):
                ext = {"min": np.min, "max": np.max}[reducer](v)
                j = i0 + int(np.flatnonzero(values[i0:i1 + 1] == ext)[0])
                seg[f"{value_name}_{reducer}"] = float(ext)
                seg[f"kp_at_{reducer}"] = float(st["kp"][j])
                seg[f"{value_name}_mean"] = float(np.mean(v))
        out.append(seg)
    return out


def kp_bins(st, bin_m):
    """Bin label index per station: floor(KP·1000 / bin_m) (bins aligned to round KP values)."""
    k = st["kp"] * 1000.0 / bin_m
    return np.floor(k + 1e-9).astype(np.int64)


def bin_table(st, bin_m, columns, ok=None):
    """Per KP bin: valid length and min / mean / max of each column (dict name → per-station values).
    ok: optional per-station boolean (e.g. DoB ≥ target) → percentage of the valid length."""
    b = kp_bins(st, bin_m)
    lo, hi = _edges(st["ch"])
    w = hi - lo
    rows = []
    for key in np.unique(b):
        m = b == key
        row = {"kp_from": key * bin_m / 1000.0, "kp_to": (key + 1) * bin_m / 1000.0}
        first = next(iter(columns.values()))
        valid = m & np.isfinite(first)
        row["length"] = float(w[m].sum())
        row["covered"] = float(w[valid].sum())
        for name, vals in columns.items():
            v = vals[m & np.isfinite(vals)]
            row[f"{name}_min"] = float(v.min()) if len(v) else np.nan
            row[f"{name}_mean"] = float(v.mean()) if len(v) else np.nan
            row[f"{name}_max"] = float(v.max()) if len(v) else np.nan
        if ok is not None:
            row["ok_pct"] = float(100.0 * w[valid & ok].sum() / w[valid].sum()) if valid.any() else np.nan
        rows.append(row)
    if len(rows) > 1 and st["kp"][-1] < st["kp"][0]:          # KP decreasing along the line
        rows.reverse()
    return rows


def _length(st, mask):
    lo, hi = _edges(st["ch"])
    return float((hi - lo)[mask].sum())


# ================================================================ 1. depth of burial
def dob_analysis(route, seabed, step=1.0, target=1.5, bin_m=100.0, width=0.5, kp_range=None, progress=None):
    if route.z is None:
        raise ValueError(f"'{route.name}' has no cable-top Z. Import the cable profile with a cable-top column.")
    st = stations(route, step, kp_range)
    _report(progress, 0.1)
    sb = seabed_along(seabed, route, st, width)
    _report(progress, 0.7)
    top = cable_top(route, st["ch"])
    dob = sb - top
    rep = reported_dob(route, st["ch"])
    valid = np.isfinite(dob)
    fail = valid & (dob < target - EPS)
    segs = segments_from(st, fail, dob, "dob", "min", route)
    gaps = [g for g in segments_from(st, ~valid, route=route) if g["length"] > step * 1.5]
    cols = {"dob": dob, "seabed": sb, "cable_top": top}
    if rep is not None:
        cols["dob_reported"] = rep
    bins = bin_table(st, bin_m, cols, ok=~fail)
    total = _length(st, np.ones(len(dob), bool))
    cov = _length(st, valid)
    j = int(np.nanargmin(dob)) if valid.any() else None
    summary = {"length": total, "covered": cov, "coverage_pct": 100.0 * cov / total if total else 0.0,
               "compliant": _length(st, valid & ~fail), "non_compliant": _length(st, fail),
               "exposed": _length(st, valid & (dob < -EPS)), "segments": len(segs),
               "dob_min": float(dob[j]) if j is not None else np.nan,
               "kp_at_min": float(st["kp"][j]) if j is not None else np.nan,
               "dob_mean": float(np.nanmean(dob)) if valid.any() else np.nan}
    summary["compliant_pct"] = 100.0 * summary["compliant"] / cov if cov else 0.0
    if rep is not None:
        dd = (dob - rep)[np.isfinite(dob - rep)]
        summary["vs_reported_mean"] = float(dd.mean()) if len(dd) else np.nan
        summary["vs_reported_max_abs"] = float(np.abs(dd).max()) if len(dd) else np.nan
    _report(progress, 1.0)
    return {"kind": "dob", "route": route.name, "st": st, "series": {"seabed": sb, "cable_top": top, "dob": dob,
            "dob_reported": rep}, "segments": segs, "gaps": gaps, "bins": bins, "summary": summary,
            "params": {"step": step, "target": target, "bin_m": bin_m, "width": width}}


# ================================================================ 2. free spans
def free_span_profile(route, seabed, diameter=0.2, gap_min=0.1, len_min=10.0, step=1.0, bin_m=100.0,
                      width=0.5, kp_range=None, progress=None):
    """Method A — cable profile (top of cable) + seabed surface."""
    if route.z is None:
        raise ValueError(f"'{route.name}' has no cable-top Z. Use method B (point cloud) or import a cable profile.")
    st = stations(route, step, kp_range)
    _report(progress, 0.1)
    sb = seabed_along(seabed, route, st, width)
    _report(progress, 0.7)
    top = cable_top(route, st["ch"])
    gap = (top - diameter) - sb
    res = _spans(route, st, gap, gap_min, len_min, bin_m, {"seabed": sb, "cable_top": top})
    res["params"] = {"method": "profile", "diameter": diameter, "gap_min": gap_min, "len_min": len_min,
                     "step": step, "bin_m": bin_m, "width": width}
    _report(progress, 1.0)
    return res


def free_span_points(route, pc, diameter=0.2, gap_min=0.1, len_min=10.0, step=1.0, bin_m=100.0,
                     search=1.5, side=3.0, kp_range=None, progress=None):
    """Method B — point cloud only (cable visible in MBES data).

    Per station: cable crest = highest point within ±`search` m of the route (route position
    uncertainty); seabed under the cable = median of the points `search`…`search + side` m on each
    side (both sides averaged; one side if the other is empty).
    The cable counts as visible where the crest is ≥ half a diameter above that seabed.
    gap = crest − diameter − seabed. The seabed under the cable is interpolated from the sides —
    a local scour pit narrower than the corridor is not seen; check spans in the profile view."""
    st = stations(route, step, kp_range)
    ch = st["ch"]
    _report(progress, 0.05)
    r = corridor(pc.xyz, pc.active_indices(), route, search + side, (ch[0] - step / 2, ch[-1] + step / 2))
    _report(progress, 0.6)
    k = _bin_index(r["ch"], ch)
    n = len(ch)
    core = np.abs(r["off"]) <= search
    crest = _group_stat(k[core], r["z"][core], n, np.max)
    # lateral position of the crest (for drawing the detected cable)
    off_c = np.full(n, np.nan)
    if core.any():
        kc, zc, oc = k[core], r["z"][core], r["off"][core]
        o = np.lexsort((-zc, kc))
        first = np.r_[True, kc[o][1:] != kc[o][:-1]]
        off_c[kc[o][first]] = oc[o][first]

    left = (r["off"] < -search)
    right = (r["off"] > search)
    sl = _group_stat(k[left], r["z"][left], n, np.median)
    sr = _group_stat(k[right], r["z"][right], n, np.median)
    sb = np.where(np.isfinite(sl) & np.isfinite(sr), (sl + sr) / 2.0, np.where(np.isfinite(sl), sl, sr))
    _report(progress, 0.85)
    visible = np.isfinite(crest) & np.isfinite(sb) & (crest - sb >= diameter / 2.0)
    gap = np.where(visible, crest - diameter - sb, np.nan)
    res = _spans(route, st, gap, gap_min, len_min, bin_m, {"seabed": sb, "cable_top": np.where(visible, crest, np.nan)})
    tan = _tangents(route, ch)
    nrm = np.column_stack([tan[:, 1], -tan[:, 0]])          # + offset = right of the route
    oc = np.nan_to_num(off_c)
    res["crest_xy"] = np.column_stack([st["x"] + nrm[:, 0] * oc, st["y"] + nrm[:, 1] * oc])
    res["summary"]["visible"] = _length(st, visible)
    res["params"] = {"method": "points", "diameter": diameter, "gap_min": gap_min, "len_min": len_min,
                     "step": step, "bin_m": bin_m, "search": search, "side": side}
    _report(progress, 1.0)
    return res


def _spans(route, st, gap, gap_min, len_min, bin_m, extra):
    span = np.isfinite(gap) & (gap > gap_min + EPS)
    segs = segments_from(st, span, gap, "gap", "max", route)
    for s in segs:
        s["flag"] = "SPAN" if s["length"] >= len_min else "short"
    cols = {"gap": gap, **extra}
    bins = bin_table(st, bin_m, cols)
    valid = np.isfinite(gap)
    long_ = [s for s in segs if s["flag"] == "SPAN"]
    summary = {"length": _length(st, np.ones(len(gap), bool)), "covered": _length(st, valid),
               "spans": len(long_), "short_spans": len(segs) - len(long_),
               "span_length_total": float(sum(s["length"] for s in long_)),
               "longest": max((s["length"] for s in segs), default=0.0),
               "gap_max": float(np.nanmax(gap)) if valid.any() else np.nan}
    summary["coverage_pct"] = 100.0 * summary["covered"] / summary["length"] if summary["length"] else 0.0
    return {"kind": "span", "route": route.name, "st": st, "series": {"gap": gap, **extra}, "segments": segs,
            "bins": bins, "summary": summary}


# ================================================================ 3. rock berm
def _grid_around(route, c0, c1, margin, cell):
    from core.surface import grid_extent
    ch = np.r_[np.arange(c0, c1, max(cell, 0.5)), c1]
    xy = route.xy_at(ch)
    lo, hi = xy.min(axis=0) - margin, xy.max(axis=0) + margin
    x0, ytop, nr, nc = grid_extent(lo[0], lo[1], hi[0], hi[1], cell, snap=True)
    if nr * nc > 40_000_000:
        raise ValueError(f"The analysis grid would have {nr * nc:,} cells — use a larger cell size or a shorter KP range")
    return x0, ytop, nr, nc


def _cell_route_coords(route, x0, ytop, cell, nr, nc):
    """Chainage and signed offset of every grid cell centre relative to the route."""
    from scipy.spatial import cKDTree
    ds = max(cell / 2.0, 0.1)
    sc = np.r_[np.arange(0.0, route.length, ds), route.length]
    sxy = route.xy_at(sc)
    tan = _tangents(route, sc)
    xs = x0 + (np.arange(nc) + 0.5) * cell
    ys = ytop - (np.arange(nr) + 0.5) * cell
    X, Y = np.meshgrid(xs, ys)
    P = np.column_stack([X.ravel(), Y.ravel()])
    _d, j = cKDTree(sxy).query(P)
    v = P - sxy[j]
    along = v[:, 0] * tan[j, 0] + v[:, 1] * tan[j, 1]
    off = v[:, 0] * tan[j, 1] - v[:, 1] * tan[j, 0]
    return (sc[j] + along).reshape(nr, nc), off.reshape(nr, nc)


def berm_template(pre, route, kp_from, kp_to, crest_width=2.0, height=1.0, slope_n=3.0, end_slope_n=None,
                  cell=0.5, smooth=5.0, progress=None):
    """Design surface of a rock berm on the pre-lay seabed `pre` (Dem / Tin).

    Crest level = pre-lay seabed on the route centre line (smoothed over `smooth` m) + height;
    crest width across the route; side slopes 1:slope_n down to the existing seabed; the berm ends
    slope at 1:end_slope_n (default = side slope) beyond KP from / to."""
    from core.volume import sample_on_grid
    end_n = end_slope_n or slope_n
    c0, c1 = ch_range(route, kp_from, kp_to)
    reach = crest_width / 2.0 + height * slope_n + 2 * cell
    x0, ytop, nr, nc = _grid_around(route, max(0.0, c0 - height * end_n), min(route.length, c1 + height * end_n),
                                    reach, cell)
    _report(progress, 0.1)
    P = sample_on_grid(pre, x0, ytop, cell, nr, nc)
    _report(progress, 0.4)
    CH, OFF = _cell_route_coords(route, x0, ytop, cell, nr, nc)
    _report(progress, 0.7)
    # centre-line seabed (smoothed) → crest level
    st = stations(route, max(cell, 0.5))
    from core.filters import surface_z
    zc = surface_z(pre, st["x"], st["y"]) if isinstance(pre, Dem) else seabed_along(pre, route, st)
    good = np.isfinite(zc)
    if good.sum() < 2:
        raise ValueError("The pre-lay surface does not cover the route")
    zc = np.interp(st["ch"], st["ch"][good], zc[good])
    if smooth > 0:
        w = max(1, int(round(smooth / max(cell, 0.5))))
        k = np.ones(2 * w + 1) / (2 * w + 1)
        zc = np.convolve(np.pad(zc, w, mode="edge"), k, mode="valid")
    crest = np.interp(np.clip(CH, 0, route.length), st["ch"], zc) + height
    d_side = np.maximum(np.abs(OFF) - crest_width / 2.0, 0.0)
    d_end = np.maximum(np.maximum(c0 - CH, CH - c1), 0.0)
    G = crest - d_side / slope_n - d_end / end_n
    D = np.where(np.isfinite(P), np.maximum(P, G), np.nan)
    _report(progress, 1.0)
    return {"design": Dem(D.astype(np.float32), x0, ytop, cell, "design",
                          meta={"berm": f"crest {crest_width:g} m, height {height:g} m, slopes 1:{slope_n:g}",
                                "kp_from": float(kp_from), "kp_to": float(kp_to)}),
            "pre": P, "x0": x0, "ytop": ytop, "cell": cell}


def berm_check(asbuilt, design, route, kp_from, kp_to, pre=None, half_width=None, tol=0.1, cell=None,
               step=1.0, bin_m=25.0, min_cover=None, check_every=25.0, check_len=None, progress=None):
    """As-built berm vs design.

    design: Dem / Tin. Footprint = where design is ≥ 5 cm above the pre-lay surface (if `pre` is
    given), else the corridor ±half_width between KP from / to. Thickness error = as-built − design.
    Under-filled cells: error < −tol. Coverage = footprint area within tolerance or above.
    Along the centre line: crest error per station (under-filled runs → segments) and, when the
    route has cable-top Z, the rock cover over the cable (as-built − cable top)."""
    from core.volume import sample_on_grid
    if isinstance(design, Dem):
        cell = cell or design.cell
    elif cell is None:
        raise ValueError("Give a cell size for a TIN design surface")
    c0, c1 = ch_range(route, kp_from, kp_to)
    hw = half_width if half_width is not None else 10.0
    x0, ytop, nr, nc = _grid_around(route, c0, c1, hw + 2 * cell, cell)
    G = sample_on_grid(design, x0, ytop, cell, nr, nc)
    _report(progress, 0.2)
    A = sample_on_grid(asbuilt, x0, ytop, cell, nr, nc)
    _report(progress, 0.4)
    CH, OFF = _cell_route_coords(route, x0, ytop, cell, nr, nc)
    if pre is not None:
        P = sample_on_grid(pre, x0, ytop, cell, nr, nc)
        fp = np.isfinite(G) & np.isfinite(P) & (G - P >= 0.05)
    else:
        P = None
        fp = np.isfinite(G) & (np.abs(OFF) <= hw) & (CH >= c0) & (CH <= c1)
    _report(progress, 0.6)
    valid = fp & np.isfinite(A)
    E = np.where(valid, A - G, np.nan)
    a = cell * cell
    under = valid & (E < -tol - EPS)
    over = valid & (E > tol + EPS)
    stats = {"footprint_area": float(fp.sum() * a), "measured_area": float(valid.sum() * a),
             "under_area": float(under.sum() * a), "over_area": float(over.sum() * a),
             "under_volume": float(-E[under].sum() * a), "over_volume": float(E[over].sum() * a),
             "coverage_pct": float(100.0 * (valid & ~under).sum() / valid.sum()) if valid.any() else np.nan,
             "measured_pct": float(100.0 * valid.sum() / fp.sum()) if fp.any() else 0.0,
             "error_mean": float(np.nanmean(E)) if valid.any() else np.nan,
             "error_min": float(np.nanmin(E)) if valid.any() else np.nan,
             "cell": cell, "tol": tol}
    if P is not None:
        stats["design_volume"] = float(np.nansum(np.where(fp, G - P, 0.0)) * a)
        stats["placed_volume"] = float(np.nansum(np.where(valid, np.maximum(A - P, 0.0), 0.0)) * a)
    # per KP bin (cells)
    kp_cell = route.kp_at(np.clip(CH, 0, route.length))
    kb = np.floor(kp_cell * 1000.0 / bin_m + 1e-9).astype(np.int64)
    bins = []
    for key in np.unique(kb[fp]):
        m = fp & (kb == key)
        mv = m & valid
        row = {"kp_from": key * bin_m / 1000.0, "kp_to": (key + 1) * bin_m / 1000.0,
               "footprint_m2": float(m.sum() * a), "measured_m2": float(mv.sum() * a),
               "under_m3": float(-E[mv & under].sum() * a), "over_m3": float(E[mv & over].sum() * a),
               "coverage_pct": float(100.0 * (mv & ~under).sum() / mv.sum()) if mv.any() else np.nan,
               "error_mean": float(E[mv].mean()) if mv.any() else np.nan,
               "error_min": float(E[mv].min()) if mv.any() else np.nan}
        if P is not None:
            row["design_m3"] = float(np.nansum(np.where(m, G - P, 0.0)) * a)
            row["placed_m3"] = float(np.nansum(np.where(mv, np.maximum(A - P, 0.0), 0.0)) * a)
        bins.append(row)
    _report(progress, 0.75)
    # centre line
    st = stations(route, step, (kp_from, kp_to))
    from core.filters import surface_z
    gz = surface_z(Dem(G.astype(np.float32), x0, ytop, cell, "d"), st["x"], st["y"])
    az = surface_z(Dem(A.astype(np.float32), x0, ytop, cell, "a"), st["x"], st["y"])
    crest_err = az - gz
    segs = segments_from(st, np.isfinite(crest_err) & (crest_err < -tol - EPS), crest_err, "error", "min", route)
    series = {"design": gz, "asbuilt": az, "crest_error": crest_err}
    cover_segs = []
    if route.z is not None:
        top = cable_top(route, st["ch"])
        cover = az - top
        series["cable_top"] = top
        series["cover"] = cover
        if np.isfinite(cover).any():
            stats["cover_min"] = float(np.nanmin(cover))
            stats["kp_at_cover_min"] = float(st["kp"][int(np.nanargmin(cover))])
        if min_cover is not None:
            cover_segs = segments_from(st, np.isfinite(cover) & (cover < min_cover - EPS), cover, "cover", "min",
                                       route)
            stats["cover_below_min_length"] = float(sum(s["length"] for s in cover_segs))
    if P is not None:
        series["pre"] = surface_z(Dem(P.astype(np.float32), x0, ytop, cell, "p"), st["x"], st["y"])
    stats["under_segments"] = len(segs)
    # check sections across the route
    sections = []
    if check_every and check_every > 0:
        cl = check_len or 2 * hw
        tan = _tangents(route, np.array([0.0]))
        for c in np.arange(c0, c1 + 1e-6, check_every):
            t = _tangents(route, np.array([c]))[0]
            nrm = np.array([t[1], -t[0]])
            p = route.xy_at(np.array([c]))[0]
            sections.append({"kp": float(route.kp_at(np.array([c]))[0]),
                             "xy": np.array([p - nrm * cl / 2.0, p + nrm * cl / 2.0])})
        del tan
    _report(progress, 1.0)
    return {"kind": "berm", "route": route.name, "st": st, "series": series, "segments": segs,
            "cover_segments": cover_segs, "bins": bins, "summary": stats, "sections": sections,
            "grid": {"x0": x0, "ytop": ytop, "cell": cell, "error": E.astype(np.float32),
                     "asbuilt": A.astype(np.float32), "design": G.astype(np.float32)},
            "params": {"kp_from": kp_from, "kp_to": kp_to, "tol": tol, "bin_m": bin_m, "step": step,
                       "min_cover": min_cover, "half_width": half_width}}
