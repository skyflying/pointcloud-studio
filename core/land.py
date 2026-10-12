"""Land analyses (result dicts as in core/terrain.py):

C. stockpile_volumes — volume above a base derived from each stockpile boundary, per survey
D. road_design        — corridor template on a centre line: formation, crossfall, cut / fill slopes,
                        volumes per chainage interval, mass haul, longitudinal profile
E. hydrology          — depression filling, D8 flow, accumulation, streams, watershed, inundation
F. clearance          — points closer than a limit to a 3D line (power line, pipe, conductor)
"""
from __future__ import annotations

import heapq

import numpy as np

from core.surface import Dem
from core.terrain import _f, _report, layer, surface_grid


# ================================================================ C. stockpiles
def stockpile_volumes(surfaces, polygons, base="plane", level=None, cell=None, density=None, progress=None):
    """surfaces: [(name, date, Dem | Tin)] (one or more surveys); polygons: [(name, ring)] — the toe of
    each stockpile. Base: 'plane' (best-fit plane through the boundary), 'tin' (boundary triangulated),
    'lowest' (lowest boundary point), 'level' (fixed elevation)."""
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

    from core.filters import surface_z
    from core.volume import polygon_mask
    rows, feats, lay = [], [], []
    total = max(len(surfaces) * len(polygons), 1)
    k = 0
    for name, ring in polygons:
        ring = np.asarray(ring, float)[:, :2]
        if np.array_equal(ring[0], ring[-1]):
            ring = ring[:-1]
        lo, hi = ring.min(axis=0), ring.max(axis=0)
        prev = None
        for sname, sdate, S in surfaces:
            c = cell or (S.cell if isinstance(S, Dem) else 0.5)
            Z, x0, ytop, c = surface_grid(S, c, (lo[0] - c, lo[1] - c, hi[0] + c, hi[1] + c))
            xs = x0 + (np.arange(Z.shape[1]) + 0.5) * c
            ys = ytop - (np.arange(Z.shape[0]) + 0.5) * c
            inside = polygon_mask(xs, ys, ring)
            # boundary samples every half cell
            seg = np.vstack([ring, ring[:1]])
            pts = []
            for a, b in zip(seg[:-1], seg[1:]):
                n = max(2, int(np.ceil(np.hypot(*(b - a)) / (c / 2))))
                t = np.linspace(0, 1, n, endpoint=False)
                pts.append(a + (b - a) * t[:, None])
            bp = np.vstack(pts)
            Sd = Dem(Z.astype(np.float32), x0, ytop, c, "s")
            bz = surface_z(Sd, bp[:, 0], bp[:, 1])
            good = np.isfinite(bz)
            X, Y = np.meshgrid(xs, ys)
            if base == "level":
                B = np.full_like(Z, float(level))
            elif good.sum() < 3:
                B = np.full_like(Z, np.nan)
            elif base == "lowest":
                B = np.full_like(Z, float(bz[good].min()))
            elif base == "tin":
                f = LinearNDInterpolator(bp[good], bz[good])
                B = f(X, Y)
                nn = NearestNDInterpolator(bp[good], bz[good])
                B = np.where(np.isfinite(B), B, nn(X, Y))
            else:
                A_ = np.column_stack([bp[good, 0] - x0, bp[good, 1] - ytop, np.ones(good.sum())])
                co, *_ = np.linalg.lstsq(A_, bz[good], rcond=None)
                B = co[0] * (X - x0) + co[1] * (Y - ytop) + co[2]
            H = Z - B
            ok = inside & np.isfinite(H)
            a = c * c
            above = float(np.clip(H[ok], 0, None).sum() * a)
            below = float(np.clip(-H[ok], 0, None).sum() * a)
            row = {"stockpile": name, "survey": sname, "date": sdate or "", "area_m2": float(inside.sum() * a),
                   "measured_pct": float(100 * ok.sum() / max(inside.sum(), 1)), "volume_m3": above,
                   "below_base_m3": below, "height_max": float(H[ok].max()) if ok.any() else np.nan,
                   "base": base}
            if density:
                row["tonnes"] = above * density
            if prev is not None:
                row["change_m3"] = above - prev["volume_m3"]
            rows.append(row)
            prev = row
            if (sname, sdate, S) is surfaces[-1] or len(surfaces) == 1:
                lay.append((f"Stockpile {name}: height above base",
                            layer(Z, x0, ytop, c, np.where(ok, H, np.nan), "height m", "Viridis")))
            k += 1
            _report(progress, k / total)
        feats.append({"type": "Polygon", "parts": [ring], "attrs": {"NAME": name, "VOL_M3": prev["volume_m3"],
                                                                   "AREA_M2": prev["area_m2"]}})
    tot = sum(r["volume_m3"] for r in rows if r["survey"] == surfaces[-1][0])
    low = sorted({r["stockpile"] for r in rows if r["measured_pct"] < 95})
    note = ([("Warning", f"{len(low)} stockpile(s) less than 95 % covered by the survey — volumes too small: "
                         + ", ".join(low[:8]) + (" …" if len(low) > 8 else ""))] if low else [])
    return {"kind": "stockpile", "title": "Stockpile volumes",
            "summary": [("Stockpiles", f"{len(polygons)} · surveys: {', '.join(s[0] for s in surfaces)}"),
                        ("Base", {"plane": "best-fit plane through the boundary", "tin": "boundary triangulated",
                                  "lowest": "lowest boundary point", "level": f"level {level}"}[base]),
                        ("Total volume (latest survey)", f"{tot:,.1f} m³"
                         + (f" · {tot * density:,.0f} t" if density else ""))] + note,
            "tables": {"Volumes": rows}, "features": {"stockpiles": feats}, "layers": lay}


