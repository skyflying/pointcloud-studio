"""Vector reference layer: points, lines and polygons from GIS / CAD files, in project coordinates.

Shown in 3D (draped on the terrain, or at their own Z for 3D lines), usable as analysis areas
(polygons) and as routes / centre lines (lines)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class VectorLayer:
    features: list                      # [{"type", "parts": [array (N, 2|3)], "attrs": {}}]
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.features = [{"type": f["type"], "parts": [np.asarray(p, float) for p in f["parts"]],
                          "attrs": dict(f.get("attrs", {}))} for f in self.features]

    def __len__(self):
        return len(self.features)

    def counts(self):
        c = {}
        for f in self.features:
            c[f["type"]] = c.get(f["type"], 0) + 1
        return c

    def bounds(self):
        allp = np.vstack([p[:, :2] for f in self.features for p in f["parts"]])
        return allp.min(axis=0), allp.max(axis=0)

    def z_range(self):
        zs = [p[:, 2] for f in self.features for p in f["parts"] if p.shape[1] >= 3]
        zs = np.concatenate(zs) if zs else np.empty(0)
        zs = zs[np.isfinite(zs) & (np.abs(zs) > 1e-9)]
        return (float(zs.min()), float(zs.max())) if len(zs) else (np.nan, np.nan)

    def label(self, k):
        a = self.features[k]["attrs"]
        for key in ("name", "NAME", "Name", "id", "ID", "label", "layer"):
            if a.get(key) not in (None, ""):
                return str(a[key])
        return f"{self.features[k]['type'].lower()} {k + 1}"

    def polygons(self):
        """[(index, label, outer ring (N, 2))]"""
        return [(k, self.label(k), f["parts"][0][:, :2]) for k, f in enumerate(self.features)
                if f["type"] == "Polygon" and len(f["parts"][0]) >= 3]

    def lines(self):
        """[(index, label, polyline (N, 2|3))]"""
        return [(k, self.label(k), f["parts"][0]) for k, f in enumerate(self.features)
                if f["type"] == "LineString" and len(f["parts"][0]) >= 2]

    def size_text(self):
        c = self.counts()
        return " · ".join(f"{n} {t.replace('LineString', 'line').replace('Polygon', 'polygon').replace('Point', 'point')}"
                          f"{'s' if n != 1 else ''}" for t, n in c.items()) or "empty"

    def to_json(self):
        def val(v):
            if isinstance(v, (bool, np.bool_)):
                return bool(v)
            if isinstance(v, (int, np.integer)):
                return int(v)
            if isinstance(v, (float, np.floating)):
                return float(v) if np.isfinite(v) else None
            return None if v is None else str(v)
        return [{"type": f["type"], "parts": [p.tolist() for p in f["parts"]],
                 "attrs": {str(k): val(v) for k, v in f["attrs"].items()}} for f in self.features]
