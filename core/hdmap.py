"""HD map annotation of point clouds: point classes, map features, semi-automatic extraction and lanelets.

Point classes: ASPRS LAS 1.4 codes where they exist (2 ground, 11 road surface, 6 building, 3–5 vegetation,
13 wire guard, 15 transmission tower, 17 bridge deck) and user-definable codes 64–79 for HD map objects.

Map features live in a VectorLayer (meta["hdmap"] = True); every feature has FID and TYPE and the attributes
below. TYPE ↔ Lanelet2 tags are listed in FEATURE_TYPES (exported by fileio.lanelet2)."""
from __future__ import annotations

import math

import numpy as np

# code → (key, label, colour)
CLASSES = {
    2: ("ground", "Ground", "#a6733f"),
    11: ("road_surface", "Road surface", "#595959"),
    6: ("building", "Building", "#e64d40"),
    3: ("low_veg", "Low vegetation", "#99e680"),
    4: ("med_veg", "Medium vegetation", "#4dbf4d"),
    5: ("high_veg", "High vegetation", "#1a8026"),
    13: ("wire_guard", "Wire guard / shield", "#c0c0ff"),
    15: ("tower", "Transmission tower", "#d0a0ff"),
    17: ("bridge_deck", "Bridge deck", "#f2a633"),
    64: ("lane_marking", "Lane marking", "#ffffff"),
    65: ("curb", "Road edge / curb", "#ff9e42"),
    66: ("pole", "Pole", "#00e5ff"),
    67: ("traffic_sign", "Traffic sign", "#ff3d7f"),
    68: ("traffic_light", "Traffic light", "#ffe23d"),
    69: ("guardrail", "Guardrail / barrier", "#b0bec5"),
    70: ("crosswalk", "Crosswalk marking", "#e0e0ff"),
    71: ("stop_line", "Stop line", "#ff5252"),
    72: ("road_symbol", "Road arrow / symbol", "#ffd0a0"),
    73: ("sidewalk", "Sidewalk", "#8d6e63"),
    74: ("vehicle", "Vehicle (temporary)", "#7e57c2"),
    75: ("pedestrian", "Pedestrian / dynamic", "#ab47bc"),
    76: ("median", "Median / traffic island", "#9ccc65"),
}
HD_CLASS_CODES = tuple(sorted(c for c in CLASSES if c >= 64))

# TYPE → geometry, label, colour, Lanelet2 type, default subtype, subtype choices
FEATURE_TYPES = {
    "lane_line": ("LineString", "Lane line", "#76ff03", "line_thin",
                  ("solid", "dashed", "solid_solid", "dashed_solid", "solid_dashed")),
    "lane_line_thick": ("LineString", "Lane line (thick)", "#c6ff00", "line_thick",
                        ("solid", "dashed", "solid_solid")),
    "road_edge": ("LineString", "Road edge", "#ff9e42", "road_border", ("",)),
    "curb": ("LineString", "Curb", "#ffb74d", "curbstone", ("high", "low")),
    "stop_line": ("LineString", "Stop line", "#ff5252", "stop_line", ("",)),
    "virtual_line": ("LineString", "Virtual line", "#90a4ae", "virtual", ("",)),
    "guardrail": ("LineString", "Guardrail", "#b0bec5", "guard_rail", ("",)),
    "crosswalk": ("Polygon", "Crosswalk", "#e040fb", "zebra_marking", ("",)),
    "road_symbol": ("Polygon", "Road arrow / symbol", "#ffd0a0", "arrow",
                    ("straight", "left", "right", "straight_left", "straight_right", "other")),
    "pole": ("Point", "Pole", "#00e5ff", "pole", ("",)),
    "traffic_sign": ("Point", "Traffic sign", "#ff3d7f", "traffic_sign", ("",)),
    "traffic_light": ("Point", "Traffic light", "#ffe23d", "traffic_light", ("red_yellow_green", "")),
    "lanelet": ("Polygon", "Lanelet (lane)", "#2979ff", "lanelet", ("road", "crosswalk", "walkway")),
}
COLOR_MAP = {k: v[2] for k, v in FEATURE_TYPES.items()}
FIELDS = [("FID", "N"), ("TYPE", "C"), ("SUBTYPE", "C"), ("COLOR", "C"), ("WIDTH", "N"), ("HEIGHT", "N"),
          ("LANE_ID", "C"), ("SPEED", "N"), ("LEFT_FID", "N"), ("RIGHT_FID", "N"), ("NOTE", "C")]


def class_name(code):
    return CLASSES.get(int(code), (None, f"Class {int(code)}", None))[1]


def new_layer():
    from core.vector_layer import VectorLayer
    return VectorLayer([], meta={"hdmap": True})


def is_hdmap(layer):
    if (getattr(layer, "meta", None) or {}).get("hdmap"):
        return True
    return bool(layer.features) and all("TYPE" in f["attrs"] and "FID" in f["attrs"] for f in layer.features)


def next_fid(layer):
    ids = [int(f["attrs"].get("FID") or 0) for f in layer.features]
    return (max(ids) if ids else 0) + 1