# ================================================================ D. road corridor
def road_design(surface, route, kp_from=None, kp_to=None, grade="ground", pvi=(), smooth=50.0, offset=0.0,
                half_width=4.0, crossfall=2.5, cut_n=1.5, fill_n=2.0, reach=40.0, cell=0.5, step=1.0, bin_m=20.0,
                cut_to_fill=0.9, section_every=20.0, progress=None):
    """Corridor template along a centre line.

    Design grade along the centre line: 'ground' = existing ground smoothed over `smooth` m + offset,
    or 'pvi' = straight grades between (KP, level) points (no vertical curves). Formation: ±half_width,
    falling outward at `crossfall` % from the centre line; beyond it a cut slope 1:cut_n up to the ground
    or a fill slope 1:fill_n down to it. Cut / fill per chainage interval; mass haul ordinate =
    Σ (cut × cut_to_fill − fill) (cut_to_fill: compacted fill gained per m³ of cut)."""
    from core import cable
    from core.filters import surface_z
    from core.scour import sample_grid
    if kp_from is None or kp_to is None:
        kp_from, kp_to = route.kp_range()
    c0, c1 = cable.ch_range(route, kp_from, kp_to)
    st = cable.stations(route, step, (kp_from, kp_to))
    gz = cable.seabed_along(surface, route, st)
    if not np.isfinite(gz).any():
        raise ValueError("The surface does not cover the centre line")
    _report(progress, 0.1)
    if grade == "pvi" and len(pvi) >= 2:
        pk = np.array([p[0] for p in pvi], float)
        pz = np.array([p[1] for p in pvi], float)
        o = np.argsort(pk)
        design = np.interp(st["kp"], pk[o], pz[o])
    else:
        ok = np.isfinite(gz)
        g = np.interp(st["ch"], st["ch"][ok], gz[ok])
        w = max(1, int(round(smooth / step / 2)))
        kern = np.ones(2 * w + 1) / (2 * w + 1)
        design = np.convolve(np.pad(g, w, mode="edge"), kern, mode="valid") + offset
    cf = crossfall / 100.0
    a = cell * cell
    edges = np.arange(c0, c1 + bin_m, bin_m)
    edges[-1] = min(edges[-1], c1) if len(edges) > 1 else c1
    cut_b = np.zeros(max(len(edges) - 1, 1))
    fill_b = np.zeros_like(cut_b)
    # the corridor is processed in pieces along the centre line (a long or diagonal route would need a huge
    # bounding-box grid); every cell is counted in exactly one piece (by its chainage)
    # short pieces keep each piece's bounding box close to the corridor itself, also on diagonal routes
    piece = float(np.clip(3.0 * (half_width + reach), 50.0, 1000.0))
    cuts = np.r_[np.arange(c0, c1, piece), c1]
    full = None
    try:
        fx0, fytop, fnr, fnc = cable._grid_around(route, c0, c1, half_width + reach, cell)
        if fnr * fnc > 12_000_000:
            raise ValueError("too large")
        full = (fx0, fytop, fnr, fnc, np.full((fnr, fnc), np.nan, np.float32), np.full((fnr, fnc), np.nan, np.float32),
                np.full((fnr, fnc), np.nan, np.float32))
    except ValueError:
        full = None                                   # too large for one layer: volumes only
    for pi, (pa, pb) in enumerate(zip(cuts[:-1], cuts[1:])):
        x0, ytop, nr, nc = cable._grid_around(route, max(pa - cell, 0.0), min(pb + cell, route.length),
                                              half_width + reach, cell)
        G = sample_grid(surface, x0, ytop, cell, nr, nc)
        CH, OFF = cable._cell_route_coords(route, x0, ytop, cell, nr, nc)
        last = pi == len(cuts) - 2
        inrange = (CH >= pa) & ((CH <= pb) if last else (CH < pb)) & (np.abs(OFF) <= half_width + reach)
        gl = np.interp(np.clip(CH, c0, c1), st["ch"], design)
        d = np.abs(OFF)
        F = gl - cf * np.minimum(d, half_width)
        E = gl - cf * half_width
        dd = np.maximum(d - half_width, 0.0)
        side = np.where(G >= E, np.minimum(G, E + dd / cut_n), np.maximum(G, E - dd / fill_n))
        Dz = np.where(d <= half_width, F, side)
        Dz = np.where(inrange & np.isfinite(G), Dz, np.nan)
        diff = Dz - G                                    # + fill, − cut
        ok = np.isfinite(diff)
        cut = np.where(ok, np.clip(-diff, 0, None), 0.0)
        fill = np.where(ok, np.clip(diff, 0, None), 0.0)
        bi = np.clip(np.searchsorted(edges, CH, side="right") - 1, 0, len(cut_b) - 1)
        np.add.at(cut_b, bi[inrange], cut[inrange] * a)
        np.add.at(fill_b, bi[inrange], fill[inrange] * a)
        if full is not None:
            fx0, fytop, fnr, fnc, FD, FG, FX = full
            r0 = int(round((fytop - ytop) / cell))
            q0 = int(round((x0 - fx0) / cell))
            rs = slice(max(r0, 0), min(r0 + nr, fnr))
            qs = slice(max(q0, 0), min(q0 + nc, fnc))
            sub = (slice(rs.start - r0, rs.stop - r0), slice(qs.start - q0, qs.stop - q0))
            m = inrange[sub] & ok[sub]
            for T_, V_ in ((FD, Dz), (FG, G), (FX, diff)):
                T_[rs, qs] = np.where(m, V_[sub], T_[rs, qs])
        _report(progress, 0.15 + 0.6 * (pi + 1) / (len(cuts) - 1))
    rows, mass = [], 0.0
    for k in range(len(cut_b)):
        cv, fv = float(cut_b[k]), float(fill_b[k])
        mass += cv * cut_to_fill - fv
        kp_a = float(route.kp_at(np.array([edges[k]]))[0])
        kp_b = float(route.kp_at(np.array([min(edges[k + 1], c1)]))[0])
        rows.append({"kp_from": kp_a, "kp_to": kp_b, "cut_m3": cv, "fill_m3": fv, "net_m3": cv - fv,
                     "mass_haul_m3": mass})
    cz = np.where(np.isfinite(gz), design, np.nan)   # centre-line design level = the grade line
    tc, tf = float(cut_b.sum()), float(fill_b.sum())
    secs = []
    if section_every and section_every > 0:
        for cc in np.arange(c0, c1 + 1e-6, section_every):
            t = cable._tangents(route, np.array([cc]))[0]
            nrm = np.array([t[1], -t[0]])
            p = route.xy_at(np.array([cc]))[0]
            L = half_width + reach
            secs.append({"kp": float(route.kp_at(np.array([cc]))[0]), "xy": np.array([p - nrm * L, p + nrm * L])})
    _report(progress, 1.0)
    mh = np.array([r["mass_haul_m3"] for r in rows])
    kpm = np.array([(r["kp_from"] + r["kp_to"]) / 2 for r in rows])
    return {"kind": "road", "title": f"Road corridor — {route.name}",
            "summary": [("Length", f"{c1 - c0:,.1f} m · formation {2 * half_width:g} m, crossfall {crossfall:g} %, "
                                   f"cut 1:{cut_n:g}, fill 1:{fill_n:g}"),
                        ("Design grade", "existing ground smoothed over {:g} m {:+g} m".format(smooth, offset)
                         if grade != "pvi" else f"{len(pvi)} PVI points (straight grades)"),
                        ("Cut", f"{tc:,.1f} m³"), ("Fill", f"{tf:,.1f} m³"),
                        ("Balance", f"{tc * cut_to_fill - tf:+,.1f} m³ (cut × {cut_to_fill:g} − fill; "
                                    "+ = surplus to dispose, − = borrow needed)"),
                        ("Max cut / fill on the centre line", f"{_f(np.nanmax(gz - cz))} / {_f(np.nanmax(cz - gz))} m")]
                       + ([] if full is not None else [("Note", "corridor too large for one layer grid — volumes, "
                                                                "profile and sections only (use a larger cell for layers)")]),
            "tables": {"Earthworks per interval": rows,
                       "Longitudinal profile": [{"kp": float(k), "ground": float(g), "design": float(z),
                                                 "cut_fill": float(z - g)} for k, g, z in zip(st["kp"], gz, cz)]},
            "layers": ([] if full is None else [
                ("Road design surface", Dem(full[4], full[0], full[1], cell, "design", meta={"title": "road design"})),
                ("Road cut (−) / fill (+)", layer(np.where(np.isfinite(full[4]), full[4], full[5]), full[0], full[1],
                                                 cell, np.where(np.abs(full[6]) > 1e-6, full[6], np.nan), "cut/fill m",
                                                 "Diverging", symmetric=True))]),
            "features": {}, "sections": secs,
            "chart": {"x": st["kp"], "panels": [
                {"lines": [("Ground", "#8b919c", gz), ("Design", "#ffd166", cz)], "ylabel": "Elevation (m)"},
                {"x": kpm, "lines": [("Mass haul", "#4cc9f0", mh)], "ylabel": "m³ (cumulative)",
                 "hlines": [(0.0, "#5a606b", "balance")]}]}}


