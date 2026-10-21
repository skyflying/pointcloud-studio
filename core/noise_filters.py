"""Further noise filters: robust local surface (MAD), isolated clusters, photogrammetry-specific tests
and camera files. Each filter returns candidate indices (a subset of idx) and an info dict."""
from __future__ import annotations

import csv

import numpy as np


def _report(progress, f):
    if progress is not None:
        progress(float(min(max(f, 0.0), 1.0)))


# ---------------------------------------------------------------- robust local surface
def local_residuals(xyz, idx, k=24, progress=None, chunk=200_000, p0=0.0, p1=1.0):
    """Signed distance of every point to the plane through its k nearest neighbours (PCA) and the robust
    spread (1.4826 · MAD) of the neighbours' distances to that plane. Planes are fitted without the point
    itself, so a spike does not pull its own plane."""
    from scipy.spatial import cKDTree
    idx = np.asarray(idx)
    P = xyz[idx]
    c0 = P.mean(axis=0)
    Q = P - c0
    tree = cKDTree(Q)
    k = int(min(max(k, 6), len(Q) - 1))
    res = np.empty(len(Q))
    sig = np.empty(len(Q))
    for a in range(0, len(Q), chunk):
        b = min(a + chunk, len(Q))
        _, nn = tree.query(Q[a:b], k=k + 1)
        nb = Q[nn[:, 1:]]
        cen = nb.mean(axis=1)
        d = nb - cen[:, None, :]
        w, v = np.linalg.eigh(np.einsum("nki,nkj->nij", d, d))
        nrm = v[:, :, 0]
        res[a:b] = ((Q[a:b] - cen) * nrm).sum(axis=1)
        rn = np.abs((d * nrm[:, None, :]).sum(axis=2))
        sig[a:b] = 1.4826 * np.median(rn, axis=1)
        _report(progress, p0 + (p1 - p0) * b / len(Q))
    return res, sig


def local_mad(xyz, idx, k=24, n_sigma=4.0, min_sigma=0.02, side="both", progress=None):
    idx = np.asarray(idx)
    res, sig = local_residuals(xyz, idx, k, progress)
    s = np.maximum(sig, min_sigma)
    # sign: normals from PCA have arbitrary direction — make "above" positive with the vertical component
    out = np.abs(res) > n_sigma * s
    if side != "both":                       # above / below: compare with the neighbours' median Z
        P = xyz[idx]
        from scipy.spatial import cKDTree
        tree = cKDTree(P[:, :2])
        _, nn = tree.query(P[:, :2], k=min(k, len(P)))
        zmed = np.median(P[nn, 2], axis=1)
        up = P[:, 2] > zmed
        out &= up if side == "shoal" else ~up
    return idx[out], {"threshold": f"> {n_sigma:g} × local robust σ (median σ {np.median(s):.3f} m, "
                                   f"k = {k})"}


