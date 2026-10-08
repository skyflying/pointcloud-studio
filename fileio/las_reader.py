"""LAS / LAZ reader. Keeps the original LasData so export can reuse header, point format and VLRs."""
from __future__ import annotations

import os

import laspy
import numpy as np

from core.pointcloud import PointCloud

ATTR_MAP = {"intensity": "Intensity", "classification": "Classification",
            "red": "R", "green": "G", "blue": "B"}


def read_las(path):
    las = laspy.read(path)
    xyz = np.column_stack((np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)))
    dims = list(las.point_format.dimension_names)
    attrs = {name: np.asarray(las[d]) for d, name in ATTR_MAP.items() if d in dims}

    h = las.header
    source = {
        "type": "las", "path": path, "las": las,
        "version": f"{h.version.major}.{h.version.minor}",
        "point_format": h.point_format.id,
        "scales": tuple(float(v) for v in h.scales),
        "offsets": tuple(float(v) for v in h.offsets),
        "dimensions": dims,
        "vlrs": [f"{v.user_id} / {v.record_id}: {v.description}" for v in h.vlrs],
        "compressed": path.lower().endswith(".laz"),
    }
    return PointCloud(xyz, attrs, source, name=os.path.basename(path))