# ================================================================ E. hydrology
def _fill_depressions(Z, progress=None):
    """Priority-flood with a tiny gradient (Barnes et al. 2014): every cell drains to the edge of the data."""
    nr, nc = Z.shape
    F = Z.copy()
    done = ~np.isfinite(Z)
    heap = []
    edge = np.zeros_like(done)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    pad = np.pad(done, 1, constant_values=True)
    nb_nan = np.zeros_like(done)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            nb_nan |= pad[1 + dr:1 + dr + nr, 1 + dc:1 + dc + nc]
    seeds = np.flatnonzero((edge | nb_nan) & ~done)
    for i in seeds:
        heap.append((F.flat[i], int(i)))
    heapq.heapify(heap)
    done.flat[seeds] = True
    Ff = F.ravel()
    D = done.ravel()
    offs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    eps = 1e-5
    total = max(int(np.isfinite(Z).sum()), 1)
    popped = 0
    while heap:
        z, i = heapq.heappop(heap)
        popped += 1
        if progress is not None and popped % 200_000 == 0:
            progress(popped / total)
        r, c = divmod(i, nc)
        for dr, dc in offs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < nr and 0 <= cc < nc:
                j = rr * nc + cc
                if not D[j]:
                    D[j] = True
                    if Ff[j] <= z:
                        Ff[j] = z + eps
                    heapq.heappush(heap, (Ff[j], j))
    return Ff.reshape(nr, nc)


