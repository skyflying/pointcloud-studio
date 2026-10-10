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
        # LAS noise handling: points classified 7 ("rejected") are hidden from display, selection and
        # surfaces while hide_noise is on. orig_class keeps the class each point had when loaded.
        self.hide_noise = True
        self.orig_class = None
        self.reject_classes = {7, 18}       # ASPRS low noise (7), high noise (18) + any class used to reject

    def recompute_bbox(self):
        self.bbox_min = self.xyz.min(axis=0)
        self.bbox_max = self.xyz.max(axis=0)

    def __len__(self):
        return len(self.xyz)

    def has(self, name):
        return name in self.attrs

    # ---------- noise (LAS class 7) ----------
    NOISE = 7

    @property
    def is_las(self):
        return self.source.get("las") is not None

    @property
    def max_class(self):
        """LAS point formats 0–5 store 5-bit classes (0–31); formats 6–10 store 0–255."""
        las = self.source.get("las")
        try:
            return 255 if las is not None and las.header.point_format.id >= 6 else 31
        except Exception:
            return 31

    @property
    def can_classify(self):
        return self.is_las and "Classification" in self.attrs

    def noise_mask(self):
        if not self.can_classify:
            return np.zeros(len(self), dtype=bool)
        return np.isin(self.attrs["Classification"], list(self.reject_classes))

    @property
    def n_noise(self):
        return int(np.count_nonzero(self.noise_mask() & (self.deleted_batch == 0)))

    def ensure_classification(self):
        """Clouds without a classification (e.g. XYZ) get one: all 1 = unclassified."""
        if "Classification" not in self.attrs:
            self.attrs["Classification"] = np.ones(len(self), dtype=np.uint8)
            dims = self.source.get("dimensions")
            if isinstance(dims, list) and "Classification" not in dims:
                dims.append("Classification")

    def set_class(self, idx, values):
        """Write classes to the point cloud and (LAS) to the record used for export."""
        idx = np.asarray(idx, dtype=np.int64)
        self.ensure_classification()
        if self.orig_class is None:
            self.orig_class = self.attrs["Classification"].copy()
        cls = self.attrs["Classification"]
        cls[idx] = values
        las = self.source.get("las")
        if las is not None:
            arr = np.array(las.classification)
            arr[idx] = values
            las.classification = arr

    def original_class(self, idx):
        src = self.orig_class if self.orig_class is not None else self.attrs["Classification"]
        out = np.array(src[idx])
        out[np.isin(out, list(self.reject_classes))] = 1   # was already rejected when loaded → "unclassified"
        return out

    # ---------- active points ----------
    def active_mask(self):
        m = self.deleted_batch == 0
        if self.hide_noise and self.can_classify:
            m &= ~np.isin(self.attrs["Classification"], list(self.reject_classes))
        return m

    @property
    def n_active(self):
        return int(np.count_nonzero(self.active_mask()))

    def active_indices(self):
        return np.flatnonzero(self.active_mask())

    def local_xyz(self, idx=None):
        pts = self.xyz if idx is None else self.xyz[idx]
        return (pts - self.offset).astype(np.float32)
