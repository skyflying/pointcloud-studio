"""Exporters: LAS / LAZ / text. All writers stream in chunks and report progress (0..1)."""
from __future__ import annotations

import copy
import re

import laspy
import numpy as np

from core.pointcloud import STANDARD_ATTRS

CHUNK = 1_000_000
TEXT_DELIMITERS = {"space": ("Space", " "), "tab": ("Tab", "\t"),
                   "comma": ("Comma  ,", ","), "semicolon": ("Semicolon  ;", ";")}

try:
    import pyproj  # optional: embed a CRS when exporting XYZ → LAS
except ImportError:  # pragma: no cover
    pyproj = None


def _chunks(idx):
    for s in range(0, len(idx), CHUNK):
        yield s, idx[s:s + CHUNK]


def _report(progress, done, total):
    if progress is not None:
        progress(done / max(total, 1))


# ---------------------------------------------------------------- columns
def column_choices(pc):
    """All columns that can be written to a text file, in natural order."""
    las = pc.source.get("las")
    if las is not None:
        return ["X", "Y", "Z"] + [d for d in pc.source["dimensions"] if d not in ("X", "Y", "Z")]
    return ["X", "Y", "Z"] + list(pc.attrs)


def default_columns(pc):
    if pc.source.get("type") == "xyz":
        out = []
        for i, r in enumerate(pc.source["roles"]):
            if r == "Ignore":
                continue
            out.append(r if r != "Custom" else f"col_{i + 1}")
        return out
    return ["X", "Y", "Z"]


def _values(pc, name, ci):
    if name in ("X", "Y", "Z"):
        return pc.xyz[ci, "XYZ".index(name)]
    las = pc.source.get("las")
    if las is not None:
        return np.asarray(las[name][ci])
    return pc.attrs[name][ci]


def _text_format(pc, name, idx, decimals):
    if name in ("X", "Y", "Z"):
        return f"%.{decimals}f"
    sample = _values(pc, name, idx[:200_000])
    if np.issubdtype(sample.dtype, np.integer):
        return "%d"
    a = _values(pc, name, idx).astype(np.float64)
    if np.isfinite(a).all() and np.all(a == np.round(a)):
        return "%d"
    return "%.10g"


# ---------------------------------------------------------------- text
def write_text(pc, idx, path, columns, delimiter=" ", decimals=3, header=True, progress=None):
    fmts = [_text_format(pc, c, idx, decimals) for c in columns]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if header:
            f.write(delimiter.join(columns) + "\n")
        for s, ci in _chunks(idx):
            arr = np.column_stack([_values(pc, c, ci).astype(np.float64) for c in columns])
            np.savetxt(f, arr, fmt=fmts, delimiter=delimiter)
            _report(progress, s + len(ci), len(idx))


# ---------------------------------------------------------------- LAS / LAZ
def _las_name(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name)[:32] or "extra"


def write_las(pc, idx, path, compress=None, version="1.2", scale=0.001, epsg=None,
              extra_dims=True, progress=None):
    if compress is None:
        compress = path.lower().endswith(".laz")
    las = pc.source.get("las")

    if las is not None:  # LAS → LAS: keep everything from the source
        hdr = copy.deepcopy(las.header)
        with laspy.open(path, mode="w", header=hdr, do_compress=compress) as w:
            for s, ci in _chunks(idx):
                w.write_points(las.points[ci])
                _report(progress, s + len(ci), len(idx))
        return

    # XYZ → LAS: build a header
    a = pc.attrs
    has_rgb = all(k in a for k in ("R", "G", "B"))
    cls = a.get("Classification")
    cls_max = float(np.nanmax(cls[idx])) if cls is not None and len(idx) else 0
    v14 = version == "1.4" or cls_max > 31
    pf = (7 if has_rgb else 6) if v14 else (2 if has_rgb else 0)
    hdr = laspy.LasHeader(point_format=pf, version="1.4" if v14 else "1.2")
    hdr.scales = np.array([scale] * 3, dtype=np.float64)
    hdr.offsets = np.floor(pc.xyz[idx].min(axis=0)) if len(idx) else np.zeros(3)

    custom = [k for k in a if k not in STANDARD_ATTRS] if extra_dims else []
    if custom:
        hdr.add_extra_dims([laspy.ExtraBytesParams(name=_las_name(k), type=np.float64,
                                                   description=k[:32]) for k in custom])
    if epsg:
        if pyproj is None:
            raise RuntimeError("Embedding a CRS requires the 'pyproj' package")
        hdr.add_crs(pyproj.CRS.from_epsg(int(epsg)))

    rgb_mul = 1
    if has_rgb:
        mx = max(float(np.nanmax(a[k][idx])) for k in ("R", "G", "B")) if len(idx) else 0
        rgb_mul = 257 if mx <= 255 else 1
    cls_cap = 255 if v14 else 31

    with laspy.open(path, mode="w", header=hdr, do_compress=compress) as w:
        for s, ci in _chunks(idx):
            pts = laspy.ScaleAwarePointRecord.zeros(len(ci), header=hdr)
            pts.x, pts.y, pts.z = pc.xyz[ci, 0], pc.xyz[ci, 1], pc.xyz[ci, 2]
            if "Intensity" in a:
                pts.intensity = np.clip(np.nan_to_num(a["Intensity"][ci]), 0, 65535).astype(np.uint16)
            if cls is not None:
                pts.classification = np.clip(np.nan_to_num(cls[ci]), 0, cls_cap).astype(np.uint8)
            if has_rgb:
                for k, d in (("R", "red"), ("G", "green"), ("B", "blue")):
                    pts[d] = np.clip(np.nan_to_num(a[k][ci]) * rgb_mul, 0, 65535).astype(np.uint16)
            for k in custom:
                pts[_las_name(k)] = a[k][ci].astype(np.float64)
            w.write_points(pts)
            _report(progress, s + len(ci), len(idx))


# ---------------------------------------------------------------- dispatch
def export(pc, idx, path, fmt, opts, progress=None):
    idx = np.asarray(idx, dtype=np.int64)
    if fmt in ("las", "laz"):
        write_las(pc, idx, path, compress=(fmt == "laz"), progress=progress, **opts)
    else:
        write_text(pc, idx, path, progress=progress, **opts)
