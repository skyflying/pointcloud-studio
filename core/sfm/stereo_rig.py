"""Stereo camera rig: calibration (chessboard, file or manual) and metric dense points from synchronised pairs.

Several pairs are tied together by structure from motion on the left images; the rig baseline gives
the metric scale (median ratio of stereo depth to SfM depth of the same features)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np

from core.sfm.images import cv2_module, imread


@dataclass
class RigCalibration:
    K1: np.ndarray
    d1: np.ndarray
    K2: np.ndarray
    d2: np.ndarray
    R: np.ndarray                     # left camera → right camera
    T: np.ndarray                     # (3,) metres; right camera x_r = R·x_l + T
    size: tuple                       # (width, height) the calibration refers to
    rms: float = float("nan")
    meta: dict = field(default_factory=dict)

    @property
    def baseline(self):
        return float(np.linalg.norm(self.T))

    def scaled_to(self, w, h):
        sx, sy = w / self.size[0], h / self.size[1]
        S = np.diag([sx, sy, 1.0])
        return RigCalibration(S @ self.K1, self.d1, S @ self.K2, self.d2, self.R, self.T, (w, h), self.rms,
                              dict(self.meta))

    def to_dict(self):
        return {"K1": self.K1.tolist(), "D1": np.ravel(self.d1).tolist(), "K2": self.K2.tolist(),
                "D2": np.ravel(self.d2).tolist(), "R": self.R.tolist(), "T": np.ravel(self.T).tolist(),
                "size": list(self.size), "rms": self.rms}

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)


def manual_calibration(f_px, cx, cy, baseline_m, width, height):
    K = np.array([[f_px, 0, cx], [0, f_px, cy], [0, 0, 1.0]])
    return RigCalibration(K, np.zeros(5), K.copy(), np.zeros(5), np.eye(3), np.array([-baseline_m, 0, 0]),
                          (int(width), int(height)), meta={"source": "manual"})


def load_calibration(path):
    """JSON written by this program, or an OpenCV FileStorage file (.yml / .yaml / .xml / .json) with
    K1/M1, D1, K2/M2, D2, R, T (and optionally image_size / width + height)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if "K1" in d:
                return RigCalibration(np.array(d["K1"], float), np.array(d["D1"], float), np.array(d["K2"], float),
                                      np.array(d["D2"], float), np.array(d["R"], float),
                                      np.array(d["T"], float).ravel(), tuple(d["size"]), d.get("rms", np.nan),
                                      {"source": os.path.basename(path)})
        except (ValueError, KeyError):
            pass
    cv2 = cv2_module()
    fs = cv2.FileStorage(path, cv2.FILE_STORAGE_READ)
    if not fs.isOpened():
        raise ValueError("Cannot read the calibration file")

    def mat(*names):
        for n in names:
            node = fs.getNode(n)
            if not node.empty():
                m = node.mat()
                if m is not None:
                    return np.asarray(m, float)
        return None

    K1, K2 = mat("K1", "M1", "cameraMatrix1", "camera_matrix_left"), mat("K2", "M2", "cameraMatrix2",
                                                                         "camera_matrix_right")
    d1, d2 = mat("D1", "distCoeffs1", "dist_left"), mat("D2", "distCoeffs2", "dist_right")
    R, T = mat("R"), mat("T")
    size = None
    for n in ("image_size", "imageSize", "size"):
        node = fs.getNode(n)
        if not node.empty():
            v = node.mat()
            if v is not None:
                size = tuple(int(x) for x in np.ravel(v)[:2])
    if size is None:
        w, h = fs.getNode("width"), fs.getNode("height")
        if not w.empty() and not h.empty():
            size = (int(w.real()), int(h.real()))
    fs.release()
    if K1 is None or K2 is None or R is None or T is None:
        raise ValueError("The file needs K1, K2, R and T (and D1, D2)")
    if size is None:
        size = (int(round(K1[0, 2] * 2)), int(round(K1[1, 2] * 2)))
    return RigCalibration(K1, np.zeros(5) if d1 is None else d1.ravel(), K2, np.zeros(5) if d2 is None else d2.ravel(),
                          R, T.ravel(), size, meta={"source": os.path.basename(path)})


