"""Cross-sections along a polyline: corridor points, TIN section, DEM section."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

CHUNK = 4_000_000


@dataclass
class Section:
    name: str
    xy: np.ndarray                    # (K, 2) world coordinates of polyline vertices
    width: float = 1.0                # corridor half-width (m)
    start: float = 0.0                # chainage at the first vertex (e.g. KP offset, m)
    result: dict = field(default_factory=dict)

    @property
    def seg_len(self):
        return np.linalg.norm(np.diff(self.xy, axis=0), axis=1)

    @property
    def length(self):
        return float(self.seg_len.sum())

    def xy_at(self, chainage):
        """World XY at chainage (relative to start)."""
        cum = np.r_[0, np.cumsum(self.seg_len)]
        s = np.clip(np.asarray(chainage, float) - self.start, 0, cum[-1])
        i = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(self.xy) - 2)
        t = (s - cum[i]) / np.maximum(self.seg_len[i], 1e-12)
        return self.xy[i] + (self.xy[i + 1] - self.xy[i]) * t[..., None]


def _project(xy, P0, D, L, cum):
    """Per point: best segment → (chainage, signed offset, |offset|)."""
    best_d = np.full(len(xy), np.inf)
    ch = np.zeros(len(xy))
    off = np.zeros(len(xy))
    for k in range(len(P0)):
        v = xy - P0[k]
        proj = v @ D[k]                                      # metres along the segment
        t = np.clip(proj, 0.0, L[k])
        perp = v[:, 0] * D[k, 1] - v[:, 1] * D[k, 0]        # + = right of the line
        along = proj - t
        d = np.hypot(perp, along)
        better = d < best_d
        best_d[better] = d[better]
        ch[better] = cum[k] + t[better]
        off[better] = perp[better]
    return ch, off, best_d


def corridor_points(xyz, idx, sec):
    """Points within `width` of the polyline. Returns dict(idx, chainage, offset, z)."""
    P = sec.xy
    P0, P1 = P[:-1], P[1:]
    L = sec.seg_len
    D = (P1 - P0) / np.maximum(L, 1e-12)[:, None]
    cum = np.r_[0, np.cumsum(L)][:-1]
    lo = P.min(axis=0) - sec.width
    hi = P.max(axis=0) + sec.width
    out = {"idx": [], "chainage": [], "offset": [], "z": []}
    for s in range(0, len(idx), CHUNK):
        ci = idx[s:s + CHUNK]
        xy = xyz[ci, :2]
        m = (xy[:, 0] >= lo[0]) & (xy[:, 0] <= hi[0]) & (xy[:, 1] >= lo[1]) & (xy[:, 1] <= hi[1])
        if not m.any():
            continue
        ci, xy = ci[m], xy[m]
        ch, off, d = _project(xy, P0, D, L, cum)
        keep = d <= sec.width
        out["idx"].append(ci[keep])
        out["chainage"].append(ch[keep])
        out["offset"].append(off[keep])
        out["z"].append(xyz[ci[keep], 2])
    res = {k: (np.concatenate(v) if v else np.empty(0)) for k, v in out.items()}
    order = np.argsort(res["chainage"], kind="stable")
    return {k: v[order] for k, v in res.items()}


def _tin_segment(V, F, p0, p1):
    """Exact section of a TIN along segment p0→p1: list of (t, z) pieces with NaN gaps."""
    d = p1 - p0
    seglen = np.hypot(*d)
    if seglen < 1e-12:
        return np.empty(0), np.empty(0)
    tri = V[F]                                            # (M,3,3)
    lo = np.minimum(p0, p1)
    hi = np.maximum(p0, p1)
    tmin = tri[:, :, :2].min(axis=1)
    tmax = tri[:, :, :2].max(axis=1)
    cand = np.flatnonzero((tmax[:, 0] >= lo[0]) & (tmin[:, 0] <= hi[0]) &
                          (tmax[:, 1] >= lo[1]) & (tmin[:, 1] <= hi[1]))
    if len(cand) == 0:
        return np.empty(0), np.empty(0)
    T = tri[cand]
    ts, zs, owner = [], [], []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        A, B = T[:, a], T[:, b]
        e = B[:, :2] - A[:, :2]
        den = d[0] * e[:, 1] - d[1] * e[:, 0]
        ok = np.abs(den) > 1e-15
        den = np.where(ok, den, 1.0)
        w = A[:, :2] - p0
        t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den          # along segment
        u = (w[:, 0] * d[1] - w[:, 1] * d[0]) / den                # along edge
        hit = ok & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)
        ts.append(t[hit])
        zs.append(A[hit, 2] + u[hit] * (B[hit, 2] - A[hit, 2]))
        owner.append(np.flatnonzero(hit))
    # segment endpoints that lie inside a triangle
    for tt, p in ((0.0, p0), (1.0, p1)):
        a, b, c = T[:, 0], T[:, 1], T[:, 2]
        det = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        det = np.where(np.abs(det) > 1e-15, det, np.nan)
        l1 = ((b[:, 1] - c[:, 1]) * (p[0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (p[1] - c[:, 1])) / det
        l2 = ((c[:, 1] - a[:, 1]) * (p[0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (p[1] - c[:, 1])) / det
        l3 = 1 - l1 - l2
        inside = np.flatnonzero((l1 >= -1e-12) & (l2 >= -1e-12) & (l3 >= -1e-12))
        if len(inside):
            k = inside[0]
            ts.append(np.array([tt]))
            zs.append(np.array([l1[k] * a[k, 2] + l2[k] * b[k, 2] + l3[k] * c[k, 2]]))
            owner.append(np.array([k]))
    t = np.concatenate(ts)
    z = np.concatenate(zs)
    own = np.concatenate(owner)
    # each crossed triangle contributes the piece [min t, max t]
    order = np.lexsort((t, own))
    t, z, own = t[order], z[order], own[order]
    first = np.r_[True, own[1:] != own[:-1]]
    last = np.r_[own[1:] != own[:-1], True]
    t0, z0, t1, z1 = t[first], z[first], t[last], z[last]
    good = t1 > t0 + 1e-12
    t0, z0, t1, z1 = t0[good], z0[good], t1[good], z1[good]
    if len(t0) == 0:
        return np.empty(0), np.empty(0)
    o = np.argsort(t0)
    t0, z0, t1, z1 = t0[o], z0[o], t1[o], z1[o]
    out_t, out_z = [t0[0]], [z0[0]]
    end = t1[0]
    out_t.append(t1[0])
    out_z.append(z1[0])
    gap_tol = 1e-9
    for i in range(1, len(t0)):
        if t0[i] > end + gap_tol:                         # gap in the TIN
            out_t.append(np.nan)
            out_z.append(np.nan)
            out_t.append(t0[i])
            out_z.append(z0[i])
        out_t.append(t1[i])
        out_z.append(z1[i])
        end = max(end, t1[i])
    return np.asarray(out_t) * seglen, np.asarray(out_z)


def tin_section(tin, sec):
    chs, zs = [], []
    cum = 0.0
    for k in range(len(sec.xy) - 1):
        c, z = _tin_segment(tin.vertices, tin.triangles, sec.xy[k], sec.xy[k + 1])
        if len(c):
            if chs:
                chs.append(np.array([np.nan]))
                zs.append(np.array([np.nan]))
                if not np.isnan(zs[-2][-1]) and abs(c[0]) < 1e-9:   # continuous across the vertex
                    chs.pop(); zs.pop()
            chs.append(c + cum)
            zs.append(z)
        cum += sec.seg_len[k]
    if not chs:
        return np.empty(0), np.empty(0)
    return np.concatenate(chs), np.concatenate(zs)


def dem_section(dem, sec, step=None):
    """Bilinear DEM samples along the polyline (NaN where any neighbour cell is NoData)."""
    step = step or dem.cell / 2
    n = max(2, int(np.ceil(sec.length / step)) + 1)
    ch = np.linspace(0, sec.length, n)
    xy = sec.xy_at(ch + sec.start)
    c = (xy[:, 0] - dem.x0) / dem.cell - 0.5
    r = (dem.ytop - xy[:, 1]) / dem.cell - 0.5
    nr, nc = dem.z.shape
    c0 = np.floor(c).astype(int)
    r0 = np.floor(r).astype(int)
    fc, fr = c - c0, r - r0
    z = np.full(n, np.nan)
    ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < nc) & (r0 + 1 < nr)
    i = np.flatnonzero(ok)
    Z = dem.z
    z00 = Z[r0[i], c0[i]]
    z01 = Z[r0[i], c0[i] + 1]
    z10 = Z[r0[i] + 1, c0[i]]
    z11 = Z[r0[i] + 1, c0[i] + 1]
    z[i] = ((1 - fr[i]) * ((1 - fc[i]) * z00 + fc[i] * z01) + fr[i] * ((1 - fc[i]) * z10 + fc[i] * z11))
    return ch, z


def compute(sec, pc, tin=None, dem=None):
    idx = pc.active_indices()
    res = corridor_points(pc.xyz, idx, sec)
    res["tin"] = tin_section(tin, sec) if tin is not None else None
    res["dem"] = dem_section(dem, sec) if dem is not None else None
    sec.result = res
    return res
