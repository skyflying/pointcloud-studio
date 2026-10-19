"""Camera model, projection, triangulation, similarity transforms and bundle adjustment.

Camera model (one per physical camera / image size): pinhole with focal length f, principal point
(cx, cy) and radial distortion k1, k2 —  x = R·X + t;  u = f·(1 + k1 r² + k2 r⁴)·x/z + c.
Poses are world → camera (R, t); the camera centre is C = −Rᵀt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Intrinsics:
    f: float
    cx: float
    cy: float
    k1: float = 0.0
    k2: float = 0.0
    width: int = 0
    height: int = 0
    fixed: bool = False                     # calibrated (stereo rig / user values): not refined
    meta: dict = field(default_factory=dict)

    def K(self):
        return np.array([[self.f, 0, self.cx], [0, self.f, self.cy], [0, 0, 1.0]])

    def dist(self):
        return np.array([self.k1, self.k2, 0.0, 0.0, 0.0])

    def scaled(self, s):
        return Intrinsics(self.f * s, self.cx * s, self.cy * s, self.k1, self.k2, int(round(self.width * s)),
                          int(round(self.height * s)), self.fixed, dict(self.meta))


def rodrigues_rotate(points, rvecs):
    """Rotate points (n, 3) by axis-angle vectors (n, 3)."""
    theta = np.linalg.norm(rvecs, axis=1)[:, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        v = np.where(theta > 1e-12, rvecs / theta, 0.0)
    dot = np.sum(points * v, axis=1)[:, None]
    c, s = np.cos(theta), np.sin(theta)
    return c * points + s * np.cross(v, points) + dot * (1 - c) * v


def rvec_to_R(r):
    import cv2
    return cv2.Rodrigues(np.asarray(r, float).reshape(3, 1))[0]


def R_to_rvec(R):
    import cv2
    return cv2.Rodrigues(np.asarray(R, float))[0].ravel()


def project(X, rvec, t, intr):
    """Project world points with one camera."""
    R = rvec_to_R(rvec)
    Xc = X @ R.T + t
    x = Xc[:, :2] / Xc[:, 2:3]
    r2 = (x * x).sum(axis=1, keepdims=True)
    d = 1 + intr.k1 * r2 + intr.k2 * r2 * r2
    return intr.f * d * x + [intr.cx, intr.cy], Xc[:, 2]


def undistort_normalized(uv, intr):
    """Pixel → normalised image coordinates (distortion removed)."""
    import cv2
    if len(uv) == 0:
        return np.empty((0, 2))
    p = cv2.undistortPoints(np.asarray(uv, np.float64).reshape(-1, 1, 2), intr.K(), intr.dist())
    return p.reshape(-1, 2)


def triangulate_pairs(x1, x2, R1, t1, R2, t2):
    """Linear triangulation from normalised coordinates of two views. Returns (n, 3)."""
    import cv2
    P1 = np.hstack([R1, t1.reshape(3, 1)])
    P2 = np.hstack([R2, t2.reshape(3, 1)])
    Xh = cv2.triangulatePoints(P1, P2, x1.T.astype(np.float64), x2.T.astype(np.float64))
    return (Xh[:3] / Xh[3]).T


def triangulation_angle(X, C1, C2):
    a = X - C1
    b = X - C2
    cosang = (a * b).sum(1) / np.maximum(np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1), 1e-30)
    return np.degrees(np.arccos(np.clip(cosang, -1, 1)))


def umeyama(src, dst, with_scale=True):
    """Similarity dst ≈ s·R·src + t (least squares). Returns s, R, t."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    a, b = src - mu_s, dst - mu_d
    cov = b.T @ a / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var = (a ** 2).sum() / len(src)
    s = float(np.trace(np.diag(D) @ S) / var) if with_scale and var > 0 else 1.0
    t = mu_d - s * R @ mu_s
    return s, R, t


