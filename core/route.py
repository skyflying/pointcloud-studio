"""Cable / pipeline routes: polyline in project coordinates + KP (kilometre point) per vertex."""
from __future__ import annotations

from dataclasses import dataclass, field

DIST_LABEL = ["KP"]          # "KP" (cables / pipelines offshore) or "Chainage" (roads, land pipelines)


def dist_label():
    return DIST_LABEL[0]

import numpy as np


@dataclass
class Route:
    name: str
    xy: np.ndarray                      # (N, 2) project coordinates
    kp: np.ndarray | None = None        # (N,) KP in km at each vertex (None → computed from start_kp)
    start_kp: float = 0.0               # km, used when kp is None
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.xy = np.asarray(self.xy, dtype=np.float64)
        if self.kp is not None:
            self.kp = np.asarray(self.kp, dtype=np.float64)

    @property
    def cum(self):
        """Distance along the polyline (m) at each vertex."""
        return np.r_[0.0, np.cumsum(np.hypot(*np.diff(self.xy, axis=0).T))]

    @property
    def length(self):
        return float(self.cum[-1])

    @property
    def kp_vertices(self):
        """KP (km) at each vertex: from the file, or start_kp + distance."""
        return self.kp if self.kp is not None else self.start_kp + self.cum / 1000.0

    def kp_at(self, chainage_m):
        """KP (km) at a distance along the line."""
        return np.interp(chainage_m, self.cum, self.kp_vertices)

    def chainage_at_kp(self, kp_km):
        k = self.kp_vertices
        if np.all(np.diff(k) >= 0):
            return np.interp(kp_km, k, self.cum)
        return np.interp(kp_km, k[::-1], self.cum[::-1])

    def xy_at(self, chainage_m):
        c = self.cum
        s = np.clip(np.asarray(chainage_m, float), 0, c[-1])
        i = np.clip(np.searchsorted(c, s, side="right") - 1, 0, len(c) - 2)
        t = (s - c[i]) / np.maximum(c[i + 1] - c[i], 1e-12)
        return self.xy[i] + (self.xy[i + 1] - self.xy[i]) * t[..., None]

    def kp_range(self):
        k = self.kp_vertices
        return float(k.min()), float(k.max())
