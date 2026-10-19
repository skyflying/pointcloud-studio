"""Feature extraction (SIFT / ORB), pair selection and geometrically verified matching."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

from core.sfm.images import cv2_module, imread


class Features:
    def __init__(self, kp, desc, colors, size, scale):
        self.kp = kp                # (n, 2) float32 pixel coordinates in the working resolution
        self.desc = desc            # (n, d) uint8
        self.colors = colors        # (n, 3) uint8 RGB
        self.size = size            # (width, height) of the working image
        self.scale = scale          # working / original


def extract(path, max_dim=2000, n_features=8000, method="sift"):
    cv2 = cv2_module()
    img = imread(path, max_dim)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if method == "orb":
        det = cv2.ORB_create(nfeatures=n_features, fastThreshold=10)
    else:
        det = cv2.SIFT_create(nfeatures=n_features, contrastThreshold=0.02)
    kps, desc = det.detectAndCompute(gray, None)
    if desc is None or len(kps) == 0:
        return Features(np.empty((0, 2), np.float32), np.empty((0, 128), np.uint8), np.empty((0, 3), np.uint8),
                        (img.shape[1], img.shape[0]), 1.0)
    kp = np.array([k.pt for k in kps], np.float32)
    if method != "orb":
        d = desc.astype(np.float32)                                     # RootSIFT
        d /= np.maximum(d.sum(axis=1, keepdims=True), 1e-12)
        d = np.sqrt(d)
        desc = np.clip(d * 512.0, 0, 255).astype(np.uint8)
    ij = np.clip(np.round(kp).astype(int), 0, [img.shape[1] - 1, img.shape[0] - 1])
    colors = img[ij[:, 1], ij[:, 0]][:, ::-1].copy()
    return Features(kp, desc, colors, (img.shape[1], img.shape[0]), 1.0)


def extract_all(paths, max_dim, n_features, method="sift", workers=4, progress=None, cancel=None):
    out = [None] * len(paths)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(extract, p, max_dim, n_features, method): i for i, p in enumerate(paths)}
        for fu in futs:
            if cancel is not None and cancel():
                break
            out[futs[fu]] = fu.result()
            done += 1
            if progress:
                progress(done / len(paths))
    return out


# ---------------------------------------------------------------- pairs
def select_pairs(n, mode="auto", window=10, gps_xy=None, gps_k=12, exhaustive_limit=60):
    """Image pairs to match. mode: exhaustive | sequential | gps | auto."""
    pairs = set()
    if mode == "auto":
        if gps_xy is not None and np.isfinite(gps_xy).all() and n > exhaustive_limit:
            mode = "gps"
        elif n <= exhaustive_limit:
            mode = "exhaustive"
        else:
            mode = "sequential"
    if mode == "exhaustive":
        for i in range(n):
            for j in range(i + 1, n):
                pairs.add((i, j))
    elif mode == "gps" and gps_xy is not None:
        from scipy.spatial import cKDTree
        k = min(gps_k + 1, n)
        _, nn = cKDTree(gps_xy).query(gps_xy, k=k)
        for i in range(n):
            for j in np.atleast_1d(nn[i])[1:]:
                pairs.add((min(i, int(j)), max(i, int(j))))
        for i in range(n - 1):                                     # always the sequence neighbours too
            pairs.add((i, i + 1))
    else:
        for i in range(n):
            for d in range(1, window + 1):
                if i + d < n:
                    pairs.add((i, i + d))
            # a few longer jumps help to close loops in long sequences
            for d in (2 * window, 4 * window):
                if i + d < n and i % max(window // 2, 1) == 0:
                    pairs.add((i, i + d))
    return sorted(pairs), mode


# ---------------------------------------------------------------- matching
def _knn(d1, d2, method):
    cv2 = cv2_module()
    if method == "orb":
        m = cv2.BFMatcher(cv2.NORM_HAMMING)
        return m.knnMatch(d1, d2, k=2)
    m = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=64))
    return m.knnMatch(d1.astype(np.float32), d2.astype(np.float32), k=2)


def _ratio(knn, ratio):
    out = {}
    for pair in knn:
        if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance:
            out[pair[0].queryIdx] = pair[0].trainIdx
        elif len(pair) == 1:
            out[pair[0].queryIdx] = pair[0].trainIdx
    return out


def match_pair(fa, fb, method="sift", ratio=0.8, ransac_px=2.0, min_inliers=25):
    """Mutual ratio-test matches verified with a fundamental matrix (MAGSAC / RANSAC).
    Returns (m, 2) index array, or None."""
    cv2 = cv2_module()
    if len(fa.kp) < 8 or len(fb.kp) < 8:
        return None
    ab = _ratio(_knn(fa.desc, fb.desc, method), ratio)
    ba = _ratio(_knn(fb.desc, fa.desc, method), ratio)
    m = np.array([(i, j) for i, j in ab.items() if ba.get(j) == i], np.int64).reshape(-1, 2)
    if len(m) < min_inliers:
        return None
    p1, p2 = fa.kp[m[:, 0]], fb.kp[m[:, 1]]
    flag = getattr(cv2, "USAC_MAGSAC", cv2.FM_RANSAC)
    try:
        F, mask = cv2.findFundamentalMat(p1, p2, flag, ransac_px, 0.999, 10000)
    except cv2.error:
        F, mask = cv2.findFundamentalMat(p1, p2, cv2.FM_RANSAC, ransac_px, 0.999)
    if F is None or mask is None:
        return None
    m = m[mask.ravel().astype(bool)]
    return m if len(m) >= min_inliers else None


def match_all(feats, pairs, method="sift", ratio=0.8, ransac_px=2.0, min_inliers=25, workers=4,
              progress=None, cancel=None):
    out = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(match_pair, feats[i], feats[j], method, ratio, ransac_px, min_inliers): (i, j)
                for i, j in pairs}
        for fu in futs:
            if cancel is not None and cancel():
                break
            m = fu.result()
            if m is not None:
                out[futs[fu]] = m
            done += 1
            if progress and done % 5 == 0:
                progress(done / len(pairs))
    return out


# ---------------------------------------------------------------- tracks
def build_tracks(feats, matches, min_len=2):
    """Union–find over matched features. Returns (tracks: list of (img, kp) arrays, kp2track per image)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    offs = np.cumsum([0] + [len(f.kp) for f in feats])
    rows = [m[:, 0] + offs[i] for (i, j), m in matches.items()]
    cols = [m[:, 1] + offs[j] for (i, j), m in matches.items()]
    r_ = np.concatenate(rows) if rows else np.empty(0, np.int64)
    c_ = np.concatenate(cols) if cols else np.empty(0, np.int64)
    G = coo_matrix((np.ones(len(r_), np.int8), (r_, c_)), shape=(offs[-1], offs[-1]))
    _, roots = connected_components(G, directed=False)
    img_of = np.repeat(np.arange(len(feats)), np.diff(offs))
    kp_of = np.arange(offs[-1]) - offs[img_of]
    order = np.argsort(roots, kind="stable")
    r = roots[order]
    starts = np.flatnonzero(np.r_[True, r[1:] != r[:-1]])
    ends = np.r_[starts[1:], len(r)]
    tracks = []
    kp2track = [-np.ones(len(f.kp), np.int64) for f in feats]
    for s, e in zip(starts, ends):
        if e - s < min_len:
            continue
        nodes = order[s:e]
        imgs = img_of[nodes]
        if len(np.unique(imgs)) != len(imgs):                    # two features of one image: inconsistent
            continue
        tid = len(tracks)
        tr = np.column_stack([imgs, kp_of[nodes]])
        tracks.append(tr)
        for im, k in tr:
            kp2track[im][k] = tid
    return tracks, kp2track
