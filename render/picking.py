"""Screen-space projection, region selection and point picking.

All visible transforms (camera, Z exaggeration, viewport) are linear in homogeneous
coordinates, so the whole chain collapses to one 4×4 matrix (row-vector convention).
Region selection is done against *all* active points (not only displayed ones),
processed in chunks to bound memory.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF

CHUNK = 4_000_000


def screen_matrix(viewer):
    tr = viewer.markers.get_transform("visual", "canvas")
    return np.asarray(tr.map(np.eye(4)), dtype=np.float64).astype(np.float32)


def _project(pc, idx, M):
    pts = (pc.xyz[idx] - pc.offset).astype(np.float32)
    p = pts @ M[:3] + M[3]
    w = p[:, 3]
    valid = w > 1e-12
    with np.errstate(divide="ignore", invalid="ignore"):
        x = p[:, 0] / w
        y = p[:, 1] / w
    return x, y, w, valid


def _polygon_mask(poly, width, height):
    img = QImage(width, height, QImage.Format_Grayscale8)
    img.fill(0)
    painter = QPainter(img)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in poly]))
    painter.end()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8)
    return buf.reshape(height, img.bytesPerLine())[:, :width].copy()


def select_polygon(viewer, pc, poly):
    """Indices of active points whose screen projection lies inside the polygon."""
    width, height = (int(v) for v in viewer.canvas.size)
    poly = np.asarray(poly, dtype=np.float64)
    mask = _polygon_mask(poly, width, height)
    x0, y0 = np.maximum(poly.min(axis=0), 0)
    x1, y1 = np.minimum(poly.max(axis=0), (width - 1, height - 1))
    M = screen_matrix(viewer)
    active = pc.active_indices()
    out = []
    for s in range(0, len(active), CHUNK):
        idx = active[s:s + CHUNK]
        x, y, _, ok = _project(pc, idx, M)
        ok &= (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
        cand = np.flatnonzero(ok)
        if len(cand) == 0:
            continue
        inside = mask[y[cand].astype(np.int32), x[cand].astype(np.int32)] > 0
        out.append(idx[cand[inside]])
    return np.concatenate(out) if out else np.empty(0, dtype=np.int64)


def pick_point(viewer, pc, pos, radius=8.0):
    """Front-most displayed point within `radius` px of pos, or None."""
    idx_all = viewer.display_idx
    if len(idx_all) == 0:
        return None
    M = screen_matrix(viewer)
    px, py = float(pos[0]), float(pos[1])
    best_i, best_w = None, np.inf
    for s in range(0, len(idx_all), CHUNK):
        idx = idx_all[s:s + CHUNK]
        x, y, w, ok = _project(pc, idx, M)
        d2 = (x - px) ** 2 + (y - py) ** 2
        cand = np.flatnonzero(ok & (d2 <= radius * radius))
        if len(cand) == 0:
            continue
        # prefer front-most; among near-equal depth prefer closest to cursor
        score = w[cand] * (1.0 + 0.002 * np.sqrt(d2[cand]))
        k = cand[np.argmin(score)]
        if w[k] < best_w:
            best_i, best_w = int(idx[k]), float(w[k])
    return best_i