def umeyama_ransac(src, dst, thr, with_scale=True, iters=500, seed=0):
    """Robust similarity. thr in destination units. Returns s, R, t, inlier mask."""
    rng = np.random.default_rng(seed)
    n = len(src)
    if n < 3:
        raise ValueError("At least 3 positions are needed")
    best = None
    for _ in range(iters if n > 3 else 1):
        k = rng.choice(n, 3, replace=False) if n > 3 else np.arange(3)
        try:
            s, R, t = umeyama(src[k], dst[k], with_scale)
        except np.linalg.LinAlgError:
            continue
        err = np.linalg.norm((s * src @ R.T + t) - dst, axis=1)
        inl = err < thr
        if best is None or inl.sum() > best[3].sum() or (inl.sum() == best[3].sum() and err[inl].sum() < best[4]):
            best = (s, R, t, inl, err[inl].sum())
    s, R, t, inl, _ = best
    if inl.sum() >= 3:
        s, R, t = umeyama(src[inl], dst[inl], with_scale)
        err = np.linalg.norm((s * src @ R.T + t) - dst, axis=1)
        inl = err < thr
    return s, R, t, inl


def degenerate_layout(P, ratio=0.02):
    """True if positions are (almost) on a line — a similarity cannot fix the rotation about it."""
    q = np.asarray(P, float) - np.mean(P, axis=0)
    sv = np.linalg.svd(q, compute_uv=False)
    return len(P) < 3 or sv[0] <= 0 or sv[1] / sv[0] < ratio


# ---------------------------------------------------------------- bundle adjustment
def _skew_rows(Y):
    """−[Y]× for many vectors: (n, 3, 3) — derivative of exp(ω)·Y with respect to ω at ω = 0."""
    z = np.zeros(len(Y))
    return -np.stack([np.stack([z, -Y[:, 2], Y[:, 1]], 1), np.stack([Y[:, 2], z, -Y[:, 0]], 1),
                      np.stack([-Y[:, 1], Y[:, 0], z], 1)], 1)