def calibrate_chessboard(left, right, cols, rows, square_m, log=print, progress=None):
    """Chessboard pairs → stereo calibration. cols × rows = inner corners."""
    cv2 = cv2_module()
    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square_m
    O, L, R_ = [], [], []
    size = None
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-4)
    for k, (pl, pr) in enumerate(zip(left, right)):
        gl, gr = imread(pl, gray=True), imread(pr, gray=True)
        size = (gl.shape[1], gl.shape[0])
        okl, cl = cv2.findChessboardCorners(gl, (cols, rows), cv2.CALIB_CB_ADAPTIVE_THRESH)
        okr, cr = cv2.findChessboardCorners(gr, (cols, rows), cv2.CALIB_CB_ADAPTIVE_THRESH)
        if okl and okr:
            cl = cv2.cornerSubPix(gl, cl, (7, 7), (-1, -1), crit)
            cr = cv2.cornerSubPix(gr, cr, (7, 7), (-1, -1), crit)
            O.append(objp)
            L.append(cl)
            R_.append(cr)
        if progress:
            progress((k + 1) / len(left))
    log(f"Chessboard found in {len(O)} of {len(left)} pairs")
    if len(O) < 5:
        raise ValueError("The chessboard was found in fewer than 5 pairs — check the inner-corner count")
    _, K1, d1, _, _ = cv2.calibrateCamera(O, L, size, None, None)
    _, K2, d2, _, _ = cv2.calibrateCamera(O, R_, size, None, None)
    rms, K1, d1, K2, d2, R, T, _, _ = cv2.stereoCalibrate(O, L, R_, K1, d1, K2, d2, size,
                                                          flags=cv2.CALIB_USE_INTRINSIC_GUESS, criteria=crit)
    log(f"Stereo calibration: RMS {rms:.3f} px, baseline {np.linalg.norm(T):.4f} m")
    return RigCalibration(K1, d1.ravel(), K2, d2.ravel(), R, T.ravel(), size, float(rms),
                          {"source": "chessboard", "pairs": len(O)})


# ---------------------------------------------------------------- processing
def frame_points(cal, pl, pr, max_dim=1600, step=2, block=5, depth_range=None):
    """Metric dense points of one pair in the left camera frame (x right, y down, z forward)."""
    from core.sfm.dense import pair_points
    il, ir = imread(pl, max_dim), imread(pr, max_dim)
    c = cal.scaled_to(il.shape[1], il.shape[0])
    if ir.shape[:2] != il.shape[:2]:
        import cv2
        ir = cv2.resize(ir, (il.shape[1], il.shape[0]))
    return pair_points(il, ir, c.K1, c.d1, c.K2, c.d2, c.R, c.T, depth_range=depth_range, step=step, block=block)


ORIENTATIONS = {
    # camera frame (x right, y down, z forward) → world (X east-like, Y north-like, Z up)
    "forward": np.array([[1, 0, 0], [0, 0, 1.0], [0, -1.0, 0]]),     # looking horizontally ahead
    "down": np.array([[1, 0, 0], [0, -1.0, 0], [0, 0, -1.0]]),       # looking straight down (nadir)
    "camera": np.eye(3),                                              # raw camera frame
}


