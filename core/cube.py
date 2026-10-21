"""CUBE-style bathymetry estimation and sounding flagging
(after Calder & Mayer 2003, "Automatic processing of high-rate, high-density multibeam echosounder data",
Geochemistry, Geophysics, Geosystems 4(6)).

Batch variant — every node is estimated from all of its soundings at once (the original CUBE assimilates
soundings one by one in time order with a Kalman update; results therefore differ in detail from CARIS /
Qimera CUBE surfaces and are NOT a certified implementation):

1. Uncertainty propagation: a sounding with vertical / horizontal standard uncertainty σv, σh contributes
   to the grid nodes within its capture distance with the variance
       σ² = σv² · (1 + ((r + s_h·σh) / ρ)^α)          r = distance to the node, ρ = node spacing
2. Multiple hypotheses: per node the contributions are ordered by depth and split where the gap between
   neighbours exceeds k_h · √(σa² + σb²); each group is a depth hypothesis (inverse-variance mean,
   variance, sample count).
3. Disambiguation: the hypothesis taken as the sea floor is chosen per node by
       density  — most supporting soundings,
       locale   — closest to the median of the neighbouring nodes' density choices,
       prior    — closest to a reference surface (TIN / DEM).
4. Flagging: a sounding is a candidate outlier when it lies more than k_f · √(σv² + σhyp²) from the
   chosen hypothesis of its own node.
Outputs: the candidate indices and grids of depth, 95 % uncertainty and hypothesis count.
"""
from __future__ import annotations

import numpy as np


class Cancelled(Exception):
    pass


def _report(progress, f):
    if progress is not None:
        progress(float(min(max(f, 0.0), 1.0)))


def _hypotheses(node, z, var):
    """Contributions (node, z, var) → hypotheses arrays and per-contribution hypothesis id."""
    o = np.lexsort((z, node))
    n_, z_, v_ = node[o], z[o], var[o]
    new_node = np.r_[True, n_[1:] != n_[:-1]]
    gap = np.r_[np.inf, np.diff(z_)]
    sig = np.r_[np.inf, np.sqrt(v_[1:] + v_[:-1])]
    return o, n_, z_, v_, new_node, gap, sig