def bundle_adjust(cams, intr_list, cam_intr, points, obs_cam, obs_pt, obs_uv, fix_cams=(), fix_tcomp=None,
                  refine_intrinsics=False, loss_px=2.0, max_nfev=60, fix_points=None, f_prior=None,
                  rounds=3):
    """Joint refinement of camera poses, 3D points and (optionally) f, k1, k2 of each camera model.

    cams (nc, 6) rvec|t · intr_list [Intrinsics] · cam_intr (nc,) index into intr_list · points (np, 3)
    obs_* (no,) observations · fix_cams: camera indices kept fixed · fix_tcomp (cam, comp): one translation
    component kept fixed (scale gauge) · fix_points: boolean mask of points kept fixed (GCPs) ·
    f_prior: per camera model (f0, relative sigma) or None — a weak prior on the focal length
    Analytic Jacobian; rotations are updated as exp(ω)·R0 and re-linearised `rounds` times.
    Returns cams, intr_list, points, per-observation residual (px)."""
    import cv2
    from scipy.optimize import least_squares
    from scipy.sparse import coo_matrix

    nc, npt, no = len(cams), len(points), len(obs_cam)
    ni = len(intr_list)
    refine = [refine_intrinsics and not intr_list[i].fixed for i in range(ni)]
    intr0 = np.array([[it.f, it.k1, it.k2] for it in intr_list], float)
    pp = np.array([[it.cx, it.cy] for it in intr_list], float)
    R0 = np.stack([cv2.Rodrigues(np.asarray(c[:3], float).reshape(3, 1))[0] for c in cams]) if nc else \
        np.zeros((0, 3, 3))
    T0 = np.asarray(cams[:, 3:], float).copy()

    cam_free = np.ones((nc, 6), bool)
    for c in fix_cams:
        cam_free[c] = False
    if fix_tcomp is not None:
        cam_free[fix_tcomp[0], 3 + fix_tcomp[1]] = False
    pt_free = np.ones(npt, bool) if fix_points is None else ~np.asarray(fix_points, bool)
    in_free = np.zeros((ni, 3), bool)
    for i in range(ni):
        if refine[i]:
            in_free[i] = True
    priors = [] if f_prior is None else [(i, p[0], p[1]) for i, p in enumerate(f_prior)
                                         if p is not None and in_free[i, 0]]

    cam_idx = -np.ones((nc, 6), np.int64)
    n = int(cam_free.sum())
    cam_idx[cam_free] = np.arange(n)
    in_idx = -np.ones((ni, 3), np.int64)
    m = int(in_free.sum())
    in_idx[in_free] = n + np.arange(m)
    n += m
    pt_idx = -np.ones((npt, 3), np.int64)
    k = int(pt_free.sum())
    pt_idx[pt_free] = n + np.arange(3 * k).reshape(k, 3)
    n += 3 * k

    oc, op = obs_cam, obs_pt
    oi = cam_intr[oc]
    w_prior = np.sqrt(max(no, 1)) * 0.5             # prior counts like a fraction of the observations

    def unpack(x):
        v = np.zeros((nc, 6))
        v[:, 3:] = T0
        v[cam_free] = x[cam_idx[cam_free]]
        I = intr0.copy()
        I[in_free] = x[in_idx[in_free]]
        P = points.copy()
        if k:
            P[pt_free] = x[pt_idx[pt_free]]
        return v, I, P

    def forward(x):
        v, I, P = unpack(x)
        Y0 = np.matmul(R0[oc], P[op][:, :, None])[:, :, 0]
        Y = rodrigues_rotate(Y0, v[oc, :3])
        Xc = Y + v[oc, 3:]
        z = Xc[:, 2]
        z = np.where(np.abs(z) < 1e-9, 1e-9, z)
        xy = Xc[:, :2] / z[:, None]
        r2 = (xy * xy).sum(axis=1)
        f, k1, k2 = I[oi, 0], I[oi, 1], I[oi, 2]
        d = 1 + k1 * r2 + k2 * r2 * r2
        uv = (f * d)[:, None] * xy + pp[oi]
        return v, I, P, Y, Xc, z, xy, r2, f, k1, k2, d, uv

    def residuals(x):
        out = forward(x)
        r = (out[-1] - obs_uv).ravel()
        if priors:
            I = out[1]
            r = np.concatenate([r, [w_prior * (I[i, 0] - f0) / (s0 * f0) for i, f0, s0 in priors]])
        return r

    # Jacobian pattern (fixed order)
    blocks = []                                      # (column index array per obs, which derivative)
    for j in range(6):
        blocks.append(("cam", j, cam_idx[oc, j]))
    for j in range(3):
        blocks.append(("int", j, in_idx[oi, j]))
    for j in range(3):
        blocks.append(("pt", j, pt_idx[op, j]))
    rows_l, cols_l, sel_l = [], [], []
    ridx = np.arange(no)
    for b, (kind, j, col) in enumerate(blocks):
        ok = col >= 0
        for dd in (0, 1):
            rows_l.append(2 * ridx[ok] + dd)
            cols_l.append(col[ok])
            sel_l.append((b, dd, ok))
    n_rows = 2 * no + len(priors)
    prow = [2 * no + q for q in range(len(priors))]
    pcol = [in_idx[i, 0] for i, _, _ in priors]
    rows = np.concatenate(rows_l + [np.asarray(prow, np.int64)])
    cols = np.concatenate(cols_l + [np.asarray(pcol, np.int64)])
    from scipy.sparse import csr_matrix
    _pat = coo_matrix((np.arange(1, len(rows) + 1, dtype=np.float64), (rows, cols)), shape=(n_rows, n)).tocsr()
    perm = _pat.data.astype(np.int64) - 1                  # csr order of the entries (no duplicates)
    indices, indptr = _pat.indices, _pat.indptr

    def jac(x):
        v, I, P, Y, Xc, z, xy, r2, f, k1, k2, d, uv = forward(x)
        x_, y_ = xy[:, 0], xy[:, 1]
        dd_ = 2 * (k1 + 2 * k2 * r2)
        Juv_xy = np.empty((no, 2, 2))
        Juv_xy[:, 0, 0] = f * (d + x_ * dd_ * x_)
        Juv_xy[:, 0, 1] = f * x_ * dd_ * y_
        Juv_xy[:, 1, 0] = f * y_ * dd_ * x_
        Juv_xy[:, 1, 1] = f * (d + y_ * dd_ * y_)
        iz = 1.0 / z
        Jxy_X = np.zeros((no, 2, 3))
        Jxy_X[:, 0, 0] = iz
        Jxy_X[:, 0, 2] = -x_ * iz
        Jxy_X[:, 1, 1] = iz
        Jxy_X[:, 1, 2] = -y_ * iz
        J = np.matmul(Juv_xy, Jxy_X)            # d uv / d Xc   (n, 2, 3)
        Jrot = np.matmul(J, _skew_rows(Y))     # d uv / d ω
        Rfull = np.stack([rvec_to_R(r) for r in v[:, :3]]) @ R0 if nc else R0
        Jpt = np.matmul(J, Rfull[oc])
        Jint = np.stack([np.stack([d * x_, f * x_ * r2, f * x_ * r2 * r2], 1),
                         np.stack([d * y_, f * y_ * r2, f * y_ * r2 * r2], 1)], 1)
        src = {"cam": np.concatenate([Jrot, J], axis=2), "int": Jint, "pt": Jpt}
        data = []
        for (b, dd, ok) in sel_l:
            kind, j, _ = blocks[b]
            data.append(src[kind][ok, dd, j])
        pdat = [w_prior / (s0 * f0) for _, f0, s0 in priors]
        vals = np.concatenate(data + [np.asarray(pdat, float)])
        return csr_matrix((vals[perm], indices, indptr), shape=(n_rows, n))

    x0 = np.concatenate([np.where(np.arange(6)[None, :] < 3, 0.0, np.c_[np.zeros((nc, 3)), T0])[cam_free],
                         intr0[in_free], points[pt_free].ravel()])
    per_round = max(int(max_nfev // max(rounds, 1)), 5)
    for _ in range(max(rounds, 1)):
        if n == 0:
            break
        sol = least_squares(residuals, x0, jac=jac, x_scale="jac", loss="huber", f_scale=loss_px,
                            method="trf", tr_solver="lsmr", max_nfev=per_round, ftol=1e-7, xtol=1e-9)
        v, I, P = unpack(sol.x)
        # re-linearise the rotations: R0 ← exp(ω)·R0, ω = 0
        R0 = np.stack([rvec_to_R(v[c, :3]) @ R0[c] for c in range(nc)]) if nc else R0
        T0 = v[:, 3:].copy()
        intr0, points = I, P
        x0 = np.concatenate([np.where(np.arange(6)[None, :] < 3, 0.0, np.c_[np.zeros((nc, 3)), T0])[cam_free],
                             intr0[in_free], points[pt_free].ravel()])
        if sol.status in (2, 3, 4) and sol.nfev < per_round:   # converged
            break
    cams_out = np.c_[np.stack([R_to_rvec(R) for R in R0]) if nc else np.zeros((0, 3)), T0]
    new_intr = [Intrinsics(intr0[i, 0], it.cx, it.cy, intr0[i, 1], intr0[i, 2], it.width, it.height, it.fixed,
                           dict(it.meta)) for i, it in enumerate(intr_list)]
    res = residuals(x0)[:2 * no].reshape(-1, 2)
    return cams_out, new_intr, points, np.linalg.norm(res, axis=1)