def make_feature(layer, ftype, coords, **attrs):
    """Add a feature to the layer: coords (N, 3) world XYZ. Returns it."""
    geom = FEATURE_TYPES[ftype][0]
    p = np.asarray(coords, float)
    if p.ndim == 1:
        p = p[None, :]
    if geom == "LineString" and len(p) < 2:
        raise ValueError("A line needs at least two points")
    if geom == "Polygon":
        if len(p) < 3:
            raise ValueError("An area needs at least three points")
        if not np.allclose(p[0], p[-1]):
            p = np.vstack([p, p[:1]])
    if geom == "Point":
        p = p[:1]
    a = {"FID": next_fid(layer), "TYPE": ftype, "SUBTYPE": FEATURE_TYPES[ftype][4][0], "COLOR": "white",
         "WIDTH": 0.15 if ftype.startswith("lane_line") else None, "HEIGHT": None, "LANE_ID": "", "SPEED": None,
         "LEFT_FID": None, "RIGHT_FID": None, "NOTE": ""}
    a.update({k: v for k, v in attrs.items() if v is not None or k in a})
    f = {"type": geom, "parts": [p], "attrs": a}
    layer.features.append(f)
    return f


def feature_by_fid(layer, fid):
    return next((f for f in layer.features if int(f["attrs"].get("FID") or -1) == int(fid)), None)


def line_features(layer, types=None):
    return [f for f in layer.features if f["type"] == "LineString"
            and (types is None or f["attrs"].get("TYPE") in types)]


# ---------------------------------------------------------------- lanelets
def _orient_like(a, b):
    """Return b reversed if it runs against a."""
    d1 = np.hypot(*(a[0, :2] - b[0, :2])) + np.hypot(*(a[-1, :2] - b[-1, :2]))
    d2 = np.hypot(*(a[0, :2] - b[-1, :2])) + np.hypot(*(a[-1, :2] - b[0, :2]))
    return b[::-1] if d2 < d1 else b


def _side(left, right):
    """+1 if `left` is on the left of the driving direction of `right`→ (mean cross product)."""
    d = right[-1, :2] - right[0, :2]
    c = np.mean([d[0] * (q[1] - right[0, 1]) - d[1] * (q[0] - right[0, 0]) for q in left[:, :2]])
    return 1 if c > 0 else -1


def _densify(xy, step):
    out = [xy[:1]]
    for a, b in zip(xy[:-1], xy[1:]):
        n = max(1, int(np.ceil(np.hypot(*(b - a)) / step)))
        t = np.linspace(0, 1, n + 1)[1:, None]
        out.append(a + (b - a) * t)
    return np.vstack(out)


def point_line_distance(pts, line):
    """Distance of each point (N, 2) to a polyline (M, 2)."""
    a, b = line[:-1], line[1:]
    d = b - a
    L2 = np.maximum((d * d).sum(axis=1), 1e-12)
    out = np.full(len(pts), np.inf)
    for s in range(0, len(pts), 2000):
        p = pts[s:s + 2000, None, :]
        t = np.clip(((p - a) * d).sum(axis=2) / L2, 0, 1)
        q = a + t[..., None] * d
        out[s:s + 2000] = np.sqrt(((p - q) ** 2).sum(axis=2)).min(axis=1)
    return out


def make_lanelet(layer, left_fid, right_fid, subtype="road", speed=None, lane_id=""):
    """Lanelet between two boundary lines (Lanelet2: left / right bound in the driving direction, which is the
    direction of the left line as drawn). Stored as a polygon (left forward + right backward)."""
    L = feature_by_fid(layer, left_fid)
    R = feature_by_fid(layer, right_fid)
    if L is None or R is None or L["type"] != "LineString" or R["type"] != "LineString":
        raise ValueError("Choose two line features as the lane boundaries")
    if int(left_fid) == int(right_fid):
        raise ValueError("The two boundaries must be different lines")
    lp, rp = L["parts"][0], _orient_like(L["parts"][0], R["parts"][0])
    if _side(lp, rp) < 0:                          # the line given as left lies on the right: swap
        L, R = R, L
        lp, rp = rp, lp
    ring = np.vstack([lp, rp[::-1], lp[:1]])
    w = float(np.median(point_line_distance(_densify(lp[:, :2], 1.0), rp[:, :2])))
    return make_feature(layer, "lanelet", ring, SUBTYPE=subtype, LEFT_FID=int(L["attrs"]["FID"]),
                        RIGHT_FID=int(R["attrs"]["FID"]), SPEED=speed, LANE_ID=lane_id, WIDTH=round(w, 2),
                        COLOR="")


# ---------------------------------------------------------------- semi-automatic extraction
def _components(ix, iy, bridge=0):
    """Connected components (8-neighbourhood) of occupied grid cells → label per point. bridge = k joins cells up
    to k empty cells apart (sparse scans of thin markings)."""
    from scipy import ndimage
    x0, y0 = ix.min(), iy.min()
    nx, ny = int(ix.max() - x0) + 1, int(iy.max() - y0) + 1
    if float(nx) * ny > 400_000_000:
        raise ValueError("Area too large for this cell size — work on a smaller part (subset)")
    occ = np.zeros((ny, nx), bool)
    occ[iy - y0, ix - x0] = True
    if bridge > 0:
        occ = ndimage.binary_dilation(occ, structure=np.ones((3, 3), bool), iterations=int(bridge))
    lab, n = ndimage.label(occ, structure=np.ones((3, 3), bool))
    return lab[iy - y0, ix - x0], n


