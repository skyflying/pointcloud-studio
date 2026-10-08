"""Point cloud data model.

Coordinates are kept in float64. For GPU display the offset is subtracted and the
result cast to float32, so large projected coordinates (e.g. TWD97) keep precision.
deleted_batch: 0 = active, >0 = id of the delete batch (trash, used from phase 3).
"""
from __future__ import annotations

import numpy as np

XYZ_ROLES = ("X", "Y", "Z")
STANDARD_ATTRS = ("Intensity", "R", "G", "B", "Classification")


class PointCloud:
    def __init__(self, xyz, attrs=None, source=None, name=""):
        xyz = np.ascontiguousarray(xyz, dtype=np.float64)
        if xyz.ndim != 2 or xyz.shape[1] != 3:
            raise ValueError("Coordinate array must be N×3")
        if len(xyz) == 0:
            raise ValueError("No valid points were read")
        self.xyz = xyz
        self.attrs = {}
        for key, arr in (attrs or {}).items():
            arr = np.asarray(arr)
            if len(arr) != len(xyz):
                raise ValueError(f"Attribute {key} length does not match point count")
            self.attrs[key] = arr
        self.source = source or {}
        self.name = name

        self.bbox_min = xyz.min(axis=0)
        self.bbox_max = xyz.max(axis=0)
        self.offset = np.round((self.bbox_min + self.bbox_max) / 2.0)
        self.deleted_batch = np.zeros(len(xyz), dtype=np.int32)

    def __len__(self):
        return len(self.xyz)

    def has(self, name):
        return name in self.attrs

    @property
    def n_active(self):
        return int(np.count_nonzero(self.deleted_batch == 0))

    def active_indices(self):
        return np.flatnonzero(self.deleted_batch == 0)

    def local_xyz(self, idx=None):
        pts = self.xyz if idx is None else self.xyz[idx]
        return (pts - self.offset).astype(np.float32)
