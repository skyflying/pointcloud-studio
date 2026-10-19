"""Dense reconstruction: rectified image pairs → semi-global matching (SGBM) with a left–right check →
3D points → multi-view voxel fusion (a point is kept only where several pairs agree)."""
from __future__ import annotations

import numpy as np

from core.sfm.images import cv2_module, imread


def sgbm_disparity(gl, gr, min_disp, num_disp, block=5, lr_check=True, uniqueness=10):
    """Disparity of the left image (float32, NaN = invalid)."""
    cv2 = cv2_module()
    num_disp = int(max(16, int(np.ceil(num_disp / 16.0)) * 16))
    min_disp = int(np.floor(min_disp))
    cn = 1
    sg = cv2.StereoSGBM_create(minDisparity=min_disp, numDisparities=num_disp, blockSize=block,
                               P1=8 * cn * block * block, P2=32 * cn * block * block, disp12MaxDiff=-1,
                               uniquenessRatio=uniqueness, speckleWindowSize=100, speckleRange=2,
                               preFilterCap=63, mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
    dl = sg.compute(gl, gr).astype(np.float32) / 16.0
    invalid = dl < min_disp + 0.5
    if lr_check:
        # right disparity: match the mirrored images (right becomes "left")
        # right-image disparity: match the mirrored pair (mirrored right image as "left"); same range
        dr = sg.compute(np.ascontiguousarray(gr[:, ::-1]), np.ascontiguousarray(gl[:, ::-1]))
        dr = dr[:, ::-1].astype(np.float32) / 16.0
        dr[dr < min_disp + 0.5] = np.nan
        h, w = dl.shape
        xs = np.arange(w)[None, :] - np.nan_to_num(dl)
        xi = np.clip(np.round(xs).astype(int), 0, w - 1)
        drs = np.take_along_axis(dr, xi, axis=1)
        invalid |= (np.abs(dl - drs) > 1.0) | (xs < 0)
    dl[invalid] = np.nan
    return dl


def _voxel_reduce(xyz, rgb, vox, origin):
    """Mean point / colour per voxel. Returns keys, xyz, rgb, counts."""
    # keys must mean the same voxel in every call (pairs are fused by key): fixed origin, 21 bits per axis;
    # points outside that range are dropped (callers keep extent / voxel < 2 M)
    k = np.floor((xyz - origin) / vox).astype(np.int64)
    ok = ((k >= 0) & (k < (1 << 21))).all(axis=1)
    if not ok.all():
        xyz, rgb, k = xyz[ok], rgb[ok], k[ok]
    key = (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]
    u, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    P = np.column_stack([np.bincount(inv, weights=xyz[:, j]) / cnt for j in range(3)])
    C = np.column_stack([np.bincount(inv, weights=rgb[:, j].astype(float)) / cnt for j in range(3)])
    return u, P, C, cnt


_ROT90 = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])   # camera frame of a 90° CW image


def _rot_k(K, h):
    """Intrinsics of the image rotated 90° clockwise: u' = h − 1 − v, v' = u."""
    return np.array([[K[1, 1], 0, h - 1 - K[1, 2]], [0, K[0, 0], K[0, 2]], [0, 0, 1.0]])