def _d8(F, cell):
    nr, nc = F.shape
    best = np.zeros(F.shape)
    rec = np.full(F.shape, -1, np.int64)
    idx = np.arange(nr * nc).reshape(nr, nc)
    P = np.pad(F, 1, constant_values=np.nan)
    I = np.pad(idx, 1, constant_values=-1)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == dc == 0:
                continue
            dist = cell * (np.sqrt(2) if dr and dc else 1.0)
            Nz = P[1 + dr:1 + dr + nr, 1 + dc:1 + dc + nc]
            drop = (F - Nz) / dist
            take = np.isfinite(drop) & (drop > best)
            best = np.where(take, drop, best)
            rec = np.where(take, I[1 + dr:1 + dr + nr, 1 + dc:1 + dc + nc], rec)
    return rec.ravel()


def hydrology(surface, cell=None, stream_area=10_000.0, outlet=None, snap=20.0, level=None, seed=None,
              max_cells=4_000_000, progress=None):
    """Sinks (filled depth), D8 flow accumulation, stream network (contributing area ≥ stream_area m²),
    optional watershed above an outlet (x, y; snapped to the largest flow within `snap` m), optional
    inundation below a water level (connected to `seed` if given)."""
    from scipy import ndimage
    from core.surface import grid_extent  # noqa: F401
    Z, x0, ytop, c = surface_grid(surface, cell)
    if Z.size > max_cells:
        f = int(np.ceil(np.sqrt(Z.size / max_cells)))
        Z, x0, ytop, c = surface_grid(surface, c * f)
    nr, nc = Z.shape
    _report(progress, 0.05)
    F = _fill_depressions(Z, (lambda f: _report(progress, 0.05 + 0.4 * f)) if progress else None)
    _report(progress, 0.45)
    rec = _d8(F, c)
    valid = np.isfinite(Z).ravel()
    order = np.argsort(-np.where(valid, F.ravel(), -np.inf), kind="stable")
    acc = valid.astype(np.float64)
    rl, al = rec.tolist(), acc.tolist()
    for i in order.tolist():
        j = rl[i]
        if j >= 0 and valid[i]:
            al[j] += al[i]
    acc = np.asarray(al).reshape(nr, nc) * c * c          # contributing area m²
    acc = np.where(np.isfinite(Z), acc, np.nan)
    _report(progress, 0.7)
    sink = np.where(np.isfinite(Z), F - Z, np.nan)
    sink = np.where(sink > 0.01, sink, np.nan)
    xs = x0 + (np.arange(nc) + 0.5) * c
    ys = ytop - (np.arange(nr) + 0.5) * c

    def xy(i):
        r, cc = divmod(int(i), nc)
        return xs[cc], ys[r]
    # streams: trace from heads downstream
    st = (np.nan_to_num(acc) >= stream_area).ravel()
    up = np.zeros(nr * nc, np.int32)
    s_idx = np.flatnonzero(st)
    tgt = rec[s_idx]
    okt = (tgt >= 0)
    np.add.at(up, tgt[okt][st[tgt[okt]]], 1)
    heads = s_idx[up[s_idx] == 0]
    seen = np.zeros(nr * nc, bool)
    feats = []
    Zr = Z.ravel()
    accr = np.nan_to_num(acc).ravel()
    for h in heads:
        line = [h]
        i = h
        while True:
            j = rec[i]
            if j < 0 or not st[j]:
                break
            line.append(j)
            if seen[j] or up[j] > 1:
                break
            seen[j] = True
            i = j
        if len(line) >= 2:
            p = np.array([[*xy(i), Zr[i]] for i in line])
            feats.append({"type": "LineString", "parts": [p], "attrs": {"AREA_KM2": float(accr[line[-1]] / 1e6),
                                                                       "LENGTH_M": float(np.sum(np.hypot(
                                                                           *np.diff(p[:, :2], axis=0).T)))}})
    # confluence-to-confluence pieces below the first junction
    for j0 in np.flatnonzero(st & (up > 1)):
        line = [j0]
        i = j0
        while True:
            j = rec[i]
            if j < 0 or not st[j]:
                break
            line.append(j)
            if up[j] > 1:
                break
            i = j
        if len(line) >= 2:
            p = np.array([[*xy(i), Zr[i]] for i in line])
            feats.append({"type": "LineString", "parts": [p], "attrs": {"AREA_KM2": float(accr[line[-1]] / 1e6),
                                                                       "LENGTH_M": float(np.sum(np.hypot(
                                                                           *np.diff(p[:, :2], axis=0).T)))}})
    out_feats = {"streams": feats}
    layers = [("Flow accumulation (log10 m²)", layer(Z, x0, ytop, c, np.log10(np.where(acc > 0, acc, np.nan)),
                                                    "log10 area", "Viridis")),
              ("Sinks: fill depth (m)", layer(Z, x0, ytop, c, sink, "sink depth m", "Turbo"))]
    summary = [("Grid", f"{nc} × {nr} cells of {c:g} m"),
               ("Sinks", f"{np.isfinite(sink).sum() * c * c:,.0f} m² (deepest {_f(np.nanmax(sink) if np.isfinite(sink).any() else np.nan)} m)"),
               ("Streams", f"{len(feats)} reaches with ≥ {stream_area:,.0f} m² contributing area")]
    tables = {}
    # watershed
    if outlet is not None:
        ox, oy = outlet
        cc = int((ox - x0) / c)
        rr = int((ytop - oy) / c)
        k = max(1, int(snap / c))
        r0, r1 = max(rr - k, 0), min(rr + k + 1, nr)
        q0, q1 = max(cc - k, 0), min(cc + k + 1, nc)
        if r0 >= r1 or q0 >= q1:
            raise ValueError("The outlet lies outside the surface")
        win = np.nan_to_num(acc[r0:r1, q0:q1], nan=-1)
        a_, b_ = np.unravel_index(int(np.argmax(win)), win.shape)
        pour = (r0 + a_) * nc + (q0 + b_)
        donors = np.argsort(rec, kind="stable")
        rs = rec[donors]
        start = np.searchsorted(rs, np.arange(nr * nc))
        stop = np.searchsorted(rs, np.arange(nr * nc), side="right")
        mask = np.zeros(nr * nc, bool)
        mask[pour] = True
        front = np.array([pour])
        while len(front):
            nxt = np.concatenate([donors[start[i]:stop[i]] for i in front]) if len(front) else np.empty(0, int)
            nxt = nxt[~mask[nxt]]
            mask[nxt] = True
            front = nxt
        M = mask.reshape(nr, nc)
        area = float(M.sum() * c * c)
        ring = _mask_outline(M.astype(float), x0, ytop, c)
        if ring is not None:
            out_feats["watershed"] = [{"type": "Polygon", "parts": [ring], "attrs": {"AREA_KM2": area / 1e6}}]
        px, py = xy(pour)
        out_feats["outlet"] = [{"type": "Point", "parts": [np.array([[px, py]])], "attrs": {"AREA_KM2": area / 1e6}}]
        layers.append(("Watershed", layer(Z, x0, ytop, c, np.where(M, 1.0, np.nan), "watershed", "Viridis",
                                          rng=(0.0, 1.0))))
        summary.append(("Watershed", f"{area / 1e6:,.4f} km² above ({px:,.1f}, {py:,.1f})"))
        tables["Watershed"] = [{"outlet_x": px, "outlet_y": py, "area_m2": area, "area_km2": area / 1e6,
                                "mean_elevation": float(np.nanmean(Z[M])), "max_flow_path_drop":
                                    float(np.nanmax(Z[M]) - Z.ravel()[pour])}]
    # inundation
    if level is not None:
        W = np.isfinite(Z) & (Z < level)
        if seed is not None:
            lab, _n = ndimage.label(W, structure=np.ones((3, 3)))
            sc_, sr_ = int((seed[0] - x0) / c), int((ytop - seed[1]) / c)
            if 0 <= sr_ < nr and 0 <= sc_ < nc and lab[sr_, sc_] > 0:
                W = lab == lab[sr_, sc_]
            else:
                W = np.zeros_like(W)
        depth = np.where(W, level - Z, np.nan)
        area = float(W.sum() * c * c)
        vol = float(np.nansum(depth) * c * c)
        layers.append((f"Inundation depth at {level:g} m", layer(np.full_like(Z, level), x0, ytop, c, depth,
                                                                "water depth m", "Bathymetry"
                                                                if False else "Viridis")))
        ring = _mask_outline(W.astype(float), x0, ytop, c)
        if ring is not None:
            out_feats["inundation"] = [{"type": "Polygon", "parts": [ring], "attrs": {"LEVEL": level, "AREA_M2": area,
                                                                                      "VOL_M3": vol}}]
        summary.append(("Inundation", f"water level {level:g} m: {area:,.0f} m², {vol:,.0f} m³, max depth "
                                      f"{_f(np.nanmax(depth) if W.any() else np.nan)} m"
                                      + (" (connected to the seed point)" if seed is not None else "")))
        tables["Inundation"] = [{"level": level, "area_m2": area, "volume_m3": vol,
                                 "max_depth": float(np.nanmax(depth)) if W.any() else np.nan}]
    _report(progress, 1.0)
    return {"kind": "hydro", "title": "Hydrology", "summary": summary, "tables": tables, "layers": layers,
            "features": out_feats}