# ---------------------------------------------------------------- isolated clusters
def clusters(xyz, idx, radius=0.5, min_points=50, keep_largest=True, progress=None):
    """Connected groups of points (voxels of `radius` touching in 26-neighbourhood). Groups with fewer
    than min_points (and not the largest group) are candidates — water-column noise, fish, bubbles, birds,
    floating photogrammetry blobs."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    idx = np.asarray(idx)
    P = xyz[idx]
    k = np.floor((P - P.min(axis=0)) / radius).astype(np.int64)
    dims = k.max(axis=0) + 3
    key = ((k[:, 0] + 1) * dims[1] + (k[:, 1] + 1)) * dims[2] + (k[:, 2] + 1)
    uk, inv = np.unique(key, return_inverse=True)
    inv = inv.ravel()
    _report(progress, 0.3)
    rows, cols = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if (dx, dy, dz) <= (0, 0, 0):
                    continue                                   # 13 forward offsets cover all pairs
                nk = uk + (dx * dims[1] + dy) * dims[2] + dz
                j = np.searchsorted(uk, nk)
                j = np.clip(j, 0, len(uk) - 1)
                hit = uk[j] == nk
                rows.append(np.flatnonzero(hit))
                cols.append(j[hit])
    r = np.concatenate(rows) if rows else np.empty(0, int)
    c = np.concatenate(cols) if cols else np.empty(0, int)
    G = coo_matrix((np.ones(len(r), np.int8), (r, c)), shape=(len(uk), len(uk)))
    ncomp, lab = connected_components(G, directed=False)
    _report(progress, 0.8)
    plab = lab[inv]
    size = np.bincount(plab, minlength=ncomp)
    small = size[plab] < min_points
    if keep_largest and ncomp:
        small &= plab != int(np.argmax(size))
    _report(progress, 1.0)
    return idx[small], {"threshold": f"{ncomp:,} groups at {radius:g} m; groups < {min_points} points "
                                     f"({int((size < min_points).sum()):,} groups)"}


# ---------------------------------------------------------------- cameras
def _opk_to_R(omega, phi, kappa):
    """World → camera rotation (x right, y down, z forward) from photogrammetric ω φ κ (degrees) —
    the inverse of core.sfm.pipeline.write_cameras."""
    o, p, k = np.radians([omega, phi, kappa])
    co, so, cp, sp, ck, sk = np.cos(o), np.sin(o), np.cos(p), np.sin(p), np.cos(k), np.sin(k)
    M = np.array([[cp * ck, co * sk + so * sp * ck, so * sk - co * sp * ck],
                  [-cp * sk, co * ck - so * sp * sk, so * ck + co * sp * sk],
                  [sp, -so * cp, co * cp]])
    return np.diag([1.0, -1.0, -1.0]) @ M


def read_cameras(path):
    """Camera CSV: image, X, Y, Z [, omega_deg, phi_deg, kappa_deg, f_px, …, width, height, cx, cy].
    Header names are matched case-insensitively (X/Easting, Y/Northing, Z/Height/Alt, omega/phi/kappa).
    Returns list of dicts with C (3,), and R / f / cx / cy / w / h when the orientation is given."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        txt = f.read()
    delim = max((",", ";", "\t"), key=txt.count) if any(d in txt for d in ",;\t") else None
    rows = list(csv.reader(txt.splitlines(), delimiter=delim)) if delim else [l.split() for l in txt.splitlines()]
    rows = [[c.strip() for c in r] for r in rows if r and not r[0].startswith("#")]
    head = [h.lower() for h in rows[0]]

    import re
    toks = [set(t for t in re.split(r"[^a-z0-9]+", h) if t) for h in head]

    def col(*names):
        for n in names:                                   # whole header, then any word of it ("X/East")
            for i, h in enumerate(head):
                if h == n:
                    return i
            for i, t in enumerate(toks):
                if n in t or ("_" in n and h_join(head[i]) == n):
                    return i
        return None

    def h_join(h):
        return "_".join(t for t in re.split(r"[^a-z0-9]+", h) if t)

    numeric_head = all(_isnum(h) for h in head[1:4])
    if numeric_head:
        ci = {"x": 1, "y": 2, "z": 3}
        data = rows
    else:
        # ω φ κ only: yaw / pitch / roll (Metashape, DJI) follow another convention and are NOT used
        ci = {"x": col("x", "easting", "east"), "y": col("y", "northing", "north"),
              "z": col("z", "altitude", "alt", "elevation"),
              "o": col("omega", "omega_deg"), "p": col("phi", "phi_deg"), "k": col("kappa", "kappa_deg"),
              "f": col("f_px", "focal_px"), "w": col("width"), "h": col("height_px", "image_height"),
              "cx": col("cx"), "cy": col("cy")}
        if ci["z"] is None and "height" in head:
            ci["z"] = head.index("height")
        data = rows[1:]
        if ci["x"] is None or ci["y"] is None or ci["z"] is None:
            raise ValueError("The camera file needs X, Y and Z columns")
    cams = []
    for r in data:
        try:
            cam = {"name": r[0], "C": np.array([float(r[ci["x"]]), float(r[ci["y"]]), float(r[ci["z"]])])}
        except (ValueError, IndexError, TypeError):
            continue
        if not numeric_head and ci.get("o") is not None and ci.get("p") is not None and ci.get("k") is not None:
            try:
                cam["R"] = _opk_to_R(float(r[ci["o"]]), float(r[ci["p"]]), float(r[ci["k"]]))
                for key in ("f", "w", "h", "cx", "cy"):
                    if ci.get(key) is not None and r[ci[key]] != "":
                        cam[key] = float(r[ci[key]])
            except (ValueError, IndexError):
                pass
        cams.append(cam)
    if not cams:
        raise ValueError("No camera rows found")
    return cams


