"""Point cloud modelling: turn points into geometric models with parameters.

  Planes      RANSAC planes split into connected segments — walls, roofs, slabs, rock faces, quay walls,
              revetments; each with normal, dip / dip direction, area, RMS and a planar mesh of its outline
  Cylinders   RANSAC cylinders — piles, monopiles, poles, columns, pipes, tree trunks; axis, radius,
              length, tilt from vertical, RMS, cylinder mesh
  Buildings   from classified points (6 = building, 2 = ground): footprint polygon, ground and roof level,
              LOD1 block model or LOD2 roof-shape model (roof planes), area, height and volume

Every result is a Mesh (core.mesh) plus a table; buildings also give footprint polygons.
"""
from __future__ import annotations

import numpy as np

from core.mesh import Mesh, cylinder_mesh, estimate_normals, merge

PALETTE = np.array([(230, 159, 0), (86, 180, 233), (0, 158, 115), (240, 228, 66), (0, 114, 178),
                    (213, 94, 0), (204, 121, 167), (170, 170, 170), (120, 200, 80), (255, 120, 120)], np.uint8)


def _report(progress, f):
    if progress is not None:
        progress(float(min(max(f, 0.0), 1.0)))


# ---------------------------------------------------------------- geometry helpers
def plane_basis(n):
    n = np.asarray(n, float) / np.linalg.norm(n)
    u = np.cross(n, [0.0, 0.0, 1.0] if abs(n[2]) < 0.9 else [1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    return u, np.cross(n, u)


def dip_and_direction(n):
    """Dip (° from horizontal) and dip direction (° clockwise from grid north) of a plane with normal n."""
    n = np.asarray(n, float)
    if n[2] < 0:
        n = -n
    dip = float(np.degrees(np.arccos(np.clip(n[2], -1, 1))))
    ddir = float(np.degrees(np.arctan2(n[0], n[1])) % 360.0) if dip > 0.5 else 0.0
    return dip, ddir


def points_in_polygon(xy, ring):
    """Vectorised even–odd test. xy (N, 2), ring (M, 2)."""
    x, y = xy[:, 0], xy[:, 1]
    inside = np.zeros(len(xy), bool)
    R = np.asarray(ring, float)
    for (x1, y1), (x2, y2) in zip(R, np.roll(R, -1, axis=0)):
        c = ((y1 > y) != (y2 > y))
        if not c.any():
            continue
        xi = x1 + (y[c] - y1) * (x2 - x1) / (y2 - y1)
        inside[np.flatnonzero(c)[x[c] < xi]] ^= True
    return inside


def douglas_peucker(P, tol, closed=True):
    P = np.asarray(P, float)
    if len(P) < 4 or tol <= 0:
        return P
    if closed:
        # split at the two farthest vertices so both halves are open chains
        i0 = 0
        i1 = int(np.argmax(((P - P[0]) ** 2).sum(axis=1)))
        a = douglas_peucker(np.vstack([P[i0:i1 + 1]]), tol, False)
        b = douglas_peucker(np.vstack([P[i1:], P[:1]]), tol, False)
        return np.vstack([a[:-1], b[:-1]])
    keep = np.zeros(len(P), bool)
    keep[[0, -1]] = True
    stack = [(0, len(P) - 1)]
    while stack:
        s, e = stack.pop()
        if e <= s + 1:
            continue
        seg = P[e] - P[s]
        L = np.linalg.norm(seg)
        q = P[s + 1:e] - P[s]
        d = np.abs(seg[0] * q[:, 1] - seg[1] * q[:, 0]) / L if L > 0 else np.linalg.norm(q, axis=1)
        k = int(np.argmax(d))
        if d[k] > tol:
            m = s + 1 + k
            keep[m] = True
            stack += [(s, m), (m, e)]
    return P[keep]


def mask_outline(mask, x0, y0, cell):
    """Outer boundary ring of the largest region of a boolean grid (row 0 = y0, increasing y).
    Returns (N, 2) world coordinates of cell corners, counter-clockwise."""
    m = np.pad(mask.astype(bool), 1)
    nr, nc = m.shape
    edges = {}
    # horizontal edges between rows r-1 and r; vertical between columns c-1 and c (corner coordinates)
    dv = m[1:, :] != m[:-1, :]
    for r, c in zip(*np.nonzero(dv)):
        a, b = (c, r + 1), (c + 1, r + 1)
        if m[r + 1, c]:                     # inside is above (r+1): walk left→right keeps inside on the left
            a, b = a, b
        else:
            a, b = b, a
        edges.setdefault(a, []).append(b)
    dh = m[:, 1:] != m[:, :-1]
    for r, c in zip(*np.nonzero(dh)):
        a, b = (c + 1, r), (c + 1, r + 1)
        if m[r, c + 1]:                     # inside is right: walk downward (b→a) keeps inside on the left
            a, b = b, a
        edges.setdefault(a, []).append(b)
    rings = []
    while edges:
        start = next(iter(edges))
        ring = [start]
        cur = start
        while True:
            nxt_list = edges.get(cur)
            if not nxt_list:
                break
            nxt = nxt_list.pop()
            if not nxt_list:
                del edges[cur]
            if nxt == start:
                break
            ring.append(nxt)
            cur = nxt
        if len(ring) >= 4:
            rings.append(np.asarray(ring, float))
    if not rings:
        return None

    def area(R):
        return 0.5 * np.sum(R[:, 0] * np.roll(R[:, 1], -1) - np.roll(R[:, 0], -1) * R[:, 1])

    R = max(rings, key=lambda q: abs(area(q)))
    if area(R) < 0:
        R = R[::-1]
    # drop collinear corner points
    d1 = R - np.roll(R, 1, axis=0)
    d2 = np.roll(R, -1, axis=0) - R
    turn = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) > 1e-9
    R = R[turn]
    return np.column_stack([x0 + (R[:, 0] - 1) * cell, y0 + (R[:, 1] - 1) * cell])


def _label_grid(uv, cell, conn8=True):
    """Connected clusters of 2D points on a grid of `cell`. Returns labels per point (0-based)."""
    from scipy import ndimage
    lo = uv.min(axis=0)
    ij = np.floor((uv - lo) / cell).astype(np.int64)
    shape = ij.max(axis=0) + 1
    while shape[0] * shape[1] > 60_000_000:
        cell *= 1.5
        ij = np.floor((uv - lo) / cell).astype(np.int64)
        shape = ij.max(axis=0) + 1
    g = np.zeros(tuple(shape), bool)
    g[ij[:, 0], ij[:, 1]] = True
    st = np.ones((3, 3), bool) if conn8 else None
    lab, n = ndimage.label(g, structure=st)
    return lab[ij[:, 0], ij[:, 1]] - 1, n


def _planar_patch_mesh(P, n, d, spacing, color):
    """Mesh of a planar segment: 2D Delaunay of its points in plane coordinates, long edges removed,
    vertices projected exactly onto the plane."""
    from scipy.spatial import Delaunay
    u, w = plane_basis(n)
    c = P.mean(axis=0)
    q = P - c
    uv = np.column_stack([q @ u, q @ w])
    cell = spacing * 0.7
    k = np.floor((uv - uv.min(axis=0)) / cell).astype(np.int64)
    _, first = np.unique(k[:, 0] * (k[:, 1].max() + 1) + k[:, 1], return_index=True)
    uv = uv[first]
    if len(uv) < 3:
        return None
    try:
        tri = Delaunay(uv, qhull_options="Qbb Qc Qz Q12").simplices
    except Exception:  # noqa: BLE001  (collinear)
        return None
    lim = (3.0 * spacing) ** 2
    keep = np.ones(len(tri), bool)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        keep &= ((uv[tri[:, a]] - uv[tri[:, b]]) ** 2).sum(axis=1) <= lim
    tri = tri[keep]
    if len(tri) == 0:
        return None
    c_on = c - (c @ n + d) * n
    V = c_on + uv[:, :1] * u + uv[:, 1:] * w
    used = np.unique(tri)
    remap = -np.ones(len(V), np.int64)
    remap[used] = np.arange(len(used))
    return Mesh(V[used], remap[tri], np.tile(color, (len(used), 1)))


# ---------------------------------------------------------------- planes
def ransac_plane(P, thr, iters=400, rng=None, sample=30000):
    rng = rng or np.random.default_rng()
    n_pts = len(P)
    S = P if n_pts <= sample else P[rng.choice(n_pts, sample, replace=False)]
    best_n, best_d, best_c = None, None, -1
    for _ in range(max(1, iters // 100)):
        h = rng.integers(0, n_pts, (100, 3))
        a, b, c = P[h[:, 0]], P[h[:, 1]], P[h[:, 2]]
        nrm = np.cross(b - a, c - a)
        L = np.linalg.norm(nrm, axis=1)
        ok = L > 1e-12
        if not ok.any():
            continue
        nrm = nrm[ok] / L[ok, None]
        d = -(nrm * a[ok]).sum(axis=1)
        cnt = (np.abs(S @ nrm.T + d) < thr).sum(axis=0)
        k = int(np.argmax(cnt))
        if cnt[k] > best_c:
            best_c, best_n, best_d = cnt[k], nrm[k], d[k]
    if best_n is None:
        return None, None, np.empty(0, int)
    n, d = best_n, best_d
    for _ in range(3):                                          # least-squares refinement on the inliers
        inl = np.flatnonzero(np.abs(P @ n + d) < thr)
        if len(inl) < 3:
            break
        c = P[inl].mean(axis=0)
        _, _, vt = np.linalg.svd(P[inl] - c, full_matrices=False)
        n = vt[2]
        d = -n @ c
    if n[2] < 0 or (abs(n[2]) < 1e-9 and n[0] < 0):
        n, d = -n, -d
    return n, d, np.flatnonzero(np.abs(P @ n + d) < thr)


def detect_planes(xyz, idx, thr=0.05, min_points=200, max_planes=30, spacing=0.0, min_area=0.0,
                  orientation="any", iters=600, seed=0, progress=None, cancel=None, max_points=4_000_000):
    """Planar segments. orientation: 'any', 'horizontal' (≤ 15°), 'vertical' (≥ 75°), 'inclined'.
    Returns {segments: [...], mesh: Mesh, labels: array per idx (-1 = none)}."""
    from core.mesh import mean_spacing
    idx = np.asarray(idx)
    if len(idx) < 3:
        raise ValueError("Not enough points")
    rng = np.random.default_rng(seed)
    if len(idx) > max_points:                          # huge clouds: thin evenly first (keeps memory bounded)
        from core.mesh import voxel_thin
        sp0 = mean_spacing(xyz, idx)
        f = np.sqrt(len(idx) / max_points)
        idx = voxel_thin(xyz, idx, sp0 * f)
        min_points = max(10, int(min_points / f ** 2))
    if spacing <= 0:
        spacing = mean_spacing(xyz, idx) * 3.0
    c0 = xyz[idx].mean(axis=0)
    P_all = xyz[idx] - c0
    pool = np.arange(len(idx))
    labels = -np.ones(len(idx), int)
    segs, meshes = [], []
    fails = 0
    while len(segs) < max_planes and len(pool) >= min_points and fails < 6:
        if cancel is not None and cancel():
            break
        n, d, inl = ransac_plane(P_all[pool], thr, iters, rng)
        if n is None or len(inl) < min_points:
            fails += 1
            continue
        dip, ddir = dip_and_direction(n)
        ori_ok = (orientation == "any" or (orientation == "horizontal" and dip <= 15)
                  or (orientation == "vertical" and dip >= 75) or (orientation == "inclined" and 15 < dip < 75))
        gi = pool[inl]
        u, w = plane_basis(n)
        q = P_all[gi]
        lab, nl = _label_grid(np.column_stack([q @ u, q @ w]), spacing)
        accepted = False
        taken = np.zeros(len(gi), bool)
        for k in range(nl):
            sel = np.flatnonzero(lab == k)
            if len(sel) < min_points:
                continue
            taken[sel] = True
            if not ori_ok:
                continue
            pts = q[sel]
            cc = pts.mean(axis=0)
            _, _, vt = np.linalg.svd(pts - cc, full_matrices=False)
            nn = vt[2] if vt[2] @ n >= 0 else -vt[2]
            dd = -nn @ cc
            color = PALETTE[len(segs) % len(PALETTE)]
            m = _planar_patch_mesh(pts, nn, dd, spacing, color)
            area = m.area if m is not None else 0.0
            if area < min_area:
                continue
            sid = len(segs)
            labels[gi[sel]] = sid
            dip_, ddir_ = dip_and_direction(nn)
            centre = cc + c0
            segs.append({"id": sid + 1, "points": int(len(sel)), "normal": nn.tolist(),
                         "d": float(-(nn @ centre)), "centre": centre.tolist(),
                         "rms": float(np.sqrt(np.mean((pts @ nn + dd) ** 2))), "area_m2": float(area),
                         "dip_deg": dip_, "dip_dir_deg": ddir_,
                         "z_min": float(pts[:, 2].min() + c0[2]), "z_max": float(pts[:, 2].max() + c0[2])})
            if m is not None:
                m.vertices += c0
                meshes.append(m)
            accepted = True
        # remove the plane's points (also unaccepted fragments, so RANSAC moves on)
        pool = np.setdiff1d(pool, gi[taken] if taken.any() else gi, assume_unique=True)
        fails = 0 if accepted else fails + 1
        _report(progress, 1.0 - len(pool) / len(idx))
    mesh = merge(meshes, {"method": "planes", "threshold": thr}) if meshes else None
    return {"segments": segs, "mesh": mesh, "labels": labels, "indices": idx}


# ---------------------------------------------------------------- cylinders
def _circle3(a, b, c):
    """Circumcircles of triangles (vectorised, 2D). Returns centres (n, 2), radii (n,)."""
    ax, ay, bx, by, cx, cy = a[:, 0], a[:, 1], b[:, 0], b[:, 1], c[:, 0], c[:, 1]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ok = np.abs(d) > 1e-12
    d = np.where(ok, d, 1.0)
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    r = np.hypot(ax - ux, ay - uy)
    r[~ok] = np.inf
    return np.column_stack([ux, uy]), r


def _refine_cylinder(P, axis, centre, r):
    """Geometric least squares: axis direction (2 angles), point on axis (2), radius."""
    from scipy.optimize import least_squares
    a0 = axis / np.linalg.norm(axis)
    u, w = plane_basis(a0)
    c0 = centre - (centre @ a0) * a0

    def unpack(x):
        a = a0 + x[0] * u + x[1] * w
        a /= np.linalg.norm(a)
        c = c0 + x[2] * u + x[3] * w
        return a, c, x[4]

    def res(x):
        a, c, rr = unpack(x)
        q = P - c
        return np.linalg.norm(q - np.outer(q @ a, a), axis=1) - rr

    sol = least_squares(res, np.array([0, 0, 0, 0, r], float), loss="soft_l1", f_scale=max(r * 0.05, 1e-3))
    a, c, rr = unpack(sol.x)
    return a, c, abs(rr)


def detect_cylinders(xyz, idx, thr=0.03, r_min=0.05, r_max=5.0, min_points=150, max_count=20,
                     axis="vertical", max_tilt=10.0, iters=1500, seed=0, progress=None, cancel=None,
                     max_points=2_000_000):
    """Cylinders. axis 'vertical' (piles, poles, columns, trunks: circle fit in plan, tilt refined) or
    'any' (pipes, inclined members: two points + normals per hypothesis)."""
    idx = np.asarray(idx)
    rng = np.random.default_rng(seed)
    if len(idx) > max_points:                          # huge clouds: thin evenly first
        from core.mesh import mean_spacing, voxel_thin
        f = np.sqrt(len(idx) / max_points)
        idx = voxel_thin(xyz, idx, mean_spacing(xyz, idx) * f)
        min_points = max(10, int(min_points / f ** 2))
    if len(idx) < max(min_points, 10):
        raise ValueError("Not enough points")
    c0 = xyz[idx].mean(axis=0)
    P_all = xyz[idx] - c0
    nrm, _ = estimate_normals(P_all, 16, "out")
    pool = np.flatnonzero(np.abs(nrm[:, 2]) < 0.5) if axis == "vertical" else np.arange(len(idx))
    out, meshes = [], []
    labels = -np.ones(len(idx), int)
    fails = 0
    while len(out) < max_count and len(pool) >= min_points and fails < 6:
        if cancel is not None and cancel():
            break
        P = P_all[pool]
        N = nrm[pool]
        S_i = np.arange(len(P)) if len(P) <= 20000 else rng.choice(len(P), 20000, replace=False)
        best = (-1, None)
        for _ in range(max(1, iters // 250)):
            if axis == "vertical":
                h = rng.integers(0, len(P), (250, 3))
                cen, r = _circle3(P[h[:, 0], :2], P[h[:, 1], :2], P[h[:, 2], :2])
                ax_ = np.tile([0.0, 0.0, 1.0], (len(r), 1))
                cen3 = np.column_stack([cen, np.zeros(len(r))])
            else:
                h = rng.integers(0, len(P), (250, 2))
                p1, p2, n1, n2 = P[h[:, 0]], P[h[:, 1]], N[h[:, 0]], N[h[:, 1]]
                ax_ = np.cross(n1, n2)
                L = np.linalg.norm(ax_, axis=1)
                ax_ = ax_ / np.maximum(L, 1e-12)[:, None]
                # lines p + s n projected on the plane ⟂ axis: closest approach gives the centre
                m1 = n1 - (n1 * ax_).sum(1)[:, None] * ax_
                m2 = n2 - (n2 * ax_).sum(1)[:, None] * ax_
                d12 = p2 - p1
                A11, A12, A22 = (m1 * m1).sum(1), -(m1 * m2).sum(1), (m2 * m2).sum(1)
                b1, b2 = (d12 * m1).sum(1), -(d12 * m2).sum(1)
                det = A11 * A22 - A12 * A12
                ok = (L > 0.2) & (np.abs(det) > 1e-12)
                det = np.where(ok, det, 1)
                s = (b1 * A22 - A12 * b2) / det
                cen3 = p1 + s[:, None] * m1
                r = np.linalg.norm((p1 - cen3) - ((p1 - cen3) * ax_).sum(1)[:, None] * ax_, axis=1)
                r[~ok] = np.inf
            good = (r >= r_min) & (r <= r_max)
            if not good.any():
                continue
            ax_, cen3, r = ax_[good], cen3[good], r[good]
            S = P[S_i]
            SN = N[S_i]
            cnt = np.zeros(len(r), int)
            for k0 in range(0, len(r), 50):
                k1 = min(k0 + 50, len(r))
                q = S[None, :, :] - cen3[k0:k1, None, :]
                t = (q * ax_[k0:k1, None, :]).sum(2)
                dist = np.linalg.norm(q - t[..., None] * ax_[k0:k1, None, :], axis=2)
                orth = np.abs((SN[None] * ax_[k0:k1, None, :]).sum(2)) < 0.4
                cnt[k0:k1] = ((np.abs(dist - r[k0:k1, None]) < thr) & orth).sum(1)
            k = int(np.argmax(cnt))
            if cnt[k] > best[0]:
                best = (cnt[k], (ax_[k], cen3[k], r[k]))
        if best[1] is None:
            fails += 1
            continue
        a, c, r = best[1]

        def inliers(a, c, r):
            q = P - c
            t = q @ a
            return np.flatnonzero(np.abs(np.linalg.norm(q - np.outer(t, a), axis=1) - r) < thr)

        inl = inliers(a, c, r)
        if len(inl) < min_points:
            fails += 1
            pool = pool[rng.permutation(len(pool))]
            continue
        a, c, r = _refine_cylinder(P[inl], a, c, r)
        if a[2] < 0:
            a = -a
        inl = inliers(a, c, r)
        tilt = float(np.degrees(np.arccos(np.clip(abs(a[2]), -1, 1))))
        # keep the longest contiguous run along the axis (piles in a row share an axis direction)
        t = np.sort((P[inl] - c) @ a)
        gaps = np.flatnonzero(np.diff(t) > max(5 * thr, 2 * r))
        bounds = np.r_[0, gaps + 1, len(t)]
        lens = np.diff(bounds)
        j = int(np.argmax(lens))
        t0, t1 = t[bounds[j]], t[bounds[j + 1] - 1]
        tt = (P[inl] - c) @ a
        inl = inl[(tt >= t0) & (tt <= t1)]
        ok = (r_min <= r <= r_max and len(inl) >= min_points and (axis != "vertical" or tilt <= max_tilt))
        if ok:
            q = P[inl] - c
            res_ = np.linalg.norm(q - np.outer(q @ a, a), axis=1) - r
            base = c + a * t0 + c0
            k = len(out)
            out.append({"id": k + 1, "points": int(len(inl)), "radius": float(r), "diameter": float(2 * r),
                        "length": float(t1 - t0), "axis": a.tolist(), "base": base.tolist(),
                        "top": (base + a * (t1 - t0)).tolist(), "tilt_deg": tilt,
                        "rms": float(np.sqrt(np.mean(res_ ** 2)))})
            meshes.append(cylinder_mesh(base, a, r, 0.0, t1 - t0, 40, True, PALETTE[k % len(PALETTE)]))
            labels[pool[inl]] = k
            fails = 0
        else:
            fails += 1
        pool = np.delete(pool, inl)
        _report(progress, len(out) / max_count)
    mesh = merge(meshes, {"method": "cylinders"}) if meshes else None
    return {"cylinders": out, "mesh": mesh, "labels": labels, "indices": idx}


# ---------------------------------------------------------------- buildings
def _roof_z(xy, roof_xyz, roof_lab, planes):
    """Z on the roof at xy: the plane of the nearest roof point (or that point's Z)."""
    from scipy.spatial import cKDTree
    _, j = cKDTree(roof_xyz[:, :2]).query(xy, k=1)
    z = roof_xyz[j, 2].copy()
    lab = roof_lab[j]
    for k, (n, d) in enumerate(planes):
        m = lab == k
        if m.any() and abs(n[2]) > 0.2:
            z[m] = -(n[0] * xy[m, 0] + n[1] * xy[m, 1] + d) / n[2]
    return z


def building_models(xyz, cls, idx, lod=2, building_class=6, ground_class=2, cell=0.5, min_area=20.0,
                    simplify=0.75, roof_stat="median", roof_thr=0.15, progress=None, cancel=None):
    """Block (LOD1) or roof-shape (LOD2) models of the buildings in a classified cloud.
    Returns {buildings: [...], mesh: Mesh, footprints: [{"ring", "attrs"}]}."""
    from scipy import ndimage
    from scipy.spatial import Delaunay
    idx = np.asarray(idx)
    cls = np.asarray(cls)
    b_idx = idx[cls[idx] == building_class]
    g_idx = idx[cls[idx] == ground_class]
    if len(b_idx) < 10:
        raise ValueError(f"No building points (class {building_class}) — classify the cloud first "
                         "(Edit → Classify Points)")
    B = xyz[b_idx]
    G = xyz[g_idx] if len(g_idx) else None
    x0, y0 = B[:, 0].min() - 2 * cell, B[:, 1].min() - 2 * cell
    ij = np.floor((B[:, :2] - [x0, y0]) / cell).astype(np.int64)
    shape = (int(ij[:, 1].max()) + 3, int(ij[:, 0].max()) + 3)       # rows = y, cols = x
    occ = np.zeros(shape, bool)
    occ[ij[:, 1], ij[:, 0]] = True
    occ = ndimage.binary_closing(occ, np.ones((3, 3), bool), iterations=1)
    occ = ndimage.binary_fill_holes(occ)
    lab, nl = ndimage.label(occ, structure=np.ones((3, 3), bool))
    pt_lab = lab[ij[:, 1], ij[:, 0]] - 1
    gtree = None
    if G is not None and len(G):
        from scipy.spatial import cKDTree
        gtree = cKDTree(G[:, :2])
    out, meshes, foot = [], [], []
    objs = ndimage.find_objects(lab)
    for k in range(nl):
        if cancel is not None and cancel():
            break
        _report(progress, k / max(nl, 1))
        sl = objs[k]
        sub = lab[sl] == k + 1
        area_cells = float(sub.sum()) * cell * cell
        if area_cells < min_area:
            continue
        pts = B[pt_lab == k]
        if len(pts) < 10:
            continue
        ring = mask_outline(sub, x0 + sl[1].start * cell, y0 + sl[0].start * cell, cell)
        if ring is None or len(ring) < 3:
            continue
        ring = douglas_peucker(ring, max(simplify, 0.0), closed=True)
        if len(ring) < 3:
            continue
        # ground level: ground points in a ring around the footprint, else the lowest building points
        if gtree is not None:
            cxy = ring.mean(axis=0)
            rad = float(np.max(np.linalg.norm(ring - cxy, axis=1))) + 5.0
            near = gtree.query_ball_point(cxy, rad)
            gz = G[near, 2] if len(near) else np.empty(0)
            ground = float(np.median(gz)) if len(gz) else float(np.percentile(pts[:, 2], 2))
        else:
            ground = float(np.percentile(pts[:, 2], 2))
        zs = pts[:, 2]
        roof = {"median": np.median(zs), "max": zs.max(), "p90": np.percentile(zs, 90)}.get(roof_stat,
                                                                                             np.median(zs))
        color = PALETTE[len(out) % len(PALETTE)]
        planes_k = []
        if lod >= 2 and len(pts) >= 60:
            res = detect_planes(pts, np.arange(len(pts)), thr=roof_thr, min_points=max(30, len(pts) // 50),
                                max_planes=12, spacing=max(cell * 2, 0.5), orientation="any", iters=400,
                                max_points=len(pts) + 1)
            planes_k = [(np.asarray(s["normal"]), s["d"]) for s in res["segments"] if s["dip_deg"] < 70]
            plab = res["labels"]
            ok_lab = np.isin(plab, [i for i, s in enumerate(res["segments"]) if s["dip_deg"] < 70])
            # relabel to the kept planes
            keep_ids = [i for i, s in enumerate(res["segments"]) if s["dip_deg"] < 70]
            rl = -np.ones(len(pts), int)
            for new, old in enumerate(keep_ids):
                rl[plab == old] = new
            rl[~ok_lab] = -1
        if lod >= 2 and planes_k:
            # roof: Delaunay of the densified outline + interior grid, Z from the roof planes
            edge_pts = []
            for a_, b_ in zip(ring, np.roll(ring, -1, axis=0)):
                L = np.linalg.norm(b_ - a_)
                m = max(int(np.ceil(L / cell)), 1)
                edge_pts.append(a_ + (b_ - a_) * (np.arange(m)[:, None] / m))
            outline = np.vstack(edge_pts)
            gx = np.arange(ring[:, 0].min() + cell / 2, ring[:, 0].max(), cell)
            gy = np.arange(ring[:, 1].min() + cell / 2, ring[:, 1].max(), cell)
            GX, GY = np.meshgrid(gx, gy)
            grid = np.column_stack([GX.ravel(), GY.ravel()])
            inside = points_in_polygon(grid, ring)
            # keep interior points not too close to the outline (avoid slivers)
            grid = grid[inside]
            if len(grid):
                from scipy.spatial import cKDTree
                dmin, _ = cKDTree(outline).query(grid, k=1)
                grid = grid[dmin > 0.5 * cell]
            XY = np.vstack([outline, grid])
            tri = Delaunay(XY).simplices
            cen = XY[tri].mean(axis=1)
            tri = tri[points_in_polygon(cen, ring)]
            e1, e2 = XY[tri[:, 1]] - XY[tri[:, 0]], XY[tri[:, 2]] - XY[tri[:, 0]]
            cw = (e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) < 0
            tri[cw] = tri[cw][:, ::-1]                                  # counter-clockwise = roof faces up
            zr = _roof_z(XY, pts, rl, planes_k)
            zr = np.maximum(zr, ground + 0.5)
            nO, nX = len(outline), len(XY)
            V = np.vstack([np.column_stack([XY, zr]), np.column_stack([XY, np.full(nX, ground)])])
            i = np.arange(nO)
            j = (i + 1) % nO
            walls = np.vstack([np.column_stack([i, nX + j, j]), np.column_stack([i, nX + i, nX + j])])
            F = np.vstack([tri, walls, tri[:, ::-1] + nX])               # roof, walls (outward), floor (down)
            m = Mesh(V, F, np.tile(color, (len(V), 1)))
            roof_top = float(np.max(zr))
            eave = float(np.median(zr[:nO]))
            kind = "LOD2"
        else:
            m = Mesh(*_prism(ring, ground, roof), None)
            m.colors = np.tile(color, (len(m.vertices), 1))
            roof_top = float(roof)
            eave = float(roof)
            kind = "LOD1"
        bid = len(out) + 1
        area = float(abs(0.5 * np.sum(ring[:, 0] * np.roll(ring[:, 1], -1) - np.roll(ring[:, 0], -1) * ring[:, 1])))
        vol = m.volume if m.is_closed else float(area * (roof - ground))
        rec = {"id": bid, "model": kind, "points": int(len(pts)), "footprint_m2": area, "ground": ground,
               "roof": float(roof), "eave": eave, "top": roof_top, "height": float(roof_top - ground),
               "roof_planes": len(planes_k), "volume_m3": float(vol), "vertices": int(len(ring)),
               "centroid": ring.mean(axis=0).tolist()}
        out.append(rec)
        meshes.append(m)
        foot.append({"ring": ring, "attrs": {k_: (round(v, 3) if isinstance(v, float) else v)
                                             for k_, v in rec.items() if k_ != "centroid"}})
    _report(progress, 1.0)
    mesh = merge(meshes, {"method": f"buildings LOD{lod}"}) if meshes else None
    return {"buildings": out, "mesh": mesh, "footprints": foot}


def _prism(ring, zb, zt):
    from core.mesh import prism_mesh
    m = prism_mesh(ring, zb, zt)
    return m.vertices, m.faces
