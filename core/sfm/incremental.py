"""Incremental structure from motion: initial pair → PnP registration → triangulation → bundle adjustment."""
from __future__ import annotations

import numpy as np

from core.sfm.geometry import (R_to_rvec, bundle_adjust, rvec_to_R, triangulation_angle,
                               undistort_normalized)


class Cancelled(Exception):
    pass


class Reconstruction:
    def __init__(self, paths, feats, intr_list, img_intr, tracks, kp2track):
        self.paths = list(paths)
        self.feats = feats
        self.intr = list(intr_list)
        self.img_intr = np.asarray(img_intr, int)
        self.tracks = tracks
        self.kp2track = kp2track
        n = len(paths)
        self.registered = np.zeros(n, bool)
        self.cams = np.zeros((n, 6))
        nt = len(tracks)
        self.X = np.zeros((nt, 3))
        self.valid = np.zeros(nt, bool)
        # flat observation table, ordered by track then image
        lens = np.array([len(t) for t in tracks], int)
        self.obs_track = np.repeat(np.arange(nt), lens)
        tt = np.vstack(tracks) if nt else np.empty((0, 2), int)
        self.obs_img = tt[:, 0].astype(np.int64)
        self.obs_kp = tt[:, 1].astype(np.int64)
        self.obs_uv = np.zeros((len(tt), 2))
        order = np.argsort(self.obs_img, kind="stable")             # one pass per image, not a mask per image
        bounds = np.searchsorted(self.obs_img[order], np.arange(len(feats) + 1))
        for i, f in enumerate(feats):
            o = order[bounds[i]:bounds[i + 1]]
            if len(o):
                self.obs_uv[o] = f.kp[self.obs_kp[o]]
        self.obs_bad = np.zeros(len(tt), bool)
        self.obs_xn = np.zeros((len(tt), 2))
        self.update_normalized()
        self.colors = np.zeros((nt, 3), np.uint8)
        first = np.r_[0, np.cumsum(lens)[:-1]] if nt else np.empty(0, int)
        for k, o in enumerate(first):
            self.colors[k] = feats[self.obs_img[o]].colors[self.obs_kp[o]]
        self.log = []

    # ---------- helpers
    def update_normalized(self):
        for i in range(len(self.intr)):
            m = self.img_intr[self.obs_img] == i
            if m.any():
                self.obs_xn[m] = undistort_normalized(self.obs_uv[m], self.intr[i])

    def _intr_arrays(self, imgs):
        """f, k1, k2, (cx, cy) per image index (vectorised)."""
        P = np.array([[it.f, it.k1, it.k2, it.cx, it.cy] for it in self.intr], float)[self.img_intr[imgs]]
        return P[:, 0], P[:, 1], P[:, 2], P[:, 3:5]

    def R(self, i):
        return rvec_to_R(self.cams[i, :3])

    def t(self, i):
        return self.cams[i, 3:]

    def centre(self, i):
        return -self.R(i).T @ self.t(i)

    def centres(self, idx=None):
        idx = np.flatnonzero(self.registered) if idx is None else idx
        return np.array([self.centre(i) for i in idx]).reshape(-1, 3)

    def intr_of(self, i):
        return self.intr[self.img_intr[i]]

    def n_points(self):
        return int(self.valid.sum())

    # ---------- triangulation
    def triangulate(self, cand_tracks, max_err, min_angle):
        """Two-view DLT (first / last registered observation) for tracks without a point."""
        cand = np.zeros(len(self.tracks), bool)
        cand[cand_tracks] = True
        cand &= ~self.valid
        m = cand[self.obs_track] & self.registered[self.obs_img] & ~self.obs_bad
        o = np.flatnonzero(m)
        if len(o) < 2:
            return 0
        tr = self.obs_track[o]
        chg = np.flatnonzero(np.r_[True, tr[1:] != tr[:-1]])
        last = np.r_[chg[1:] - 1, len(o) - 1]
        ok = last > chg
        a, b = o[chg[ok]], o[last[ok]]
        tids = self.obs_track[a]
        if len(tids) == 0:
            return 0
        ia, ib = self.obs_img[a], self.obs_img[b]
        Ra = np.stack([self.R(i) for i in ia])
        Rb = np.stack([self.R(i) for i in ib])
        ta, tb = self.cams[ia, 3:], self.cams[ib, 3:]
        Pa = np.concatenate([Ra, ta[:, :, None]], axis=2)
        Pb = np.concatenate([Rb, tb[:, :, None]], axis=2)
        xa, xb = self.obs_xn[a], self.obs_xn[b]
        A = np.stack([xa[:, 0:1] * Pa[:, 2] - Pa[:, 0], xa[:, 1:2] * Pa[:, 2] - Pa[:, 1],
                      xb[:, 0:1] * Pb[:, 2] - Pb[:, 0], xb[:, 1:2] * Pb[:, 2] - Pb[:, 1]], axis=1)
        _, _, vt = np.linalg.svd(A)
        Xh = vt[:, -1]
        good = np.abs(Xh[:, 3]) > 1e-12
        X = Xh[:, :3] / np.where(good, Xh[:, 3], 1.0)[:, None]
        za = np.einsum("nij,nj->ni", Ra, X)[:, 2] + ta[:, 2]
        zb = np.einsum("nij,nj->ni", Rb, X)[:, 2] + tb[:, 2]
        Ca = -np.einsum("nji,nj->ni", Ra, ta)
        Cb = -np.einsum("nji,nj->ni", Rb, tb)
        ang = triangulation_angle(X, Ca, Cb)
        good &= (za > 0) & (zb > 0) & (ang >= min_angle)
        # reprojection error in both views
        for o_, Rm, tm in ((a, Ra, ta), (b, Rb, tb)):
            Xc = np.einsum("nij,nj->ni", Rm, X) + tm
            xy = Xc[:, :2] / np.where(np.abs(Xc[:, 2:3]) < 1e-12, 1e-12, Xc[:, 2:3])
            f, k1, k2, c = self._intr_arrays(self.obs_img[o_])
            r2 = (xy ** 2).sum(1)
            uv = (f * (1 + k1 * r2 + k2 * r2 * r2))[:, None] * xy + c
            good &= np.linalg.norm(uv - self.obs_uv[o_], axis=1) < max_err
        self.X[tids[good]] = X[good]
        self.valid[tids[good]] = True
        return int(good.sum())

    # ---------- bundle adjustment
    def bundle(self, refine_intrinsics=False, max_points=40000, max_err=4.0, gauge=None, max_nfev=50, seed=0,
               fix_poses=False):
        reg = np.flatnonzero(self.registered)
        m = self.registered[self.obs_img] & self.valid[self.obs_track] & ~self.obs_bad
        pts = np.flatnonzero(self.valid)
        if len(pts) == 0:
            return 0.0
        if len(pts) > max_points:
            rng = np.random.default_rng(seed)
            cnt = np.bincount(self.obs_track[m], minlength=len(self.tracks))[pts]
            w = cnt.astype(float) ** 2
            pts = np.sort(rng.choice(pts, max_points, replace=False, p=w / w.sum()))
        use = np.zeros(len(self.tracks), bool)
        use[pts] = True
        m &= use[self.obs_track]
        cam_map = -np.ones(len(self.paths), int)
        cam_map[reg] = np.arange(len(reg))
        pt_map = -np.ones(len(self.tracks), int)
        pt_map[pts] = np.arange(len(pts))
        o = np.flatnonzero(m)
        fix = []
        tcomp = None
        if fix_poses:
            fix = list(range(len(reg)))
        elif gauge is not None:
            c0, c1 = gauge
            if self.registered[c0]:
                fix = [cam_map[c0]]
            if c1 is not None and self.registered[c1]:
                tc = self.cams[c1, 3:]
                tcomp = (cam_map[c1], int(np.argmax(np.abs(tc))))
        used_intr = np.unique(self.img_intr[reg])
        imap = -np.ones(len(self.intr), int)
        imap[used_intr] = np.arange(len(used_intr))
        cams, intr, P, res = bundle_adjust(
            self.cams[reg].copy(), [self.intr[i] for i in used_intr], imap[self.img_intr[reg]],
            self.X[pts].copy(), cam_map[self.obs_img[o]], pt_map[self.obs_track[o]], self.obs_uv[o],
            fix_cams=fix, fix_tcomp=tcomp, refine_intrinsics=refine_intrinsics, max_nfev=max_nfev,
            f_prior=[self.intr[i].meta.get("f_prior") for i in used_intr])
        self.cams[reg] = cams
        for k, i in enumerate(used_intr):
            self.intr[i] = intr[k]
        self.X[pts] = P
        if refine_intrinsics:
            self.update_normalized()
        # points not in this adjustment: re-triangulate with the refined cameras
        others = np.flatnonzero(self.valid & ~use)
        if len(others):
            self.valid[others] = False
            self.triangulate(others, max_err * 1.5, 0.5)
        self.filter(max_err)
        return float(np.median(res)) if len(res) else 0.0

    def residuals(self):
        """Reprojection error of every active observation (NaN for inactive ones), vectorised."""
        from core.sfm.geometry import rodrigues_rotate
        m = self.registered[self.obs_img] & self.valid[self.obs_track] & ~self.obs_bad
        o = np.flatnonzero(m)
        err = np.full(len(self.obs_img), np.nan)
        if len(o) == 0:
            return err
        im = self.obs_img[o]
        Xc = rodrigues_rotate(self.X[self.obs_track[o]], self.cams[im, :3]) + self.cams[im, 3:]
        z = Xc[:, 2]
        xy = Xc[:, :2] / np.where(np.abs(z) < 1e-12, 1e-12, z)[:, None]
        f, k1, k2, c = self._intr_arrays(im)
        r2 = (xy ** 2).sum(1)
        uv = (f * (1 + k1 * r2 + k2 * r2 * r2))[:, None] * xy + c
        e = np.linalg.norm(uv - self.obs_uv[o], axis=1)
        e[z <= 0] = 1e9
        err[o] = e
        return err

    def filter(self, max_err, min_angle=0.5):
        err = self.residuals()
        bad = np.isfinite(err) & (err > max_err)
        self.obs_bad |= bad
        m = self.registered[self.obs_img] & ~self.obs_bad
        cnt = np.bincount(self.obs_track[m], minlength=len(self.tracks))
        self.valid &= cnt >= 2
        return int(bad.sum())

    def mean_error(self):
        err = self.residuals()
        e = err[np.isfinite(err) & (err < 1e8)]
        return float(np.sqrt(np.mean(e ** 2))) if len(e) else float("nan")

    # ---------- pipeline
    def initialize(self, matches, min_angle=2.0, max_err=4.0, log=print):
        import cv2
        cand = sorted(matches.items(), key=lambda kv: -len(kv[1]))[:15]
        rng = np.random.default_rng(0)
        best = None
        for (i, j), m in cand:
            if len(m) < 30:
                continue
            if len(m) > 1500:
                m = m[np.sort(rng.choice(len(m), 1500, replace=False))]
            ka, kb = self.feats[i].kp[m[:, 0]], self.feats[j].kp[m[:, 1]]
            xa = undistort_normalized(ka, self.intr_of(i))
            xb = undistort_normalized(kb, self.intr_of(j))
            f = 0.5 * (self.intr_of(i).f + self.intr_of(j).f)
            E, mask = cv2.findEssentialMat(xa, xb, np.eye(3), cv2.RANSAC, 0.999, 1.5 / f)
            if E is None or E.shape != (3, 3):
                continue
            n_in, R, t, mask2 = cv2.recoverPose(E, xa, xb, np.eye(3), mask=mask)
            inl = mask2.ravel() > 0
            if inl.sum() < 25:
                continue
            H, hm = cv2.findHomography(ka, kb, cv2.RANSAC, 3.0)
            h_ratio = (hm.sum() / max(len(m), 1)) if hm is not None else 1.0
            from core.sfm.geometry import triangulate_pairs
            X = triangulate_pairs(xa[inl], xb[inl], np.eye(3), np.zeros(3), R, t.ravel())
            ang = triangulation_angle(X, np.zeros(3), (-R.T @ t).ravel())
            med = float(np.median(ang))
            score = inl.sum() * min(1.0, med / 5.0) * (1.0 if h_ratio < 0.9 else 0.3)
            if med < 1.0:
                score *= 0.1
            if best is None or score > best[0]:
                best = (score, i, j, R, t.ravel(), med, int(inl.sum()), h_ratio)
        if best is None:
            raise ValueError("No usable initial image pair — the images overlap too little or have too few "
                             "features (try more overlap, sharper images or more features)")
        _, i, j, R, t, med, n_in, hr = best
        self.cams[i] = 0.0
        self.cams[j, :3] = R_to_rvec(R)
        self.cams[j, 3:] = t
        self.registered[[i, j]] = True
        self.gauge = (i, j)
        n = self.triangulate(np.unique(np.r_[self.kp2track[i][self.kp2track[i] >= 0],
                                             self.kp2track[j][self.kp2track[j] >= 0]]), max_err, min_angle * 0.5)
        log(f"Initial pair {self._name(i)} – {self._name(j)}: {n_in} inliers, median angle {med:.1f}°, "
            f"{n} points")
        self.bundle(False, max_err=max_err, gauge=self.gauge)
        return i, j

    def _name(self, i):
        import os
        return os.path.basename(self.paths[i])

    def register_next(self, max_err=4.0, min_inliers=15, failed=None):
        import cv2
        failed = failed if failed is not None else {}
        best = []
        for i in np.flatnonzero(~self.registered):
            tk = self.kp2track[i]
            sel = np.flatnonzero((tk >= 0))
            sel = sel[self.valid[tk[sel]]]
            if len(sel) >= min_inliers and len(sel) > failed.get(i, 0) * 1.5:
                best.append((len(sel), i, sel))
        best.sort(key=lambda b: -b[0])
        for n, i, sel in best[:5]:
            X = self.X[self.kp2track[i][sel]]
            uv = self.feats[i].kp[sel].astype(np.float64)
            intr = self.intr_of(i)
            try:
                ok, rv, tv, inl = cv2.solvePnPRansac(X, uv, intr.K(), intr.dist(), iterationsCount=3000,
                                                     reprojectionError=max_err, confidence=0.999,
                                                     flags=cv2.SOLVEPNP_EPNP)
            except cv2.error:
                ok, inl = False, None
            if not ok or inl is None or len(inl) < min_inliers or len(inl) < 0.15 * n:
                failed[i] = n
                continue
            inl = inl.ravel()
            rv, tv = cv2.solvePnPRefineLM(X[inl], uv[inl], intr.K(), intr.dist(), rv, tv)
            self.cams[i, :3], self.cams[i, 3:] = rv.ravel(), tv.ravel()
            self.registered[i] = True
            return i, len(inl), n
        return None, 0, 0

    def run(self, matches, max_err=4.0, min_angle=2.0, refine_intrinsics=True, ba_growth=1.25,
            max_ba_points=40000, log=print, progress=None, cancel=None):
        self.initialize(matches, min_angle, max_err, log)
        failed = {}
        last_ba = self.registered.sum()
        n = len(self.paths)
        while True:
            if cancel is not None and cancel():
                raise Cancelled()
            i, n_inl, n_corr = self.register_next(max_err, failed=failed)
            if i is None:
                # one more global adjustment, then a last attempt for the remaining images
                if self.registered.sum() > last_ba:
                    self.bundle(refine_intrinsics and self.registered.sum() >= 3, max_points=max_ba_points,
                                max_err=max_err, gauge=self.gauge)
                    last_ba = self.registered.sum()
                    failed = {k: v * 0.5 for k, v in failed.items()}
                    i, n_inl, n_corr = self.register_next(max_err, failed=failed)
                if i is None:
                    break
            tk = self.kp2track[i]
            new = self.triangulate(np.unique(tk[tk >= 0]), max_err, min_angle)
            nr = int(self.registered.sum())
            log(f"Registered {self._name(i)} ({nr}/{n}): {n_inl}/{n_corr} PnP inliers, +{new} points")
            if progress:
                progress(nr / n)
            if nr >= last_ba * ba_growth or nr - last_ba >= 20:
                err = self.bundle(refine_intrinsics and nr >= 3, max_points=max_ba_points, max_err=max_err,
                                  gauge=self.gauge)
                last_ba = nr
                log(f"  bundle adjustment ({nr} images, {self.n_points():,} points): median error {err:.2f} px")
        # final: all points, intrinsics (incl. distortion)
        self.bundle(refine_intrinsics, max_points=200000, max_err=max_err, gauge=self.gauge, max_nfev=100)
        self.filter(max_err)
        return self