def _flat_mask(xyz, idx, cell=0.25, max_above=0.08, min_share=0.3):
    """Points of the lowest SURFACE (road paint), not of walls, vehicles, signs or tree crowns over the road:
    the point is at most max_above above its cell's 5th-percentile height, the cell holds a surface — at least
    min_share × the typical number of such low points per cell (the base line of a wall or a car body has only a
    few) — and nothing rises from it (no points 0.35–1.0 m above: the foot of a wall, a car, a pole)."""
    P = xyz[idx]
    ix = np.floor((P[:, 0] - P[:, 0].min()) / cell).astype(np.int64)
    iy = np.floor((P[:, 1] - P[:, 1].min()) / cell).astype(np.int64)
    key = iy * (int(ix.max()) + 1) + ix
    z = P[:, 2]
    o = np.lexsort((z, key))
    ks = key[o]
    st = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    cnt = np.diff(np.r_[st, len(ks)])
    p5 = z[o][st + (cnt * 5) // 100]
    grp = np.empty(len(z), np.int64)
    grp[o] = np.repeat(np.arange(len(st)), cnt)
    low = z <= p5[grp] + max_above
    nlow = np.bincount(grp[low], minlength=len(st))
    typical = float(np.median(nlow[nlow > 0])) if (nlow > 0).any() else 0.0
    # a wall, car body or pole rises from the surface: points 0.35 … 1.0 m above it (a kerb is lower, a tree
    # crown over the road higher)
    dz = z - p5[grp]
    nvert = np.bincount(grp[(dz >= 0.35) & (dz <= 1.0)], minlength=len(st))
    return low & (nlow[grp] >= max(3.0, min_share * typical)) & (nvert[grp] < 2)


def marking_candidates(xyz, intensity, idx, cell=2.0, k=3.0, min_pct=80.0, flat=True):
    """Points much brighter than the road around them: intensity > median + k·1.4826·MAD of their cell
    (cell × cell m) and above the min_pct percentile overall; with flat=True only points on a locally flat,
    lowest surface (see _flat_mask — drops bright walls, vehicles and signs). Returns the subset of idx."""
    idx = np.asarray(idx, np.int64)
    if flat and len(idx):
        idx = idx[_flat_mask(xyz, idx)]
    if len(idx) == 0:
        return idx
    I = np.asarray(intensity, float)[idx]
    P = xyz[idx]
    ix = np.floor((P[:, 0] - P[:, 0].min()) / cell).astype(np.int64)
    iy = np.floor((P[:, 1] - P[:, 1].min()) / cell).astype(np.int64)
    key = iy * (int(ix.max()) + 1) + ix
    o = np.lexsort((I, key))
    ks, Is = key[o], I[o]
    st = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    cnt = np.diff(np.r_[st, len(ks)])
    med = Is[st + cnt // 2]
    grp = np.repeat(np.arange(len(st)), cnt)
    dev = np.abs(Is - med[grp])
    o2 = np.lexsort((dev, grp))
    mad = dev[o2][st + cnt // 2]
    thr_sorted = med[grp] + k * 1.4826 * np.maximum(mad[grp], 1e-6)
    thr = np.empty(len(I))
    thr[o] = thr_sorted
    floor_ = np.percentile(I, min_pct)
    return idx[(I > thr) & (I >= floor_)]


def _centreline(q, c, d, nrm, step=1.0):
    """Median centre line of a stripe's points along its main direction d: (pts (M,3), lateral residuals)."""
    t = (q[:, :2] - c) @ d
    s = (q[:, :2] - c) @ nrm
    L = float(t.max() - t.min())
    nb = max(1, int(round(L / step)))
    b = np.minimum(((t - t.min()) / max(L, 1e-9) * nb).astype(int), nb - 1)
    o = np.argsort(b, kind="stable")
    bs = b[o]
    st = np.flatnonzero(np.r_[True, bs[1:] != bs[:-1]])
    tm = np.array([np.median(t[o[i:j]]) for i, j in zip(st, np.r_[st[1:], len(bs)])])
    sm = np.array([np.median(s[o[i:j]]) for i, j in zip(st, np.r_[st[1:], len(bs)])])
    zm = np.array([np.median(q[o[i:j], 2]) for i, j in zip(st, np.r_[st[1:], len(bs)])])
    # the line runs from end to end: extend the first / last bin to the extreme points
    tm[0], tm[-1] = (t.min(), t.max()) if len(tm) > 1 else (t.min(), t.max())
    if len(tm) == 1:
        tm, sm, zm = np.r_[t.min(), t.max()], np.r_[sm, sm], np.r_[zm, zm]
    res = s - np.interp(t, tm, sm)
    pts = np.column_stack([c + np.outer(tm, d) + np.outer(sm, nrm), zm])
    return pts, res, L


def _stripe_test(q, sel, min_len, max_width, max_piece):
    """(stripe dict or None, length, width) for points q — a stripe has length ≥ min_len, width (spread across
    its median centre line, so curves keep their true width) ≤ max_width, length ≥ 2.5 × width and no corner."""
    c = q[:, :2].mean(axis=0)
    _u, _sv, vt = np.linalg.svd(q[:, :2] - c, full_matrices=False)
    d, nrm = vt[0], vt[1]
    pts, res, length = _centreline(q, c, d, nrm)
    width = float(np.percentile(res, 95) - np.percentile(res, 5))
    hd = np.unwrap(np.arctan2(*np.diff(pts[:, :2], axis=0).T[::-1])) if len(pts) > 2 else np.zeros(1)
    turn = float(np.abs(np.diff(hd)).sum()) if len(hd) > 1 else 0.0
    smooth = turn <= length / 25.0 + 0.25
    ok = length >= min_len and width <= max_width and length >= 2.5 * width and length <= max_piece and smooth
    st = {"pts": pts, "p0": pts[0], "p1": pts[-1], "dir": d, "len": length, "width": width, "idx": sel} if ok else None
    return st, length, width, (c, d, res)


def _peel(q, sel, min_len, max_width, max_piece, out, rng):
    """Straight runs out of a blob of several touching marks (lane line + stop line, two lines joined by specks):
    repeatedly the line with most points within max_width / 2 (RANSAC), its longest run without a 0.6 m gap."""
    rem = np.ones(len(q), bool)
    tol = max_width / 2 + 0.03
    for _it in range(16):
        r = np.flatnonzero(rem)
        if len(r) < 5:
            return
        Q = q[r, :2]
        best, best_n = None, 0
        for _k in range(120):
            i, j = rng.integers(0, len(r), 2)
            v = Q[j] - Q[i]
            L = float(np.hypot(*v))
            if L < 0.5:
                continue
            nv = np.array([-v[1], v[0]]) / L
            inl = np.abs((Q - Q[i]) @ nv) <= tol
            n_ = int(inl.sum())
            if n_ > best_n:
                best, best_n = (inl, v / L), n_
        if best is None or best_n < 5:
            return
        inl, dv = best
        ii = r[inl]
        t = q[ii, :2] @ dv
        o = np.argsort(t)
        brk = np.flatnonzero(np.diff(t[o]) > 0.6)
        runs = np.split(o, brk + 1)
        run = max(runs, key=len)
        take = ii[run]
        rem[take] = False
        if len(take) >= 5:
            st, *_ = _stripe_test(q[take], sel[take], min_len, max_width, max_piece)
            if st is not None:
                # the rest of this mark (points just outside the band of a slightly tilted RANSAC line)
                rr = np.flatnonzero(rem)
                if len(rr):
                    dd = point_line_distance(q[rr, :2], st["pts"][:, :2])
                    t2 = (q[rr, :2] - st["p0"][:2]) @ st["dir"]
                    tl = float((st["p1"][:2] - st["p0"][:2]) @ st["dir"])
                    lo_, hi_ = min(0.0, tl) - 0.2, max(0.0, tl) + 0.2
                    extra = rr[(dd <= max(st["width"], 0.1) + 0.1) & (t2 >= lo_) & (t2 <= hi_)]
                    if len(extra):
                        rem[extra] = False
                        allp = np.r_[take, extra]
                        st = {**st, "idx": sel[allp]}
                out.append(st)


def _empty_band(res, step=0.02, min_band=0.06):
    """Position of an empty band ≥ min_band wide inside the lateral spread of the points (two parallel lines
    joined into one component), with ≥ 25 % of the points on either side; None if there is none."""
    lo, hi = np.percentile(res, [3, 97])
    if hi - lo < 2 * min_band:
        return None
    h, e = np.histogram(res, bins=np.arange(lo, hi + step, step))
    if len(h) < 5:
        return None
    low = h < 0.1 * h.mean()
    best, run, start = None, 0, 0
    for k, v in enumerate(low):
        if v:
            if run == 0:
                start = k
            run += 1
            if 0 < start and k < len(h) - 1 and run * step >= min_band and (best is None or run > best[1]):
                best = (start, run)
        else:
            run = 0
    if best is None:
        return None
    c = float(e[best[0]] + best[1] * step / 2)
    f = (res > c).mean()
    return c if 0.25 <= f <= 0.75 else None


def _stripes_of(q, sel, min_len, max_width, out, depth=0, max_piece=8.0, rng=None):
    """Stripes of one component (points q, indices sel). Long components are cut into pieces of ≤ max_piece m
    along their main direction (curves keep their shape; the chaining joins the pieces again); a piece that is not
    a stripe loses its bright specks (≤ 15 % of the points far from the centre line) or, when several marks
    touch (a lane line running into a stop line), is peeled into straight runs."""
    if len(q) < 5:
        return
    if rng is None:
        rng = np.random.default_rng(0)
    st, length, width, (c, d, res) = _stripe_test(q, sel, min_len, max_width, max_piece)
    if length <= max_piece and width >= 0.2 and depth < 24:
        # two lines side by side joined into one component (double centre line): an empty band along the piece
        lat = (q[:, :2] - c) @ np.array([-d[1], d[0]])
        cut = _empty_band(lat)
        if cut is not None:
            m = lat > cut
            _stripes_of(q[m], sel[m], min_len, max_width, out, depth + 1, max_piece, rng)
            _stripes_of(q[~m], sel[~m], min_len, max_width, out, depth + 1, max_piece, rng)
            return
    if st is not None:
        out.append(st)
        return
    if length > max_piece and depth < 24:
        t = (q[:, :2] - c) @ d
        # cut near the middle where the fewest points are (never along a stop line crossing the cut)
        lo_, hi_ = np.percentile(t, [35, 65])
        edges = np.arange(lo_, hi_ + 0.1, 0.1)
        if len(edges) > 2:
            h_, _e = np.histogram(t, bins=edges)
            cut = float(edges[int(np.argmin(h_))] + 0.05)
        else:
            cut = float(np.median(t))
        m = t <= cut
        _stripes_of(q[m], sel[m], min_len, max_width, out, depth + 1, max_piece, rng)
        _stripes_of(q[~m], sel[~m], min_len, max_width, out, depth + 1, max_piece, rng)
        return
    if length < min_len:
        return
    inl = np.abs(res - np.median(res)) <= max_width / 2 + 0.05
    if 0.85 * len(q) <= inl.sum() < len(q):
        st2, *_ = _stripe_test(q[inl], sel[inl], min_len, max_width, max_piece)
        if st2 is not None:
            out.append(st2)
            return
    _peel(q, sel, min_len, max_width, max_piece, out, rng)


def _crosswalk_bars(stripes, max_angle=10.0, min_sep=0.5, max_sep=1.8, min_bars=3, max_len=8.0, min_width=0.2):
    """Stripes that belong to a zebra crossing: ≥ min_bars short (≤ max_len), wide (≥ min_width), similar
    parallel stripes side by side (lateral spacing min_sep … max_sep m, overlapping along their length) — lane
    line dashes next to an arrow are not. Returns a boolean mask."""
    n = len(stripes)
    bar = np.zeros(n, bool)
    if n < min_bars:
        return bar
    short = np.array([s["len"] <= max_len and s["width"] >= min_width for s in stripes])
    similar = (lambda a, b: short[a] and short[b] and
               max(stripes[a]["len"], stripes[b]["len"]) <= 2.0 * min(stripes[a]["len"], stripes[b]["len"]))
    cosmax = math.cos(math.radians(max_angle))
    mids = np.array([(s["p0"][:2] + s["p1"][:2]) / 2 for s in stripes])
    from scipy.spatial import cKDTree
    tree = cKDTree(mids)
    for i, si in enumerate(stripes):
        d = si["dir"]
        nb = 0
        for j in tree.query_ball_point(mids[i], max_sep + si["len"] / 2):
            if j == i or not similar(i, j):
                continue
            sj = stripes[j]
            if abs(float(d @ sj["dir"])) < cosmax:
                continue
            v = mids[j] - mids[i]
            lat = abs(float(v[0] * d[1] - v[1] * d[0]))
            along = abs(float(v @ d))
            if min_sep <= lat <= max_sep and along < 0.5 * (si["len"] + sj["len"]) / 2:
                nb += 1
        bar[i] = nb >= 2
    # a bar's neighbours are bars too (the outer bars of a crossing have one neighbour)
    if bar.any():
        for i in np.flatnonzero(~bar):
            si = stripes[i]
            for j in tree.query_ball_point(mids[i], max_sep + si["len"] / 2):
                if bar[j] and j != i and similar(i, j) and abs(float(si["dir"] @ stripes[j]["dir"])) >= cosmax:
                    v = mids[j] - mids[i]
                    lat = abs(float(v[0] * si["dir"][1] - v[1] * si["dir"][0]))
                    if min_sep <= lat <= max_sep and abs(float(v @ si["dir"])) < 0.5 * (si["len"] + stripes[j]["len"]) / 2:
                        bar[i] = True
                        break
    return bar


def _denoise(xyz, cand, idx, cell=None):
    """Drop isolated bright points (intensity noise) and choose the grid cell from the point spacing.
    Typical spacing h = 1 / √(points per m²) of the scope; cell = clip(1.5 h, 0.05, 0.2) m unless given. A candidate
    is kept with ≥ max(2, 0.3 × median) candidate neighbours within 2.5 h (a painted line has many, noise few)."""
    from scipy.spatial import cKDTree
    P = xyz[idx, :2]
    k1 = np.floor(P / 1.0).astype(np.int64)
    key = k1[:, 1] * (int(k1[:, 0].max() - k1[:, 0].min()) + 1) + (k1[:, 0] - k1[:, 0].min())
    _u, cnt = np.unique(key, return_counts=True)
    dens = float(np.median(cnt)) if len(cnt) else 1.0
    h = 1.0 / math.sqrt(max(dens, 1.0))
    if cell is None:
        cell = float(np.clip(1.5 * h, 0.05, 0.2))
    Q = xyz[cand, :2]
    tree = cKDTree(Q)
    nb = np.asarray(tree.query_ball_point(Q, max(2.5 * h, 0.08), return_length=True)) - 1
    thr = max(2.0, 0.3 * float(np.median(nb)))
    return cand[nb >= thr], cell


def extract_markings(xyz, intensity, idx, cell=None, k=3.0, min_pct=80.0, min_len=1.0, max_width=0.45,
                     max_gap=12.0, max_angle=10.0, max_offset=0.35, min_line=5.0, flat=True, solid_cover=0.75,
                     progress=None):
    """Lane line extraction from bright road points.
    1. candidates: locally bright points on a flat, lowest surface (marking_candidates)
    2. isolated bright points are dropped; components on a `cell` grid (default from the point spacing,
       bridging two empty cells); a component is a STRIPE when its length ≥ min_len,
       width ≤ max_width and length ≥ 2.5 × width — long components are split until they pass (curves)
    3. zebra-crossing bars (≥ 3 parallel stripes side by side) are set aside as other marks
    4. stripes are chained along their direction: next stripe within max_gap, direction within max_angle and
       lateral offset ≤ max_offset → one line; 'solid' when paint covers ≥ solid_cover of its length (an
       occlusion gap does not make a solid line dashed), otherwise 'dashed'
    5. wide stripes (≥ 0.25 m) across the thin lane lines near them are stop lines (chained among themselves)
    Returns dict(lines=[(coords (N,3), subtype, n_stripes)], stop_lines=[(coords (2,3), n)], stripe_pts,
    other_pts) — point indices of stripes (lane / stop lines) and of other bright marks (arrows, crosswalk bars,
    text)."""
    cand = marking_candidates(xyz, intensity, idx, k=k, min_pct=min_pct, flat=flat)
    if progress:
        progress(0.25)
    if len(cand) < 5:
        return {"lines": [], "stop_lines": [], "stripe_pts": np.empty(0, np.int64), "other_pts": cand}
    cand, cell = _denoise(xyz, cand, idx, cell)
    if len(cand) < 5:
        return {"lines": [], "stop_lines": [], "stripe_pts": np.empty(0, np.int64), "other_pts": cand}
    P = xyz[cand]
    ix = np.floor(P[:, 0] / cell).astype(np.int64)
    iy = np.floor(P[:, 1] / cell).astype(np.int64)
    lab, n = _components(ix, iy, bridge=2)
    if progress:
        progress(0.45)
    o = np.argsort(lab, kind="stable")
    ls = lab[o]
    st = np.flatnonzero(np.r_[True, ls[1:] != ls[:-1]])
    en = np.r_[st[1:], len(ls)]
    stripes = []
    for a, b in zip(st, en):
        if b - a < 5:
            continue
        sel = o[a:b]
        _stripes_of(P[sel], sel, min_len, max_width, stripes)
    stripes = _merge_halves(stripes, P, min_len, max_width)
    bars = _crosswalk_bars(stripes)
    stripes = [s_ for s_, b_ in zip(stripes, bars) if not b_]
    stripe_mask = np.zeros(len(cand), bool)
    for s_ in stripes:
        stripe_mask[s_["idx"]] = True
    if progress:
        progress(0.7)
    stripes, stop_pieces = _split_stop_pieces(stripes)
    stops = []
    for ch in sorted(_chain(stop_pieces, 1.5, 15.0, 0.3), key=lambda c_: -sum(s_["len"] for s_ in c_)):
        P_ = _join_pieces(ch)
        seg = P_[[0, -1]].copy()
        merged = False
        for k_, (ks, kn) in enumerate(stops):            # pieces of one stop line, side by side or overlapping
            u = ks[1, :2] - ks[0, :2]
            Lk = float(np.hypot(*u))
            u = u / max(Lk, 1e-9)
            w = seg[:, :2] - ks[0, :2]
            lat = np.abs(w[:, 0] * u[1] - w[:, 1] * u[0])
            tt = w @ u
            if lat.max() < 0.5 and tt.max() > -1.5 and tt.min() < Lk + 1.5:
                t0, t1 = min(0.0, tt.min()), max(Lk, tt.max())
                stops[k_] = (np.vstack([np.r_[ks[0, :2] + u * t0, ks[0, 2]], np.r_[ks[0, :2] + u * t1, ks[1, 2]]]),
                             kn + len(ch))
                merged = True
                break
        if merged:
            continue
        if float(np.hypot(*(seg[1, :2] - seg[0, :2]))) >= 1.5:
            stops.append((seg, len(ch)))
        else:
            stripe_mask[np.concatenate([s_["idx"] for s_ in ch])] = False
    lines = _chain(stripes, max_gap, max_angle, max_offset)
    out, short = [], []
    for ch in lines:
        P_ = _join_pieces(ch)
        # unpainted length: gaps between consecutive stripes longer than 1.5 m (shorter ones are scan holes)
        gaps = [float(np.hypot(*(b_["p0"][:2] - a_["p1"][:2]))) for a_, b_ in zip(ch[:-1], ch[1:])]
        keep = np.r_[True, np.hypot(*np.diff(P_[:, :2], axis=0).T) > 0.05]      # pieces of a split stripe touch
        P_ = P_[keep]
        L = float(np.hypot(*np.diff(P_[:, :2], axis=0).T).sum())
        wid = float(np.median([s_["width"] for s_ in ch]))
        unpainted = sum(g for g in gaps if g > 1.5)
        rec = (P_, "solid" if L - unpainted >= solid_cover * L else "dashed", len(ch), wid, L, ch)
        (out if L >= min_line else short).append(rec)
    final = []
    for r_ in out + short:
        P_, sub, n_, wid, L, ch = r_
        if L < min_line:
            stripe_mask[np.concatenate([s_["idx"] for s_ in ch])] = False      # short: an arrow / text part
            continue
        final.append((P_, sub, n_))
    out = final
    if progress:
        progress(1.0)
    return {"lines": out, "stop_lines": stops, "stripe_pts": cand[stripe_mask], "other_pts": cand[~stripe_mask]}


def _merge_halves(stripes, P, min_len, max_width, max_lat=0.25):
    """Stripes lying side by side (parallel, centre lines < max_lat apart, overlapping) are two halves of one
    wide mark cut lengthwise (a stop line) — merged again. Double lane lines (≥ 0.3 m apart) stay apart."""
    n = len(stripes)
    if n < 2:
        return stripes
    from scipy.spatial import cKDTree
    mids = np.array([(s_["p0"][:2] + s_["p1"][:2]) / 2 for s_ in stripes])
    tree = cKDTree(mids)
    alive = np.ones(n, bool)
    out = []
    for i in np.argsort([-s_["len"] for s_ in stripes]):
        if not alive[i]:
            continue
        si = stripes[i]
        for j in tree.query_ball_point(mids[i], si["len"] / 2 + 0.5):
            if j == i or not alive[j]:
                continue
            sj = stripes[j]
            if abs(float(si["dir"] @ sj["dir"])) < 0.97:
                continue
            short, long_ = (sj, si) if sj["len"] <= si["len"] else (si, sj)
            if float(point_line_distance(short["pts"][:, :2], long_["pts"][:, :2]).max()) >= max_lat:
                continue
            u = long_["dir"]
            tl = sorted([float(long_["p0"][:2] @ u), float(long_["p1"][:2] @ u)])
            ts = sorted([float(short["p0"][:2] @ u), float(short["p1"][:2] @ u)])
            if min(tl[1], ts[1]) - max(tl[0], ts[0]) < 0.5 * (ts[1] - ts[0]):
                continue
            idx_ = np.r_[si["idx"], sj["idx"]]
            st, *_ = _stripe_test(P[idx_], idx_, min_len, max_width, 1e9)
            if st is not None:
                si = st
                alive[j] = False
        alive[i] = False
        out.append(si)
    return out


def _split_stop_pieces(stripes, min_width=0.25, radius=15.0):
    """Wide stripes (≥ min_width) running ACROSS the thin lane lines near them (> 60°) are stop-line pieces.
    Returns (lane stripes, stop pieces); wide stripes along the lane lines stay lane lines (thick lines)."""
    thin = [s_ for s_ in stripes if s_["width"] < min_width]
    wide = [s_ for s_ in stripes if s_["width"] >= min_width]
    if not wide or not thin:
        return stripes, []
    from scipy.spatial import cKDTree
    tp = np.vstack([s_["pts"][:, :2] for s_ in thin])
    td = np.vstack([np.repeat(s_["dir"][None, :], len(s_["pts"]), axis=0) for s_ in thin])
    tree = cKDTree(tp)
    lane, stop = list(thin), []
    for s_ in wide:
        mid = (s_["p0"][:2] + s_["p1"][:2]) / 2
        dist, j = tree.query(mid)
        if dist <= radius and abs(float(s_["dir"] @ td[j])) < 0.5:
            stop.append(s_)
        else:
            lane.append(s_)
    return lane, stop


def _join_pieces(ch):
    """Polyline of a chain of stripes; the gaps (dashes, occlusions) are bridged with a cubic Hermite curve
    through the end tangents, so a dashed line on a curve follows the curve between its dashes."""
    out = [ch[0]["pts"]]
    for a_, b_ in zip(ch[:-1], ch[1:]):
        p0, p1 = a_["p1"], b_["p0"]
        g = float(np.hypot(*(p1[:2] - p0[:2])))
        if g > 1.0:
            m0 = _end_dir(a_["pts"], True, 2.0) * g
            m1 = _end_dir(b_["pts"], False, 2.0) * g
            t = np.linspace(0, 1, int(g) + 2)[1:-1][:, None]
            h00, h10, h01, h11 = 2 * t**3 - 3 * t**2 + 1, t**3 - 2 * t**2 + t, -2 * t**3 + 3 * t**2, t**3 - t**2
            xy = h00 * p0[:2] + h10 * m0 + h01 * p1[:2] + h11 * m1
            out.append(np.column_stack([xy, p0[2] + (p1[2] - p0[2]) * t[:, 0]]))
        out.append(b_["pts"])
    return np.vstack(out)


def _end_dir(pts, forward, span=3.0):
    """Unit tangent at the last (forward) or first end of a polyline, over the last `span` metres."""
    q = pts[:, :2] if forward else pts[::-1, :2]
    seg = np.hypot(*np.diff(q, axis=0).T)
    acc = np.cumsum(seg[::-1])
    k = int(np.searchsorted(acc, span)) + 1
    v = q[-1] - q[max(0, len(q) - 1 - k)]
    v = v if forward else -v
    return v / max(float(np.hypot(*v)), 1e-9)


def _end_curvature(chain, forward, span=15.0, min_span=6.0):
    """Signed curvature of the last `span` m of a chain (circle through 3 points along it), None when the chain
    is shorter than min_span. Positive = turning left when walking out of that end."""
    P = np.vstack([s_["pts"][:, :2] for s_ in chain])
    if not forward:
        P = P[::-1]
    seg = np.hypot(*np.diff(P, axis=0).T)
    acc = np.r_[0.0, np.cumsum(seg[::-1])][::-1]             # distance from the end
    if acc[0] < min_span:
        return None
    L = min(span, float(acc[0]))
    pick = [int(np.argmin(np.abs(acc - a))) for a in (L, L / 2, 0.0)]
    a, b, c = P[pick[0]], P[pick[1]], P[pick[2]]
    ab, bc, ca = np.hypot(*(b - a)), np.hypot(*(c - b)), np.hypot(*(a - c))
    if min(ab, bc) < 1.0:
        return None
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return float(2.0 * cross / (ab * bc * ca))


def _flip(s_):
    return {**s_, "pts": s_["pts"][::-1], "p0": s_["p1"], "p1": s_["p0"], "dir": -s_["dir"]}


def _chain(stripes, max_gap, max_angle, max_offset):
    """Greedy chaining of stripes into lines (each stripe used once), longest stripes first. The next stripe
    must continue the END of the chain (curved lines): direction within max_angle (+ the turn over the gap),
    gap ≤ max_gap, lateral
    offset from the position predicted with the chain's own curvature ≤ max_offset (+ gap² / 600); a single
    stripe (no curvature yet) allows any radius ≥ 50 m (offset + gap² / 100)."""
    if not stripes:
        return []
    used = np.zeros(len(stripes), bool)
    order = np.argsort([-s["len"] for s in stripes])
    ends = np.array([s["p0"][:2] for s in stripes] + [s["p1"][:2] for s in stripes])
    from scipy.spatial import cKDTree
    tree = cKDTree(ends)
    ns = len(stripes)
    chains = []

    def extend(chain, forward):
        while True:
            end = chain[-1] if forward else chain[0]
            d = _end_dir(end["pts"], True, 2.0) if forward else -_end_dir(end["pts"], False, 2.0)
            tip = end["p1"][:2] if forward else end["p0"][:2]
            kap = _end_curvature(chain, forward)          # signed curvature (1/m, + = to the left of d) or None
            nrm = np.array([-d[1], d[0]])
            best, best_cost = None, np.inf
            for jj in set(j % ns for j in tree.query_ball_point(tip, max_gap + 0.5)):
                if used[jj]:
                    continue
                s_ = stripes[jj]
                # orient the candidate so that it continues the chain
                if forward:
                    if np.hypot(*(s_["p1"][:2] - tip)) < np.hypot(*(s_["p0"][:2] - tip)):
                        s_ = _flip(s_)
                    near = s_["p0"][:2]
                    dj = _end_dir(s_["pts"], False, 2.0)
                else:
                    if np.hypot(*(s_["p0"][:2] - tip)) < np.hypot(*(s_["p1"][:2] - tip)):
                        s_ = _flip(s_)
                    near = s_["p1"][:2]
                    dj = -_end_dir(s_["pts"], True, 2.0)
                # dj: direction of the candidate at its near end, pointing away from the chain (like d); on a curve
                # the direction turns by up to gap / 50 m radians across the gap
                w0, w1 = max(end["width"], 0.1), max(s_["width"], 0.1)
                if max(w0, w1) > 2.5 * min(w0, w1):           # a thin lane line does not run into a stop line
                    continue
                v = near - tip
                along = float(v @ d)
                a_ = max(along, 0.0)
                if kap is None:                                # direction unknown beyond the stripe: 50 m radius
                    turn, pred, slack = a_ / 50.0, 0.0, a_ * a_ / 100.0
                else:                                          # follow the curvature of the chain
                    turn, pred, slack = abs(kap) * a_ + a_ / 300.0, kap * a_ * a_ / 2.0, a_ * a_ / 600.0
                if float(d @ dj) < math.cos(min(math.radians(max_angle) + turn, math.pi / 3)):
                    continue
                lat = abs(float(v @ nrm) - pred)
                if along < -0.5 or along > max_gap or lat > max_offset + slack:
                    continue
                cost = along + 10 * lat
                if cost < best_cost:
                    best, best_cost = (jj, s_), cost
            if best is None:
                return
            used[best[0]] = True
            if forward:
                chain.append(best[1])
            else:
                chain.insert(0, best[1])

    for i in order:
        if used[i]:
            continue
        used[i] = True
        ch = [stripes[i]]
        extend(ch, True)
        extend(ch, False)
        chains.append(ch)
    return chains


def extract_poles(xyz, idx, ground_idx, cell=0.25, min_height=2.5, max_diameter=0.6, min_hag=0.3, progress=None):
    """Pole-like objects: thin vertical columns of points above the ground.
    Points with height above ground ≥ min_hag are gridded at `cell`; connected footprints no wider than
    max_diameter whose points reach min_height above ground (continuously filled to ≥ 60 %) are poles.
    Returns list of dict(xy, z_ground, height, idx)."""
    from core.classify import height_above_ground
    from core.surface import thin_grid
    if len(ground_idx) < 10:
        raise ValueError("Pole extraction needs ground points (class 2) — classify the ground first")
    g = thin_grid(xyz, np.asarray(ground_idx), 0.5, "min")          # lowest ground point per 0.5 m cell
    hag = height_above_ground(xyz, idx, g, cell=0.5)
    keep = np.isfinite(hag) & (hag >= min_hag)
    I = idx[keep]
    H = hag[keep]
    if progress:
        progress(0.3)
    if len(I) < 5:
        return []
    P = xyz[I]
    ix = np.floor(P[:, 0] / cell).astype(np.int64)
    iy = np.floor(P[:, 1] / cell).astype(np.int64)
    lab, n = _components(ix, iy)
    o = np.argsort(lab, kind="stable")
    ls = lab[o]
    st = np.flatnonzero(np.r_[True, ls[1:] != ls[:-1]])
    en = np.r_[st[1:], len(ls)]
    out = []
    for a, b in zip(st, en):
        sel = o[a:b]
        if len(sel) < 10:
            continue
        q = P[sel]
        ext = q[:, :2].max(axis=0) - q[:, :2].min(axis=0)
        if ext.max() > max_diameter + cell:
            continue
        h = H[sel]
        if h.max() < min_height:
            continue
        bins = np.unique(np.floor(h / 0.25).astype(int))
        fill = len(bins) / max(1, int(np.ceil(h.max() / 0.25)))
        if fill < 0.6:
            continue
        xy = q[:, :2].mean(axis=0)
        zg = float(np.median(q[:, 2] - h))
        out.append({"xy": xy, "z_ground": zg, "height": float(h.max()), "idx": I[sel]})
    if progress:
        progress(1.0)
    return out