def _isnum(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def cameras_from_vector(layer):
    return [{"name": f["attrs"].get("name", ""), "C": np.asarray(f["parts"][0], float).reshape(-1, 3)[0]}
            for f in layer.features if f["type"] == "Point" and np.asarray(f["parts"][0]).size >= 3]


# ---------------------------------------------------------------- photogrammetry tests
def free_space(xyz, idx, cams, bin_px=24, rel=0.05, min_cams=2, max_frac=0.5, max_cams=80, progress=None):
    """Floating points: in a camera's view, a point much nearer than the surface seen in the same image
    cell (and alone there) lies in free space the camera looked through. Flagged when that happens in
    ≥ min_cams cameras and in at least half of the cameras that see the point."""
    idx = np.asarray(idx)
    P = xyz[idx]
    hits = np.zeros(len(P), np.int32)
    seen = np.zeros(len(P), np.int32)
    usable = [c for c in cams if "R" in c and c.get("f")]
    if len(usable) > max_cams:                          # evenly spread subset: time ∝ cameras × points
        usable = [usable[i] for i in np.linspace(0, len(usable) - 1, max_cams).round().astype(int)]
    if not usable:
        raise ValueError("The cameras have no orientation (ω φ κ, focal length) — free-space test not possible")
    for ci, cam in enumerate(usable):
        R, C, f = cam["R"], cam["C"], cam["f"]
        w = cam.get("w") or 2 * (cam.get("cx") or 0) or 4000
        h = cam.get("h") or 2 * (cam.get("cy") or 0) or 3000
        cx, cy = cam.get("cx", w / 2), cam.get("cy", h / 2)
        Xc = (P - C) @ R.T
        z = Xc[:, 2]
        front = z > 1e-6
        u = np.full(len(P), -1.0)
        v = np.full(len(P), -1.0)
        u[front] = f * Xc[front, 0] / z[front] + cx
        v[front] = f * Xc[front, 1] / z[front] + cy
        vis = front & (u >= 0) & (u < w) & (v >= 0) & (v < h)
        if vis.sum() < 10:
            continue
        q = np.flatnonzero(vis)
        nbx = int(np.ceil(w / bin_px))
        cell = (v[q] // bin_px).astype(np.int64) * nbx + (u[q] // bin_px).astype(np.int64)
        o = np.argsort(cell, kind="stable")
        cs = cell[o]
        zs = z[q][o]
        st = np.flatnonzero(np.r_[True, cs[1:] != cs[:-1]])
        cnt = np.diff(np.r_[st, len(cs)])
        grp = np.repeat(np.arange(len(st)), cnt)
        # depth of the surface seen in the cell: upper quartile (floaters are nearer, so they cannot pull
        # it towards themselves unless they fill most of the cell)
        o2 = np.lexsort((zs, grp))
        far = zs[o2][st + (3 * cnt) // 4]
        near = zs < far[grp] * (1.0 - rel)
        nfrac = np.bincount(grp, weights=near.astype(float)) / cnt
        sus = near & (nfrac[grp] < max_frac) & (cnt[grp] >= 4)
        seen[q[o]] += 1
        hits[q[o][sus]] += 1
        _report(progress, (ci + 1) / len(usable))
    flag = (hits >= min_cams) & (hits * 2 >= np.maximum(seen, 1))
    return idx[flag], {"threshold": f"in front of the seen surface in ≥ {min_cams} of {len(usable)} cameras"}


def sky_coloured(rgb):
    """Sky-like colours (blue or near-white, low texture expected): boolean mask."""
    c = rgb.astype(float)
    if c.max() > 255:
        c /= 257.0
    c /= 255.0
    mx, mn = c.max(axis=1), c.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    blue = (c[:, 2] >= c[:, 0]) & (c[:, 2] >= c[:, 1]) & (mx > 0.55) & (sat < 0.65)
    white = (mx > 0.88) & (sat < 0.12)
    return blue | white


def photogrammetry(xyz, idx, attrs, cams=None, min_views=2, use_views=True, use_distance=True, k=24,
                   n_sigma=4.0, min_sigma=0.01, use_free=True, use_sky=False, progress=None):
    """Combined test for photogrammetric (SfM-MVS) clouds. Each enabled test adds candidates:
      views     fewer supporting image pairs than min_views (clouds made by this program: 'views')
      distance  residual to the local surface, scaled by (distance / median distance)² — stereo depth noise
                grows with the square of the distance, so far points are allowed more deviation
      free      floating points in front of the surface the cameras looked at (needs camera orientations)
      sky       sky-coloured points above the local surface (needs RGB)"""
    idx = np.asarray(idx)
    out = np.zeros(len(idx), bool)
    notes = []
    if use_views:
        v = attrs.get("views")
        if v is not None:
            f = np.asarray(v)[idx] < min_views
            out |= f
            notes.append(f"views < {min_views}: {int(f.sum()):,}")
        else:
            notes.append("no 'views' attribute (skipped)")
    res = sig = None
    if use_distance or use_sky:
        res, sig = local_residuals(xyz, idx, k, progress, p0=0.0, p1=0.6)
    if use_distance:
        scale = np.ones(len(idx))
        if cams:
            from scipy.spatial import cKDTree
            C = np.array([c["C"] for c in cams])
            d, _ = cKDTree(C).query(xyz[idx], k=1)
            dm = np.median(d)
            scale = np.clip((d / max(dm, 1e-9)) ** 2, 0.25, 16.0)
            notes.append(f"distance scaling from {len(C)} cameras (median {dm:.1f} m)")
        # the local spread already reflects the local noise; the floor grows with distance²
        f = np.abs(res) > n_sigma * np.maximum(sig, min_sigma * scale)
        out |= f
        notes.append(f"local residual > {n_sigma:g} σ: {int(f.sum()):,}")
    if use_free and cams:
        try:
            fi, _ = free_space(xyz, idx, cams, progress=None)
            m = np.isin(idx, fi)
            out |= m
            notes.append(f"floating (free space): {int(m.sum()):,}")
        except ValueError as e:
            notes.append(f"free space skipped: {e}")
    elif use_free:
        notes.append("free space skipped: no cameras")
    if use_sky:
        if all(k_ in attrs for k_ in ("R", "G", "B")):
            rgb = np.column_stack([np.asarray(attrs[c])[idx] for c in ("R", "G", "B")])
            f = sky_coloured(rgb) & (np.abs(res) > 0.75 * n_sigma * np.maximum(sig, min_sigma))
            out |= f
            notes.append(f"sky-coloured off-surface: {int(f.sum()):,}")
        else:
            notes.append("no RGB (sky test skipped)")
    _report(progress, 1.0)
    return idx[out], {"threshold": " · ".join(notes)}