def _mask_outline(M, x0, ytop, c):
    """Largest outer ring of a 0/1 mask (contour at 0.5) in world coordinates."""
    import contourpy
    if M.sum() == 0:
        return None
    P = np.pad(M, 1)
    xs = x0 + (np.arange(P.shape[1]) - 0.5) * c
    ys = ytop - (np.arange(P.shape[0]) - 0.5) * c
    lines = contourpy.contour_generator(xs, ys, P).lines(0.5)
    if not lines:
        return None
    return max(lines, key=len)


# ================================================================ F. clearance
def clearance(pc, line, limit=5.0, classes=(3, 4, 5, 6), step=0.25, bin_m=5.0, progress=None):
    """3D distance from points of the chosen classes to a 3D polyline (N, 3); points closer than `limit`
    are infringements, grouped along the line into segments (chainage from the first vertex)."""
    from scipy.spatial import cKDTree
    L = np.asarray(line, float)
    if L.shape[1] < 3 or not np.isfinite(L[:, 2]).all():
        raise ValueError("The line needs a Z value at every vertex (3D line, cable profile, or height above ground)")
    seg = np.diff(L, axis=0)
    sl = np.linalg.norm(seg, axis=1)
    cum = np.r_[0.0, np.cumsum(sl)]
    ch = np.r_[np.arange(0.0, cum[-1], step), cum[-1]]
    si = np.clip(np.searchsorted(cum, ch, side="right") - 1, 0, len(seg) - 1)
    S = L[si] + seg[si] * ((ch - cum[si]) / np.maximum(sl[si], 1e-12))[:, None]
    idx = pc.active_indices()
    unclassified = bool(classes) and "Classification" not in pc.attrs
    if classes and not unclassified:
        idx = idx[np.isin(np.asarray(pc.attrs["Classification"])[idx], classes)]
    P = pc.xyz[idx]
    lo, hi = S.min(axis=0) - limit - step, S.max(axis=0) + limit + step
    m = np.all((P >= lo) & (P <= hi), axis=1)
    P, idx = P[m], idx[m]
    _report(progress, 0.3)
    d, j = cKDTree(S).query(P, distance_upper_bound=limit + step)
    ok = np.isfinite(d)
    P, idx, j = P[ok], idx[ok], j[ok]
    # exact distance to the segment of the nearest sample
    k = si[j]
    a = L[k]
    v = seg[k]
    t = np.clip(np.einsum("ij,ij->i", P - a, v) / np.maximum(sl[k] ** 2, 1e-12), 0, 1)
    q = a + v * t[:, None]
    dist = np.linalg.norm(P - q, axis=1)
    pch = cum[k] + t * sl[k]
    bad = dist < limit
    _report(progress, 0.8)
    P, dist, pch, idx, q = P[bad], dist[bad], pch[bad], idx[bad], q[bad]
    cls = np.asarray(pc.attrs["Classification"])[idx] if "Classification" in pc.attrs else np.zeros(len(idx), int)
    # segments: bins with infringements, merged when adjacent
    rows, feats = [], []
    if len(pch):
        b = np.floor(pch / bin_m).astype(int)
        ub = np.unique(b)
        groups = np.split(ub, np.flatnonzero(np.diff(ub) > 1) + 1)
        for g in groups:
            mm = np.isin(b, g)
            jmin = np.argmin(np.where(mm, dist, np.inf))
            c0, c1 = g[0] * bin_m, min((g[-1] + 1) * bin_m, cum[-1])
            vals, cnts = np.unique(cls[mm], return_counts=True)
            rows.append({"ch_from": float(c0), "ch_to": float(c1), "length": float(c1 - c0), "points": int(mm.sum()),
                         "min_clearance": float(dist[jmin]), "horizontal": float(np.hypot(*(P[jmin, :2] - q[jmin, :2]))),
                         "vertical": float(P[jmin, 2] - q[jmin, 2]), "class": int(vals[np.argmax(cnts)]),
                         "x": float(P[jmin, 0]), "y": float(P[jmin, 1]), "z": float(P[jmin, 2])})
            cc = np.r_[np.arange(c0, c1, step), c1]
            ii = np.clip(np.searchsorted(cum, cc, side="right") - 1, 0, len(seg) - 1)
            pts = L[ii] + seg[ii] * ((cc - cum[ii]) / np.maximum(sl[ii], 1e-12))[:, None]
            feats.append({"type": "LineString", "parts": [pts],
                          "attrs": {"CH_FROM": float(c0), "CH_TO": float(c1), "MIN_CLR": float(dist[jmin]),
                                    "POINTS": int(mm.sum())}})
    _report(progress, 1.0)
    pts_rows = [{"x": float(p[0]), "y": float(p[1]), "z": float(p[2]), "chainage": float(c_), "clearance": float(d_),
                 "class": int(k_)} for p, c_, d_, k_ in zip(P, pch, dist, cls)]
    return {"kind": "clearance", "title": "Clearance check",
            "summary": [("Line", f"{cum[-1]:,.1f} m (3D) · limit {limit:g} m · classes "
                                 f"{', '.join(str(c) for c in classes) if classes else 'all'}"),
                        ("Infringements", f"{len(pts_rows):,} points in {len(rows)} segment(s)"),
                        ("Smallest clearance", f"{_f(min((r['min_clearance'] for r in rows), default=np.nan))} m")]
                       + ([("Warning", "the point cloud has no classification — ALL points were checked (ground "
                                       "included); classify it to check vegetation / buildings only")]
                          if unclassified else []),
            "tables": {"Segments": rows, "Points": pts_rows[:200_000]},
            "features": {"infringements": feats,
                         "line": [{"type": "LineString", "parts": [L], "attrs": {"LIMIT_M": limit}}]},
            "layers": []}