def pair_points(img_l, img_r, K1, d1, K2, d2, R_rel, t_rel, depth_range=None, step=2, block=5,
                lr_check=True, max_disp=512):
    """Dense points of one pair in the frame of the FIRST camera. R_rel, t_rel: first → second camera
    (x2 = R_rel·x1 + t_rel). Vertical baselines are handled by rotating both images 90°, and a baseline
    pointing the "wrong" way by swapping the images, so SGBM always sees positive horizontal disparities.
    Returns xyz (n, 3), rgb (n, 3) uint8, info dict."""
    cv2 = cv2_module()
    R_rel = np.asarray(R_rel, float)
    t_rel = np.asarray(t_rel, float).ravel()
    K1, K2 = np.asarray(K1, float), np.asarray(K2, float)
    rot = abs(t_rel[1]) > abs(t_rel[0])
    if rot:
        h1, h2 = img_l.shape[0], img_r.shape[0]
        img_l, img_r = cv2.rotate(img_l, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(img_r, cv2.ROTATE_90_CLOCKWISE)
        K1, K2 = _rot_k(K1, h1), _rot_k(K2, h2)
        R_rel = _ROT90 @ R_rel @ _ROT90.T
        t_rel = _ROT90 @ t_rel
    swap = t_rel[0] > 0                         # second camera on the left of the first: swap the roles
    if swap:
        img_l, img_r, K1, K2, d1, d2 = img_r, img_l, K2, K1, d2, d1
        R_s, t_s = R_rel.T, -R_rel.T @ t_rel
    else:
        R_s, t_s = R_rel, t_rel
    h, w = img_l.shape[:2]
    if img_r.shape[:2] != (h, w):
        sx, sy = w / img_r.shape[1], h / img_r.shape[0]
        img_r = cv2.resize(img_r, (w, h))
        K2 = np.diag([sx, sy, 1.0]) @ K2
    # alpha = −1: the rectified views keep the original focal length. alpha = 0 (crop to valid pixels) zooms
    # in — ×2 typically, ×50 for converging pairs — which pushes disparities past the image width, and
    # alpha ≥ 0.5 can give a degenerate, negative focal length; −1 is stable for all pair geometries.
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(np.ascontiguousarray(K1, np.float64), np.asarray(d1, np.float64),
                                                np.ascontiguousarray(K2, np.float64), np.asarray(d2, np.float64),
                                                (w, h), np.ascontiguousarray(R_s, np.float64),
                                                np.asarray(t_s, np.float64).reshape(3, 1),
                                                flags=cv2.CALIB_ZERO_DISPARITY, alpha=-1)
    m1 = cv2.initUndistortRectifyMap(K1, d1, R1, P1, (w, h), cv2.CV_32FC1)
    m2 = cv2.initUndistortRectifyMap(K2, d2, R2, P2, (w, h), cv2.CV_32FC1)
    rl = cv2.remap(img_l, m1[0], m1[1], cv2.INTER_LINEAR)
    rr = cv2.remap(img_r, m2[0], m2[1], cv2.INTER_LINEAR)
    gl = cv2.cvtColor(rl, cv2.COLOR_BGR2GRAY) if rl.ndim == 3 else rl
    gr = cv2.cvtColor(rr, cv2.COLOR_BGR2GRAY) if rr.ndim == 3 else rr
    f = P1[0, 0]
    if not np.isfinite(P1).all() or f <= 0 or P2[0, 0] <= 0:
        raise ValueError("rectification failed for this pair (viewing directions too different)")
    B = abs(P2[0, 3] / P2[0, 0])
    if depth_range is not None:
        dmin, dmax = f * B / depth_range[1], f * B / depth_range[0]
    else:
        dmin, dmax = 0.0, min(w / 4, max_disp)
    span = dmax - dmin
    dmin, dmax = max(dmin - 0.15 * span - 2, 0.0), dmax + 0.15 * span + 2
    # a disparity search wider than max_disp (steep terrain, large images): match on a reduced copy and scale
    # the disparity map back (the disparity / width ratio — the overlap — does not change with scaling)
    s_ = min(1.0, max_disp / max(dmax - dmin, 1.0))
    if s_ < 0.999:
        s_ = max(s_, 0.125)
        gl_s = cv2.resize(gl, None, fx=s_, fy=s_, interpolation=cv2.INTER_AREA)
        gr_s = cv2.resize(gr, None, fx=s_, fy=s_, interpolation=cv2.INTER_AREA)
    else:
        s_, gl_s, gr_s = 1.0, gl, gr
    ws = gl_s.shape[1]
    lo_d, hi_d = dmin * s_, dmax * s_
    num = min(max(hi_d - lo_d, 16), max_disp, ws - lo_d - 2 * block - 16)
    if num < 16:
        raise ValueError(f"disparity range does not fit the image ({dmin:.0f}–{dmax:.0f} px at width {w}, "
                         f"scale {s_:.2f})")
    disp = sgbm_disparity(gl_s, gr_s, lo_d, num, block, lr_check)
    if s_ < 1.0:
        disp = cv2.resize(disp, (w, h), interpolation=cv2.INTER_NEAREST) / s_
    if step > 1:
        sub = np.full_like(disp, np.nan)
        sub[::step, ::step] = disp[::step, ::step]
        disp = sub
    v, u = np.nonzero(np.isfinite(disp) & (np.nan_to_num(disp) > 1e-3))
    pts = cv2.reprojectImageTo3D(np.nan_to_num(disp).astype(np.float32), Q)[v, u].astype(np.float64)
    ok = np.isfinite(pts).all(axis=1) & (pts[:, 2] > 0)
    pts, u, v = pts[ok], u[ok], v[ok]
    rgb = rl[v, u][:, ::-1] if rl.ndim == 3 else np.repeat(rl[v, u][:, None], 3, 1)
    xyz = pts @ R1                                           # rectified → (possibly swapped) first camera
    if swap:                                                 # back to the frame of the original first camera
        xyz = (xyz - t_rel) @ R_rel                          # x1 = R_relᵀ (x2 − t)
    if rot:
        xyz = xyz @ _ROT90                                   # rotated camera frame → original camera frame
    if depth_range is not None:
        z = xyz[:, 2]
        keep = (z > depth_range[0] * 0.5) & (z < depth_range[1] * 2.0)
        xyz, rgb = xyz[keep], rgb[keep]
    return xyz, rgb.astype(np.uint8), {"baseline": float(B), "f": float(f), "match_scale": float(s_),
                                       "valid": float(np.isfinite(disp[::max(step, 1), ::max(step, 1)]).mean()),
                                       "rotated": bool(rot), "swapped": bool(swap)}


def choose_neighbours(rec, n_neighbours=2, min_angle=3.0, max_angle=40.0, min_shared=30, extra=0):
    """For each registered image, the best partners (shared points, triangulation angle, sideways baseline)."""
    reg = np.flatnonzero(rec.registered)
    m = rec.valid[rec.obs_track] & rec.registered[rec.obs_img] & ~rec.obs_bad
    o = np.flatnonzero(m)
    by_img = {i: set(rec.obs_track[o[rec.obs_img[o] == i]].tolist()) for i in reg}
    C = {i: rec.centre(i) for i in reg}
    axis = {i: rec.R(i)[2] for i in reg}                      # optical axis in world coordinates
    pairs, backup = set(), set()
    for i in reg:
        cands = []
        for j in reg:
            if j == i:
                continue
            shared = by_img[i] & by_img[j]
            if len(shared) < min_shared:
                continue
            ids = np.fromiter(shared, int)
            if len(ids) > 300:
                ids = ids[:: len(ids) // 300]
            X = rec.X[ids]
            a, b = X - C[i], X - C[j]
            cosang = (a * b).sum(1) / np.maximum(np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1), 1e-30)
            ang = float(np.median(np.degrees(np.arccos(np.clip(cosang, -1, 1)))))
            if ang < min_angle or ang > max_angle:
                continue
            base = C[j] - C[i]
            bl = np.linalg.norm(base)
            fwd = abs(base @ axis[i]) / max(bl, 1e-12)            # 1 = forward motion (bad for rectification)
            if fwd > 0.85:
                continue
            # expected disparity as a fraction of the (shorter) image side: the rectified images must still
            # overlap, else SGBM has nothing to match (rectification also zooms in, hence the margin)
            Xc = X @ rec.R(i).T + rec.t(i)
            z = float(np.median(Xc[:, 2])) if len(Xc) else 0.0
            intr = rec.intr_of(i)
            frac = bl * intr.f / max(z * min(intr.width, intr.height), 1e-12) if z > 0 else 9.0
            if frac > 0.8:
                continue
            score = len(shared) * min(ang, 15.0) * (1.0 - 0.7 * fwd) * (1.0 - 0.5 * frac)
            cands.append((score, j))
        cands.sort(reverse=True)
        for k, (_, j) in enumerate(cands[:n_neighbours + extra]):
            (pairs if k < n_neighbours else backup).add((min(i, j), max(i, j)))
    backup -= pairs
    return (sorted(pairs), sorted(backup)) if extra else sorted(pairs)


def densify(rec, scale=0.5, step=2, n_neighbours=2, min_views=2, voxel=0.0, block=5, max_points=30_000_000,
            log=print, progress=None, cancel=None):
    """Dense cloud from a sparse reconstruction. scale: image size used for matching relative to the SfM
    working size. Returns xyz (n, 3), rgb (n, 3) uint8, views (n,) int."""
    pairs, backup = choose_neighbours(rec, n_neighbours, extra=2)
    if not pairs:
        pairs, backup = backup, []
    if not pairs:
        raise ValueError("No image pairs suitable for dense matching (too little overlap or forward motion only)")
    m = rec.valid[rec.obs_track] & rec.registered[rec.obs_img] & ~rec.obs_bad
    o = np.flatnonzero(m)
    sparse = rec.X[rec.valid]
    if voxel <= 0:
        # about one point per matching step at the median distance
        reg = np.flatnonzero(rec.registered)
        dist = []
        for i in reg[:: max(len(reg) // 20, 1)]:
            ids = rec.obs_track[o[rec.obs_img[o] == i]]
            if len(ids):
                Xc = rec.X[ids] @ rec.R(i).T + rec.t(i)
                dist.append(np.median(Xc[:, 2]))
        f = np.median([rec.intr_of(i).f for i in reg]) * scale
        voxel = float(np.median(dist) / f * step) if dist else 0.01
    ext = float(np.max(np.ptp(sparse, axis=0))) * 1.5 if len(sparse) else 0.0
    voxel = max(voxel, ext / 2.0e6)                         # voxel keys hold 2 M cells per axis
    origin = sparse.min(axis=0) - 0.25 * (sparse.max(axis=0) - sparse.min(axis=0)) - 10 * voxel
    log(f"Dense matching: {len(pairs)} image pairs, point spacing ≈ {voxel:.3g} (model units)")
    keys_all, P_all, C_all, W_all = [], [], [], []
    cache = {}

    def load(i):
        if i not in cache:
            if len(cache) > 6:
                cache.pop(next(iter(cache)))
            intr = rec.intr_of(i)
            img = imread(rec.paths[i], int(round(max(intr.width, intr.height) * scale)))
            cache[i] = img
        return cache[i]

    errors = []
    queue = list(pairs)
    k = -1
    while queue:
        i, j = queue.pop(0)
        k += 1
        if cancel is not None and cancel():
            break
        try:
            Ii, Ij = rec.intr_of(i), rec.intr_of(j)
            imi, imj = load(i), load(j)
            si = imi.shape[1] / Ii.width
            sj = imj.shape[1] / Ij.width
            Ki, Kj = Ii.scaled(si), Ij.scaled(sj)
            Ri, Rj = rec.R(i), rec.R(j)
            R_rel = Rj @ Ri.T
            t_rel = rec.t(j) - R_rel @ rec.t(i)
            ids = np.fromiter(set(rec.obs_track[o[rec.obs_img[o] == i]].tolist())
                              & set(rec.obs_track[o[rec.obs_img[o] == j]].tolist()), int)
            Xc = rec.X[ids] @ Ri.T + rec.t(i)
            zr = (np.percentile(Xc[:, 2], 1), np.percentile(Xc[:, 2], 99)) if len(ids) >= 10 else None
            xyz_c, rgb, info = pair_points(imi, imj, Ki.K(), Ki.dist(), Kj.K(), Kj.dist(), R_rel, t_rel,
                                           depth_range=zr, step=step, block=block)
            if len(xyz_c) == 0:
                continue
            Xw = (xyz_c - rec.t(i)) @ Ri                       # camera i → world
            if len(sparse):
                lo, hi = np.percentile(sparse, 0.5, axis=0), np.percentile(sparse, 99.5, axis=0)
                pad = 0.1 * (hi - lo)
                inb = ((Xw >= lo - pad) & (Xw <= hi + pad)).all(axis=1)
                Xw, rgb = Xw[inb], rgb[inb]
            key, P, C, cnt = _voxel_reduce(Xw, rgb, voxel, origin)
            keys_all.append(key)
            P_all.append(P * cnt[:, None])
            C_all.append(C * cnt[:, None])
            W_all.append(cnt)
            log(f"  pair {k + 1}/{len(pairs)}: {rec._name(i)} – {rec._name(j)}  {len(Xw):,} points "
                f"({info['valid'] * 100:.0f} % matched)")
        except Exception as e:  # noqa: BLE001 — one bad pair must not stop the run
            log(f"  pair {k + 1}/{len(pairs)} skipped: {type(e).__name__}: {e}")
            errors.append(f"{rec._name(i)} – {rec._name(j)}: {e}")
        if not queue and backup and len(keys_all) < max(2, len(pairs) // 3):
            log(f"  few pairs matched — trying {len(backup)} more")        # spare partners per image
            queue, backup = list(backup), []
        if progress:
            progress(min((k + 1) / max(len(pairs), 1), 1.0))
    if not keys_all:
        raise ValueError("Dense matching produced no points"
                         + (": " + "; ".join(errors[:4]) if errors else ""))
    key = np.concatenate(keys_all)
    u, inv, views = np.unique(key, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    W = np.bincount(inv, weights=np.concatenate(W_all))
    P = np.column_stack([np.bincount(inv, weights=np.concatenate(P_all)[:, j]) for j in range(3)]) / W[:, None]
    C = np.column_stack([np.bincount(inv, weights=np.concatenate(C_all)[:, j]) for j in range(3)]) / W[:, None]
    need = min(min_views, len(pairs))
    keep = views >= need
    if keep.sum() < 0.05 * len(keep) and need > 1:          # very little overlap between pairs: relax
        keep = views >= 1
        log("  few voxels seen by several pairs — keeping single-pair points")
    P, C, views = P[keep], C[keep], views[keep]
    if len(P) > max_points:
        sel = np.random.default_rng(0).choice(len(P), max_points, replace=False)
        P, C, views = P[sel], C[sel], views[sel]
    log(f"Fused dense cloud: {len(P):,} points (seen by ≥ {need} pair(s))")
    return P, np.clip(C, 0, 255).astype(np.uint8), views.astype(np.int32), voxel
