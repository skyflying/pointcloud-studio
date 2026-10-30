"""LAS / LAZ reader. Keeps the original LasData so export can reuse header, point format and VLRs."""
from __future__ import annotations

import os

import laspy
import numpy as np

from core.pointcloud import PointCloud

ATTR_MAP = {"intensity": "Intensity", "classification": "Classification",
            "red": "R", "green": "G", "blue": "B"}


KEEP_RECORDS_BELOW = 2_000_000          # smaller files keep their original records in memory (fast export)


def read_las(path, keep_records=None):
    """keep_records None: automatic — files with fewer than KEEP_RECORDS_BELOW points keep the original records
    (laspy.LasData); larger files are read in chunks and only XYZ + attributes stay in memory (the records are
    streamed from the file again for LAS export / other dimensions) — about half the memory."""
    with laspy.open(path) as r:
        n = int(r.header.point_count)
    keep = (n < KEEP_RECORDS_BELOW) if keep_records is None else bool(keep_records)
    if not keep:
        return _read_las_streamed(path, n)
    las = laspy.read(path)
    xyz = np.column_stack((np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)))
    dims = list(las.point_format.dimension_names)
    attrs = {name: np.asarray(las[d]) for d, name in ATTR_MAP.items() if d in dims}
    # extra bytes (e.g. TPU / TVU / THU from CARIS or Qimera, 'views' from photogrammetry): numeric 1-D only
    for d in getattr(las.point_format, "extra_dimension_names", []):
        if d in attrs or d in ATTR_MAP.values():
            continue
        try:
            a = np.asarray(las[d])
        except Exception:  # noqa: BLE001
            continue
        if a.ndim == 1 and a.dtype.kind in "fiu":
            attrs[d] = a

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


def _read_las_streamed(path, n):
    """Chunked read into pre-allocated arrays (peak memory ≈ the result + one chunk)."""
    from fileio.las_ref import CHUNK, LasRef
    with laspy.open(path) as r:
        hdr = r.header
        pf = hdr.point_format
        dims = list(pf.dimension_names)
        extra = [d for d in getattr(pf, "extra_dimension_names", [])]
        xyz = np.empty((n, 3), np.float64)
        attrs, start = {}, 0
        for pts in r.chunk_iterator(CHUNK):
            m = len(pts)
            sl = slice(start, start + m)
            xyz[sl, 0], xyz[sl, 1], xyz[sl, 2] = np.asarray(pts.x), np.asarray(pts.y), np.asarray(pts.z)
            for d, name in ATTR_MAP.items():
                if d in dims:
                    a = np.asarray(pts[d])
                    if name not in attrs:
                        attrs[name] = np.empty(n, a.dtype)
                    attrs[name][sl] = a
            for d in extra:
                if d in attrs or d in ATTR_MAP.values():
                    continue
                try:
                    a = np.asarray(pts[d])
                except Exception:  # noqa: BLE001
                    continue
                if a.ndim == 1 and a.dtype.kind in "fiu":
                    if d not in attrs:
                        attrs[d] = np.empty(n, a.dtype)
                    attrs[d][sl] = a
            start += m
        if start != n:
            xyz = xyz[:start]
            attrs = {k: v[:start] for k, v in attrs.items()}
        source = {
            "type": "las", "path": path, "las": LasRef(path, hdr, start),
            "version": f"{hdr.version.major}.{hdr.version.minor}",
            "point_format": pf.id,
            "scales": tuple(float(v) for v in hdr.scales),
            "offsets": tuple(float(v) for v in hdr.offsets),
            "dimensions": dims,
            "vlrs": [f"{v.user_id} / {v.record_id}: {v.description}" for v in hdr.vlrs],
            "compressed": path.lower().endswith(".laz"),
            "records_in_memory": False,
        }
    return PointCloud(xyz, attrs, source, name=os.path.basename(path))
