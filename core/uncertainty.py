"""Vertical / horizontal uncertainty (TVU / THU) of every point, from one of several sources.

Sources (spec["tvu"] / spec["thu"] → {"source": …}):
  attribute   a column of the cloud (LAS extra bytes / XYZ column), e.g. TPU, TVU, THU, Uncertainty
  table       another XYZ / CSV file with x, y and the value(s) — matched by plan position (nearest or IDW)
  raster      a GeoTIFF / BAG / any GDAL raster band sampled at the points (bilinear)
  iho         modelled from an IHO S-44 order:  TVU = √(a² + (b·d)²),  THU = c + p·d   (95 %)
  constant    one value for all points

Values may be given at 95 % confidence (CARIS / Qimera TPU, IHO limits) or as 1σ; everything is
returned as 1σ (standard uncertainty) — the 95 % values are divided by 1.96."""
from __future__ import annotations

import os

import numpy as np

Z95 = 1.96
NAME_HINTS = {
    "tvu": ("tvu", "tpu", "vertical_uncertainty", "vert_unc", "uncertainty", "unc", "depth_tpu", "std_z",
            "sigma_z", "tpu_v", "dtpu"),
    "thu": ("thu", "horizontal_uncertainty", "hor_unc", "htpu", "tpu_h", "position_tpu", "sigma_xy", "std_xy"),
}


def guess_attribute(names, kind="tvu"):
    """Best matching attribute name for TVU or THU (case-insensitive), or None."""
    low = {n.lower(): n for n in names}
    for h in NAME_HINTS[kind]:
        if h in low:
            return low[h]
    for n in names:
        nl = n.lower()
        if any(h in nl for h in NAME_HINTS[kind][:3]):
            return n
    return None


# ---------------------------------------------------------------- tables (XYZ / CSV)
def read_table(path, x_col=0, y_col=1, value_cols=(2,), skip_header=None):
    """Numeric columns of a delimited text file (pandas C parser — millions of rows are fine).
    Returns xy (n, 2), values (n, k), header names or None."""
    import pandas as pd
    from fileio.xyz_reader import detect_delimiter, detect_encoding, split_line
    enc = detect_encoding(path)
    with open(path, encoding=enc, errors="replace") as f:
        head = [ln for _, ln in zip(range(300), f) if ln.strip() and not ln.lstrip().startswith("#")]
    if not head:
        raise ValueError(f"{os.path.basename(path)} is empty")
    delim = detect_delimiter(head[:200])
    first = split_line(head[0].rstrip("\n"), delim)
    try:
        [float(first[i]) for i in (x_col, y_col)]
        header = None
    except (ValueError, IndexError):
        header = first
    sep = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}.get(delim, r"\s+")
    cols = [x_col, y_col] + list(value_cols)
    df = pd.read_csv(path, sep=sep, header=None, skiprows=1 if header else 0, usecols=cols, comment="#",
                     encoding=enc, on_bad_lines="skip")
    a = df[cols].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    a = a[np.isfinite(a).all(axis=1)]
    if not len(a):
        raise ValueError(f"No numeric rows in {os.path.basename(path)}")
    return a[:, :2], a[:, 2:], header


def match_table(xy_pts, xy_tab, val_tab, max_dist=0.0, method="nearest", k=4):
    """Values at the points from scattered table positions. max_dist > 0: points farther than that from
    any table position get NaN."""
    from scipy.spatial import cKDTree
    tree = cKDTree(xy_tab)
    if method == "idw" and len(xy_tab) >= k:
        d, j = tree.query(xy_pts, k=k)
        w = 1.0 / np.maximum(d, 1e-9) ** 2
        v = (val_tab[j] * w).sum(axis=1) / w.sum(axis=1)
        dmin = d[:, 0]
    else:
        dmin, j = tree.query(xy_pts, k=1)
        v = val_tab[j]
    if max_dist > 0:
        v = np.where(dmin <= max_dist, v, np.nan)
    return v


# ---------------------------------------------------------------- rasters (GeoTIFF / BAG / …)
def raster_bands(path):
    import rasterio
    with rasterio.open(path) as ds:
        return [ds.descriptions[i] or f"Band {i + 1}" for i in range(ds.count)]