def estimate(xyz, idx, tvu1, thu1=None, resolution=1.0, capture_pct=5.0, k_h=3.0, k_f=3.0, s_h=1.96, alpha=2.0,
             disambiguation="locale", prior_z=None, depth_positive_down=True, tile=256, locale_size=11, progress=None,
             cancel=None):
    """Run the estimator on the points idx (tvu1 / thu1: 1σ per point, aligned with idx).
    prior_z: callable (x, y) → z of a reference surface (for disambiguation='prior').
    Returns dict(candidates (point indices), node grids, summary)."""
    idx = np.asarray(idx)
    n = len(idx)
    if n == 0:
        raise ValueError("No points")
    P = xyz[idx]
    tvu1 = np.asarray(tvu1, float)
    thu1 = np.zeros(n) if thu1 is None else np.nan_to_num(np.asarray(thu1, float), nan=0.0)
    if not np.isfinite(tvu1).all():
        raise ValueError("Some points have no vertical uncertainty")
    tvu1 = np.maximum(tvu1, 1e-4)
    rho = float(resolution)
    depth = np.abs(-P[:, 2] if depth_positive_down else P[:, 2])
    # capture distance: % of depth, at least ~ the cell diagonal, at most 1.5 nodes (3 × 3 neighbourhood)
    cap = np.clip(capture_pct / 100.0 * depth, 0.75 * rho, 1.5 * rho)
    lo = np.floor(P[:, :2].min(axis=0) / rho) * rho
    ix = np.floor((P[:, 0] - lo[0]) / rho).astype(np.int64)
    iy = np.floor((P[:, 1] - lo[1]) / rho).astype(np.int64)
    nx, ny = int(ix.max()) + 1, int(iy.max()) + 1
    # nodes are stored sparsely (only occupied ones), so long corridors are fine; the node index must fit
    if float(nx) * ny > 4e18:
        raise ValueError("Extent too large for the resolution")
    # ---- tiles: each point goes to its own tile and to the neighbouring tile(s) it can contribute to
    T = max(int(tile), 8)
    ntx = (nx + T - 1) // T
    own = (iy // T) * ntx + ix // T
    extra_k, extra_p = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            k = (np.clip(iy + dy, 0, ny - 1) // T) * ntx + np.clip(ix + dx, 0, nx - 1) // T
            m = k != own                                   # only points next to a tile border
            if m.any():
                extra_k.append(k[m])
                extra_p.append(np.flatnonzero(m))
    kp = np.column_stack([own, np.arange(n)])
    if extra_k:
        ex = np.unique(np.column_stack([np.concatenate(extra_k), np.concatenate(extra_p)]), axis=0)
        kp = np.vstack([kp, ex])
    order = np.argsort(kp[:, 0], kind="stable")
    kp = kp[order]
    t_start = np.flatnonzero(np.r_[True, kp[1:, 0] != kp[:-1, 0]])
    t_end = np.r_[t_start[1:], len(kp)]
    # global hypothesis tables
    H_node, H_mean, H_var, H_n, H_spread = [], [], [], [], []
    for ti, (s, e) in enumerate(zip(t_start, t_end)):
        if cancel is not None and cancel():
            raise Cancelled()
        tkey = kp[s, 0]
        tx0, ty0 = (tkey % ntx) * T, (tkey // ntx) * T
        pts = kp[s:e, 1]
        cn, cz, cv = [], [], []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                jx, jy = ix[pts] + dx, iy[pts] + dy
                inside = (jx >= tx0) & (jx < min(tx0 + T, nx)) & (jy >= ty0) & (jy < min(ty0 + T, ny))
                if not inside.any():
                    continue
                q = pts[inside]
                cxn = lo[0] + (jx[inside] + 0.5) * rho
                cyn = lo[1] + (jy[inside] + 0.5) * rho
                r = np.hypot(P[q, 0] - cxn, P[q, 1] - cyn)
                ok = r <= cap[q]
                if not ok.any():
                    continue
                q, r = q[ok], r[ok]
                var = tvu1[q] ** 2 * (1.0 + ((r + s_h * thu1[q]) / rho) ** alpha)
                cn.append(jy[inside][ok] * nx + jx[inside][ok])
                cz.append(P[q, 2])
                cv.append(var)
        if not cn:
            continue
        node = np.concatenate(cn)
        z = np.concatenate(cz)
        var = np.concatenate(cv)
        o, n_, z_, v_, new_node, gap, sig = _hypotheses(node, z, var)
        new_h = new_node | (gap > k_h * sig)
        hid = np.cumsum(new_h) - 1
        w = 1.0 / v_
        nh = hid[-1] + 1
        sw = np.bincount(hid, weights=w, minlength=nh)
        swz = np.bincount(hid, weights=w * z_, minlength=nh)
        mean = swz / sw
        cnt = np.bincount(hid, minlength=nh)
        dev2 = np.bincount(hid, weights=w * (z_ - mean[hid]) ** 2, minlength=nh) / sw
        first = np.flatnonzero(new_h)
        H_node.append(n_[first])
        H_mean.append(mean)
        H_var.append(1.0 / sw)
        H_n.append(cnt)
        H_spread.append(dev2)
        _report(progress, 0.75 * (ti + 1) / len(t_start))
    if not H_node:
        raise ValueError("No soundings reached any node — check the resolution")
    Hn = np.concatenate(H_node)
    Hm = np.concatenate(H_mean)
    Hv = np.concatenate(H_var)
    Hc = np.concatenate(H_n)
    Hs = np.concatenate(H_spread)
    # ---- disambiguation
    o = np.lexsort((Hv, -Hc, Hn))                                   # per node: most samples first
    Hn, Hm, Hv, Hc, Hs = Hn[o], Hm[o], Hv[o], Hc[o], Hs[o]
    first = np.flatnonzero(np.r_[True, Hn[1:] != Hn[:-1]])
    n_hyp = np.diff(np.r_[first, len(Hn)])
    chosen = first.copy()                                           # density choice
    nodes = Hn[first]
    if disambiguation == "prior" and prior_z is not None:
        xn = lo[0] + (Hn % nx + 0.5) * rho
        yn = lo[1] + (Hn // nx + 0.5) * rho
        pz = np.asarray(prior_z(xn, yn), float)
        score = np.where(np.isfinite(pz), np.abs(Hm - pz) / np.sqrt(Hv + 1e-6), np.inf)
        # fall back to density where the prior is missing
        score[~np.isfinite(pz)] = 1e12 - Hc[~np.isfinite(pz)]
        grp = np.repeat(np.arange(len(first)), n_hyp)
        o2 = np.lexsort((score, grp))
        chosen = o2[np.r_[0, np.cumsum(n_hyp)[:-1]]]
    elif disambiguation == "locale":
        target_node = _neighbour_median(nodes, Hm[first], nx, ny, locale_size)
        target = target_node[np.repeat(np.arange(len(first)), n_hyp)]
        score = np.abs(Hm - target) / np.sqrt(Hv + 1e-6)
        grp = np.repeat(np.arange(len(first)), n_hyp)
        o2 = np.lexsort((score, grp))
        chosen = o2[np.r_[0, np.cumsum(n_hyp)[:-1]]]
    # sparse node tables (nodes is sorted)
    node_mean = Hm[chosen]
    node_var = np.maximum(Hv[chosen], Hs[chosen])
    _report(progress, 0.85)
    # ---- flag soundings against the chosen hypothesis of their own node
    own = iy * nx + ix
    j = np.clip(np.searchsorted(nodes, own), 0, len(nodes) - 1)
    has = nodes[j] == own
    hm = np.where(has, node_mean[j], np.nan)
    hv = np.where(has, node_var[j], np.nan)
    dev = np.abs(P[:, 2] - hm)
    lim = k_f * np.sqrt(tvu1 ** 2 + np.nan_to_num(hv, nan=0.0))
    flag = np.isfinite(hm) & (dev > lim)
    _report(progress, 1.0)
    grid = None
    if float(nx) * ny <= 40_000_000:                                # map layers only for a displayable grid
        Zg = np.full(nx * ny, np.nan, np.float32)
        Ug = np.full(nx * ny, np.nan, np.float32)
        Ng = np.zeros(nx * ny, np.float32)
        Zg[nodes] = node_mean
        Ug[nodes] = 1.96 * np.sqrt(node_var)
        Ng[nodes] = n_hyp
        grid = {"z": Zg.reshape(ny, nx)[::-1], "unc95": Ug.reshape(ny, nx)[::-1], "n_hyp": Ng.reshape(ny, nx)[::-1],
                "x0": float(lo[0]), "ytop": float(lo[1] + ny * rho), "cell": rho}       # row 0 = north
    return {"candidates": idx[flag], "deviation": dev, "limit": lim, "grid": grid,
            "summary": {"points": int(n), "flagged": int(flag.sum()), "nodes": int(len(nodes)),
                        "multi_hyp_nodes": int((n_hyp > 1).sum()), "resolution": rho,
                        "disambiguation": disambiguation, "k_h": k_h, "k_f": k_f}}


def _neighbour_median(nodes, vals, nx, ny, size, B=256):
    """Median of the values of the size × size neighbourhood of every occupied node (sparse nodes, dense
    blocks of B × B nodes with a halo — memory stays bounded for any extent)."""
    from scipy.ndimage import median_filter
    hs = size // 2
    ix, iy = nodes % nx, nodes // nx
    ntx = (nx + B - 1) // B
    tkey = (iy // B) * ntx + ix // B
    o = np.argsort(tkey, kind="stable")
    tk = tkey[o]
    st = np.flatnonzero(np.r_[True, tk[1:] != tk[:-1]])
    en = np.r_[st[1:], len(tk)]
    tiles = {int(tk[a]): o[a:b] for a, b in zip(st, en)}
    out = np.empty(len(nodes))
    for t, sel in tiles.items():
        ty, tx = divmod(t, ntx)
        x0, y0 = tx * B - hs, ty * B - hs
        parts = [sel]
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if (dx or dy) and 0 <= tx + dx < ntx and (t + dy * ntx + dx) in tiles:
                    parts.append(tiles[t + dy * ntx + dx])
        allp = np.concatenate(parts)
        cx, cy = ix[allp] - x0, iy[allp] - y0
        W = B + 2 * hs
        m = (cx >= 0) & (cy >= 0) & (cx < W) & (cy < W)
        G = np.full((W, W), np.nan)
        G[cy[m], cx[m]] = vals[allp[m]]
        # empty nodes take the value of the nearest occupied node (like edge replication), so gaps and the
        # data edge do not pull the median towards an unrelated value
        nanm = np.isnan(G)
        if nanm.any():
            from scipy.ndimage import distance_transform_edt
            _, (ri, ci) = distance_transform_edt(nanm, return_indices=True)
            G = G[ri, ci]
        M = median_filter(G, size=size, mode="nearest")
        out[sel] = M[iy[sel] - y0, ix[sel] - x0]
    return out
