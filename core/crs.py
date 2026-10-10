"""Coordinate reference system helpers (pyproj)."""
from __future__ import annotations

import numpy as np

PRESETS = [
    (3826, "TWD97 / TM2 zone 121"),
    (3825, "TWD97 / TM2 zone 119"),
    (3828, "TWD67 / TM2 zone 121"),
    (3827, "TWD67 / TM2 zone 119"),
    (32651, "WGS 84 / UTM zone 51N"),
    (32650, "WGS 84 / UTM zone 50N"),
    (4326, "WGS 84 (latitude / longitude)"),
]

_INDEX = None


def from_epsg(code):
    from pyproj import CRS
    return CRS.from_epsg(int(code))


def from_text(text):
    """'3826', 'EPSG:3826' or WKT → CRS; None for empty / 'unknown'."""
    if text is None:
        return None
    t = str(text).strip()
    if not t or t.lower() in ("none", "unknown", "local"):
        return None
    from pyproj import CRS
    if t.isdigit():
        return CRS.from_epsg(int(t))
    return CRS.from_user_input(t)


def to_text(crs):
    """Compact persistent form: 'EPSG:3826' when possible, else WKT; '' for unknown."""
    if crs is None:
        return ""
    code = epsg_of(crs)
    return f"EPSG:{code}" if code else crs.to_wkt()


def epsg_of(crs):
    if crs is None:
        return None
    try:
        return crs.to_epsg(min_confidence=70)
    except Exception:
        return None


def label(crs):
    if crs is None:
        return "Unknown / local coordinates"
    code = epsg_of(crs)
    return f"EPSG:{code} — {crs.name}" if code else crs.name


def is_geographic(crs):
    return crs is not None and crs.is_geographic


def same(a, b):
    """True if both known and equal; False if both known and different; None if either unknown."""
    if a is None or b is None:
        return None
    try:
        return bool(a.equals(b, ignore_axis_order=True))
    except TypeError:
        return bool(a == b)


def las_crs(las):
    try:
        return las.header.parse_crs()
    except Exception:
        return None


def guess_from_prj(path):
    """CRS from a .prj (Shapefile) / WKT file, or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return from_text(f.read())
    except Exception:
        return None


def transform_xy(x, y, src, dst, chunk=2_000_000):
    """Reproject coordinate arrays (always_xy: x = easting / longitude)."""
    from pyproj import Transformer
    tr = Transformer.from_crs(src, dst, always_xy=True)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    ox, oy = np.empty_like(x), np.empty_like(y)
    for s in range(0, len(x), chunk):
        ox[s:s + chunk], oy[s:s + chunk] = tr.transform(x[s:s + chunk], y[s:s + chunk])
    return ox, oy


def search(text, limit=200):
    """EPSG CRS search by code or name fragment → [(code, name, kind)]."""
    global _INDEX
    if _INDEX is None:
        from pyproj.database import query_crs_info
        from pyproj.enums import PJType
        rows = query_crs_info(auth_name="EPSG", pj_types=[PJType.PROJECTED_CRS, PJType.GEOGRAPHIC_2D_CRS],
                              allow_deprecated=False)
        _INDEX = sorted(((int(r.code), r.name, "geographic" if "GEOGRAPHIC" in r.type.name else "projected")
                         for r in rows if r.code.isdigit()), key=lambda r: r[0])
    t = (text or "").strip().lower()
    if not t:
        return [(c, n, k) for c, n, k in _INDEX if c in {p[0] for p in PRESETS}]
    out = []
    for c, n, k in _INDEX:
        if t in str(c) or all(w in n.lower() for w in t.split()):
            out.append((c, n, k))
            if len(out) >= limit:
                break
    return out