def run_rig(cal, left, right, max_dim=1600, step=2, block=5, orientation="forward", voxel=0.0,
            depth_range=None, min_views=1, sfm_kwargs=None, log=print, progress=None, cancel=None):
    """Dense metric cloud from one or many synchronised pairs. Returns dict(xyz, rgb, views, cameras, info)."""
    from core.sfm.dense import _voxel_reduce
    n = len(left)
    if n == 0 or n != len(right):
        raise ValueError("Left and right image lists must be non-empty and of equal length")
    Rw = ORIENTATIONS.get(orientation, ORIENTATIONS["forward"])
    poses = {0: (np.eye(3), np.zeros(3))}                  # camera → reference-left frame (R, C)
    scale = 1.0
    if n > 1:
        from core.sfm.pipeline import sfm_on_images
        il0 = imread(left[0], max_dim)
        c = cal.scaled_to(il0.shape[1], il0.shape[0])
        from core.sfm.geometry import Intrinsics
        intr = Intrinsics(float(c.K1[0, 0] + c.K1[1, 1]) / 2, float(c.K1[0, 2]), float(c.K1[1, 2]),
                          float(c.d1[0]) if len(c.d1) else 0.0, float(c.d1[1]) if len(c.d1) > 1 else 0.0,
                          il0.shape[1], il0.shape[0], fixed=True)
        log(f"Tying {n} stereo pairs together with structure from motion on the left images…")
        rec_ref = None
        try:
            rec = sfm_on_images(left, max_dim=max_dim, intrinsics=intr, log=log,
                                progress=(lambda f: progress(0.4 * f)) if progress else None, cancel=cancel,
                                **(sfm_kwargs or {}))
            rec_ref = rec
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "Cancelled":
                raise
            log(f"  ⚠ the pairs could not be tied together ({e}) — only the first pair is used")
        if rec_ref is not None:
            reg = np.flatnonzero(rec.registered)
            log(f"  {len(reg)} of {n} pairs registered")
            ref = reg[0]
            Rref, tref = rec.R(ref), rec.t(ref)
            poses = {}
            for i in reg:
                # pose of camera i in the frame of the reference camera: X_ref = Rref·X_w + tref
                Ri, Ci = rec.R(i), rec.centre(i)
                poses[i] = (Rref @ Ri.T, Rref @ Ci + tref)
        else:
            n = 1
    frames = sorted(poses)
    ratios = []
    chunks = []
    for k, i in enumerate(frames):
        if cancel is not None and cancel():
            break
        try:
            xyz, rgb, info = frame_points(cal, left[i], right[i], max_dim, step, block, depth_range)
        except Exception as e:  # noqa: BLE001
            log(f"  pair {i + 1} skipped: {type(e).__name__}: {e}")
            continue
        if n > 1 and rec_ref is not None:
            # metric scale: stereo depth vs. SfM depth of the same pixels
            rec = rec_ref
            tk = rec.kp2track[i]
            sel = np.flatnonzero(tk >= 0)
            sel = sel[rec.valid[tk[sel]]]
            if len(sel) >= 10 and len(xyz) >= 10:
                from scipy.spatial import cKDTree
                Xc = rec.X[tk[sel]] @ rec.R(i).T + rec.t(i)
                il = imread(left[i], max_dim)
                c = cal.scaled_to(il.shape[1], il.shape[0])
                proj = (xyz @ c.K1.T)
                uv_d = proj[:, :2] / proj[:, 2:3]
                d, j = cKDTree(uv_d).query(rec.feats[i].kp[sel] * (il.shape[1] / rec.feats[i].size[0]), k=1)
                ok = (d < 1.5 * step) & (Xc[:, 2] > 0)
                if ok.sum() >= 5:
                    ratios.append(np.median(xyz[j[ok], 2] / Xc[ok, 2]))
        chunks.append((i, xyz, rgb))
        log(f"  pair {i + 1}/{n}: {len(xyz):,} points ({info['valid'] * 100:.0f} % matched)")
        if progress:
            progress((0.4 if n > 1 else 0.0) + 0.55 * (k + 1) / len(frames))
    if not chunks:
        raise ValueError("No stereo pair produced points")
    if n > 1:
        if not ratios:
            raise ValueError("Could not relate the stereo depth to the SfM model (too few common features)")
        scale = float(np.median(ratios))
        spread = float(np.std(ratios) / scale) if len(ratios) > 1 else 0.0
        log(f"Metric scale from the rig baseline: ×{scale:.4f} (spread {spread * 100:.1f} % over {len(ratios)} pairs)")
    P, C = [], []
    cams = []
    for i, xyz, rgb in chunks:
        Rc, Cc = poses[i]
        X_ref = xyz @ Rc.T + Cc * scale                      # camera i (metric) → reference frame (metric)
        P.append(X_ref @ Rw.T)
        C.append(rgb)
        cams.append((Cc * scale) @ Rw.T)
    xyz = np.vstack(P)
    rgb = np.vstack(C)
    if voxel <= 0:
        z = np.median(np.concatenate([c[1][:, 2] for c in chunks]))
        f = cal.K1[0, 0] * (max_dim / max(cal.size) if max(cal.size) > max_dim else 1.0)
        voxel = float(z / f * step)
    voxel = max(voxel, float(np.max(np.ptp(xyz, axis=0))) / 2.0e6)
    key, Pm, Cm, cnt = _voxel_reduce(xyz, rgb, voxel, xyz.min(axis=0) - voxel)
    keep = cnt >= min_views
    if progress:
        progress(1.0)
    return {"xyz": Pm[keep], "rgb": np.clip(Cm[keep], 0, 255).astype(np.uint8), "views": cnt[keep].astype(np.int32),
            "cameras": np.array(cams), "voxel": voxel, "scale": scale, "pairs": len(chunks)}