def sample_raster(path, x, y, band=1, crs=None):
    """Bilinear sample of a raster band at project coordinates (reprojected to the raster's CRS)."""
    import rasterio
    with rasterio.open(path) as ds:
        xs, ys = np.asarray(x, float), np.asarray(y, float)
        if crs is not None and ds.crs is not None:
            try:
                from pyproj import CRS, Transformer
                src = CRS.from_user_input(crs)
                dst = CRS.from_user_input(ds.crs.to_wkt())
                if not src.equals(dst):
                    xs, ys = Transformer.from_crs(src, dst, always_xy=True).transform(xs, ys)
            except Exception:  # noqa: BLE001 — unknown CRS on one side: assume the same system
                pass
        inv = ~ds.transform
        col, row = inv * (xs, ys)
        col = np.asarray(col, float) - 0.5
        row = np.asarray(row, float) - 0.5
        # read only the window the points need (large BAG / GeoTIFF rasters), as float32
        from rasterio.windows import Window
        ok_ = np.isfinite(col) & np.isfinite(row)
        if not ok_.any():
            return np.full(len(xs), np.nan)
        c_lo = int(max(np.floor(col[ok_].min()) - 1, 0))
        r_lo = int(max(np.floor(row[ok_].min()) - 1, 0))
        c_hi = int(min(np.ceil(col[ok_].max()) + 2, ds.width))
        r_hi = int(min(np.ceil(row[ok_].max()) + 2, ds.height))
        if c_hi <= c_lo or r_hi <= r_lo:
            return np.full(len(xs), np.nan)
        a = ds.read(band, window=Window(c_lo, r_lo, c_hi - c_lo, r_hi - r_lo), masked=True)
        a = a.astype(np.float32).filled(np.nan)
        nod = ds.nodatavals[band - 1] if ds.nodatavals else None
        if nod is not None:
            a[a == np.float32(nod)] = np.nan
        a[np.abs(a) > 1e30] = np.nan                         # BAG / CARIS "no data" 1e6 … 1e38
        if "bag" in (ds.driver or "").lower():
            a[a >= 1e6] = np.nan
        col -= c_lo
        row -= r_lo
    h, w = a.shape
    c0 = np.floor(col).astype(np.int64)
    r0 = np.floor(row).astype(np.int64)
    fc, fr = col - c0, row - r0
    out = np.full(len(xs), np.nan)
    ok = (c0 >= -1) & (r0 >= -1) & (c0 < w) & (r0 < h)
    acc = np.zeros(len(xs))
    wsum = np.zeros(len(xs))
    for dr, dc, wt in ((0, 0, (1 - fr) * (1 - fc)), (0, 1, (1 - fr) * fc), (1, 0, fr * (1 - fc)), (1, 1, fr * fc)):
        rr, cc = r0 + dr, c0 + dc
        inside = ok & (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
        v = np.full(len(xs), np.nan)
        v[inside] = a[rr[inside], cc[inside]]
        good = np.isfinite(v)
        acc[good] += v[good] * wt[good]
        wsum[good] += wt[good]
    has = wsum > 1e-9
    out[has] = acc[has] / wsum[has]
    return out


def bag_supported():
    try:
        import rasterio
        from rasterio.drivers import raster_driver_extensions
        with rasterio.Env() as env:
            return "BAG" in env.drivers()
    except Exception:  # noqa: BLE001
        try:
            return ".bag" in raster_driver_extensions()
        except Exception:  # noqa: BLE001
            return False


# ---------------------------------------------------------------- resolve
def depth_of(z, depth_positive_down=True):
    """Depth (positive) from Z. Elevation data (sea floor negative) → −Z; already depths → Z."""
    z = np.asarray(z, float)
    return np.abs(z) if depth_positive_down is None else (-z if depth_positive_down else z)


def resolve_one(spec, pc, idx, kind, crs=None, depth=None):
    """1σ values for the points idx. spec: {"source", "attribute"|"path"|"order"|"value", "confidence",
    "column", "max_dist", "method", "band"}. Returns (values 1σ, description)."""
    src = spec.get("source", "iho")
    conf95 = spec.get("confidence", "95") == "95"
    scale = 1.0 / Z95 if conf95 else 1.0
    idx = np.asarray(idx)
    if src == "attribute":
        name = spec["attribute"]
        if name not in pc.attrs:
            raise ValueError(f"The cloud has no attribute '{name}'")
        v = np.asarray(pc.attrs[name], float)[idx]
        desc = f"attribute {name}"
    elif src == "table":
        xy_t, val_t, _ = read_table(spec["path"], spec.get("x_col", 0), spec.get("y_col", 1),
                                    (spec.get("column", 2),))
        v = match_table(pc.xyz[idx, :2], xy_t, val_t[:, 0], spec.get("max_dist", 0.0), spec.get("method", "nearest"))
        desc = f"table {os.path.basename(spec['path'])} (column {spec.get('column', 2) + 1})"
    elif src == "raster":
        v = sample_raster(spec["path"], pc.xyz[idx, 0], pc.xyz[idx, 1], spec.get("band", 1), crs)
        desc = f"raster {os.path.basename(spec['path'])} band {spec.get('band', 1)}"
    elif src == "constant":
        v = np.full(len(idx), float(spec["value"]))
        desc = f"constant {spec['value']:g} m"
    else:                                                  # IHO model (always 95 %)
        from core.s44 import allowed_thu, allowed_tvu
        d = depth if depth is not None else depth_of(pc.xyz[idx, 2])
        v = allowed_tvu(spec.get("order", "1a"), d) if kind == "tvu" else allowed_thu(spec.get("order", "1a"), d)
        scale = 1.0 / Z95
        desc = f"IHO S-44 Order {spec.get('order', '1a')} model"
    v = np.asarray(v, float) * scale
    v[~np.isfinite(v) | (v < 0)] = np.nan
    return v, desc + (" (95 % → 1σ)" if scale != 1.0 else " (1σ)")


def resolve(spec, pc, idx, crs=None, depth=None, fill="iho"):
    """{"tvu": spec, "thu": spec or None} → dict(tvu, thu (1σ arrays), notes, missing counts).
    Points without a value (outside a table / raster) get the IHO-model value of spec['fill_order']
    when fill == 'iho', else NaN."""
    out = {"notes": []}
    for kind in ("tvu", "thu"):
        s = spec.get(kind)
        if not s:
            out[kind] = None
            continue
        v, desc = resolve_one(s, pc, idx, kind, crs, depth)
        miss = ~np.isfinite(v)
        out[f"{kind}_missing"] = int(miss.sum())
        if miss.any() and fill == "iho":
            from core.s44 import allowed_thu, allowed_tvu
            d = (depth if depth is not None else depth_of(pc.xyz[idx, 2]))[miss]
            o = spec.get("fill_order", "1a")
            v[miss] = (allowed_tvu(o, d) if kind == "tvu" else allowed_thu(o, d)) / Z95
            desc += f"; {miss.sum():,} points without a value → IHO Order {o} model"
        out[kind] = v
        out["notes"].append(f"{kind.upper()}: {desc}")
    return out
