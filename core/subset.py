"""Subset (BASE-Editor style): an oriented box in plan view, paged along its long axis."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Subset:
    p0: np.ndarray              # start of the long axis (world XY)
    p1: np.ndarray              # end of the long axis
    width: float = 20.0         # across-axis width (m)
    overlap: float = 0.1        # page overlap fraction

    def __post_init__(self):
        self.p0 = np.asarray(self.p0, dtype=np.float64)
        self.p1 = np.asarray(self.p1, dtype=np.float64)

    @property
    def length(self):
        return float(np.hypot(*(self.p1 - self.p0)))

    @property
    def u(self):                # along-axis unit vector
        return (self.p1 - self.p0) / max(self.length, 1e-12)

    @property
    def n(self):                # across-axis unit vector (left of the axis)
        u = self.u
        return np.array([-u[1], u[0]])

    @property
    def center(self):
        return (self.p0 + self.p1) / 2

    def corners(self):
        h = self.n * self.width / 2
        return np.array([self.p0 - h, self.p1 - h, self.p1 + h, self.p0 + h])

    def local(self, xy):
        """(along, across) coordinates: along ∈ [0, length], across ∈ [-w/2, w/2] inside the box."""
        d = np.asarray(xy, dtype=np.float64) - self.p0
        return d @ self.u, d @ self.n

    def select(self, xyz, idx, chunk=4_000_000):
        """Indices (subset of idx) inside the box + their along / across coordinates."""
        c = self.corners()
        lo, hi = c.min(axis=0), c.max(axis=0)
        out_i, out_a, out_c = [], [], []
        for s in range(0, len(idx), chunk):
            ci = idx[s:s + chunk]
            xy = xyz[ci, :2]
            m = (xy[:, 0] >= lo[0]) & (xy[:, 0] <= hi[0]) & (xy[:, 1] >= lo[1]) & (xy[:, 1] <= hi[1])
            if not m.any():
                continue
            ci, xy = ci[m], xy[m]
            a, cr = self.local(xy)
            k = (a >= 0) & (a <= self.length) & (np.abs(cr) <= self.width / 2)
            out_i.append(ci[k])
            out_a.append(a[k])
            out_c.append(cr[k])
        if not out_i:
            e = np.empty(0)
            return e.astype(np.int64), e, e
        return np.concatenate(out_i), np.concatenate(out_a), np.concatenate(out_c)

    def paged(self, direction=1):
        """The next (+1) / previous (-1) box along the axis."""
        step = self.u * self.length * (1 - self.overlap) * direction
        return Subset(self.p0 + step, self.p1 + step, self.width, self.overlap)

    def to_dict(self):
        return {"p0": self.p0.tolist(), "p1": self.p1.tolist(), "width": self.width, "overlap": self.overlap}

    @classmethod
    def from_dict(cls, d):
        return cls(np.array(d["p0"]), np.array(d["p1"]), d["width"], d.get("overlap", 0.1))


def coverage(subsets, xyz, idx):
    """Fraction of points (idx) inside at least one of the boxes."""
    if len(idx) == 0 or not subsets:
        return 0.0
    hit = np.zeros(len(idx), dtype=bool)
    pos = {int(v): k for k, v in enumerate(idx)} if len(idx) < 0 else None   # (not needed: use mask below)
    mask_full = np.zeros(int(idx.max()) + 1, dtype=bool)
    for sb in subsets:
        i, _, _ = sb.select(xyz, idx)
        mask_full[i] = True
    hit = mask_full[idx]
    return float(hit.mean())
