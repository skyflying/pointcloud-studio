"""Navigation (chart) products from soundings and grids — always on the safe (shoal) side.

* product grids for BAG / S-102: reprojection / coarsening keeps the SHOALEST value of the source cells
* sounding selection: shoalest first, none closer than a radius (on the chart, at the chart scale)
* depth contours (DEPCNT) and depth areas (DEPARE) from a shoal-biased surface: before contouring the surface
  is raised to its local maximum (elevation, up +), so generalisation moves contours into deeper water,
  never into shallower water.

Heights are elevations (up +) relative to the vertical datum of the grid; depths = −elevation."""
from __future__ import annotations

import math

import numpy as np

STANDARD_DEPTHS = (0.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 100.0, 200.0, 500.0, 1000.0)


def _report(progress, f):
    if progress:
        progress(min(max(f, 0.0), 1.0))


# ---------------------------------------------------------------- CRS for S-100 products
def utm_epsg(lon, lat):
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def lonlat_center(dem, crs):
    from pyproj import CRS, Transformer
    xs, ys = dem.centers()
    cx, cy = float(np.mean([xs[0], xs[-1]])), float(np.mean([ys[0], ys[-1]]))
    if crs is None:
        raise ValueError("The project has no coordinate system — set one first")
    t = Transformer.from_crs(CRS.from_user_input(crs), CRS.from_epsg(4326), always_xy=True)
    return t.transform(cx, cy)


def shoal_block(z, factor, high=True):
    """Coarsen by an integer factor keeping the shoalest cell of each block (NaN ignored)."""
    f = int(factor)
    if f <= 1:
        return z.copy()
    r, c = z.shape
    R, C = -(-r // f), -(-c // f)
    pad = np.full((R * f, C * f), np.nan, np.float32)
    pad[:r, :c] = z
    blk = pad.reshape(R, f, C, f)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return (np.nanmax(blk, axis=(1, 3)) if high else np.nanmin(blk, axis=(1, 3))).astype(np.float32)


def product_grid(dem, unc, src_crs, dst_epsg=None, cell=None, progress=None):
    """Grid for a product: dem (elevation, up +) and unc (95 % vertical uncertainty, same shape, or None)
    reprojected to EPSG dst_epsg (None = keep the CRS) at `cell` (None = about the source cell).
    Elevation uses the MAXIMUM of the source cells falling in a target cell (shoalest), uncertainty the
    maximum too (conservative). Returns dict(z, unc, transform, crs (rasterio CRS), cell, epsg)."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    from pyproj import CRS
    src = rasterio.crs.CRS.from_wkt(CRS.from_user_input(src_crs).to_wkt())
    tr0 = from_origin(dem.x0, dem.ytop, dem.cell, dem.cell)
    rows, cols = dem.z.shape
    z0 = dem.z.astype(np.float32)
    u0 = None if unc is None else np.asarray(unc, np.float32)
    if dst_epsg is None:
        dst = src
        if cell is None or abs(cell - dem.cell) < 1e-9:
            return {"z": z0.copy(), "unc": None if u0 is None else u0.copy(), "transform": tr0, "crs": dst,
                    "cell": dem.cell, "epsg": CRS.from_user_input(src_crs).to_epsg()}
        f = max(1, int(round(cell / dem.cell)))
        z = shoal_block(z0, f, True)
        u = None if u0 is None else shoal_block(u0, f, True)
        return {"z": z, "unc": u, "transform": from_origin(dem.x0, dem.ytop, dem.cell * f, dem.cell * f),
                "crs": dst, "cell": dem.cell * f, "epsg": CRS.from_user_input(src_crs).to_epsg()}
    dst = rasterio.crs.CRS.from_epsg(int(dst_epsg))
    left, top = dem.x0, dem.ytop
    right, bottom = left + cols * dem.cell, top - rows * dem.cell
    if dst.is_geographic and cell is None:
        cell_dst = None
    else:
        cell_dst = cell
    tr, w, h = calculate_default_transform(src, dst, cols, rows, left, bottom, right, top,
                                           resolution=(cell_dst, cell_dst) if cell_dst else None)
    _report(progress, 0.2)
    z = np.full((h, w), np.nan, np.float32)
    reproject(z0, z, src_transform=tr0, src_crs=src, dst_transform=tr, dst_crs=dst,
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.max)
    _report(progress, 0.6)
    u = None
    if u0 is not None:
        u = np.full((h, w), np.nan, np.float32)
        reproject(u0, u, src_transform=tr0, src_crs=src, dst_transform=tr, dst_crs=dst,
                  src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.max)
        u[~np.isfinite(z)] = np.nan
    _report(progress, 1.0)
    return {"z": z, "unc": u, "transform": tr, "crs": dst, "cell": float(abs(tr.a)), "epsg": int(dst_epsg)}


def iho_uncertainty(z, order="1a"):
    """95 % TVU allowed by an IHO S-44 order at the depth of each cell (used when no uncertainty grid exists)."""
    from core.s44 import allowed_tvu
    return np.asarray(allowed_tvu(order, np.abs(np.asarray(z, float))), np.float32)


# ---------------------------------------------------------------- sounding selection (SOUNDG)
def select_soundings(x, y, z, radius, high=True, max_candidates=3_000_000, progress=None):
    """Shoal-biased selection: soundings are taken shoalest first; one closer than `radius` to an already kept
    sounding is dropped — so every dropped sounding has a kept sounding at least as shoal within `radius`.
    Above 300 000 soundings, candidates are first reduced to the shoalest per radius/4 cell (the guarantee then
    holds within 1.36 × radius). Returns the indices of the selected soundings."""
    x, y, z = (np.asarray(a, float) for a in (x, y, z))
    ok = np.flatnonzero(np.isfinite(x) & np.isfinite(y) & np.isfinite(z))
    if len(ok) == 0 or radius <= 0:
        return ok
    sgn = -1.0 if high else 1.0                                  # sort shoalest first
    cand = ok
    if len(ok) > 300_000:
        c = radius / 4.0
        ix = np.floor((x[ok] - x[ok].min()) / c).astype(np.int64)
        iy = np.floor((y[ok] - y[ok].min()) / c).astype(np.int64)
        key = iy * (int(ix.max()) + 1) + ix
        o = np.lexsort((sgn * z[ok], key))
        first = np.r_[True, key[o][1:] != key[o][:-1]]
        cand = ok[o[first]]
    cand = cand[np.argsort(sgn * z[cand], kind="stable")]
    if len(cand) > max_candidates:
        raise ValueError("Too many soundings for this radius — use a larger radius")
    _report(progress, 0.3)
    # greedy suppression with a hash grid of cell = radius
    r2 = radius * radius
    xc, yc = x[cand], y[cand]
    gx = np.floor((xc - xc.min()) / radius).astype(np.int64)
    gy = np.floor((yc - yc.min()) / radius).astype(np.int64)
    cells = {}
    keep = []
    n = len(cand)
    for k in range(n):
        cx, cy = gx[k], gy[k]
        px, py = xc[k], yc[k]
        hit = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                lst = cells.get((cx + dx, cy + dy))
                if lst:
                    for j in lst:
                        if (xc[j] - px) ** 2 + (yc[j] - py) ** 2 < r2:
                            hit = True
                            break
                if hit:
                    break
            if hit:
                break
        if not hit:
            keep.append(k)
            cells.setdefault((cx, cy), []).append(k)
        if progress and k % 20000 == 0:
            _report(progress, 0.3 + 0.7 * k / n)
    _report(progress, 1.0)
    return cand[np.asarray(keep, dtype=np.int64)]


# ---------------------------------------------------------------- contours and depth areas
def safety_surface(dem_z, bias_cells=1):
    """Shoal-biased surface for generalisation: local MAXIMUM of the elevation over (2k+1)² cells."""
    from scipy import ndimage
    z = np.asarray(dem_z, float)
    if bias_cells <= 0:
        return z
    ok = np.isfinite(z)
    lo = np.nanmin(z) if ok.any() else 0.0
    m = ndimage.maximum_filter(np.where(ok, z, lo - 1e6), size=2 * int(bias_cells) + 1, mode="nearest")
    return np.where(ok, m, np.nan)


def depth_levels(z_elev, levels=STANDARD_DEPTHS):
    """Depth contour values (positive down) that fall inside the depth range of the grid."""
    zf = np.asarray(z_elev, float)
    zf = zf[np.isfinite(zf)]
    if not len(zf):
        return []
    dmin, dmax = -zf.max(), -zf.min()
    return [float(d) for d in levels if dmin < d < dmax]


def depth_contours(dem, levels=STANDARD_DEPTHS, bias_cells=1, simplify_tol=0.0, min_length=0.0):
    """DEPCNT: lines at the given depths (VALDCO = depth, positive down) from the shoal-biased surface."""
    import contourpy

    from core.contours import simplify
    z = safety_surface(dem.z, bias_cells)
    rows, cols = z.shape
    xs = dem.x0 + (np.arange(cols) + 0.5) * dem.cell
    ys = dem.ytop - (np.arange(rows) + 0.5) * dem.cell
    gen = contourpy.contour_generator(xs, ys[::-1], np.ma.masked_invalid(z[::-1]),
                                      line_type=contourpy.LineType.Separate)
    feats = []
    for d in depth_levels(z, levels):
        for seg in gen.lines(-d):
            seg = np.asarray(seg, float)
            if len(seg) < 2:
                continue
            seg = simplify(seg, simplify_tol)
            if min_length > 0 and np.hypot(*np.diff(seg, axis=0).T).sum() < min_length:
                continue
            feats.append({"type": "LineString", "parts": [np.column_stack([seg, np.full(len(seg), -d)])],
                          "attrs": {"OBJL": "DEPCNT", "VALDCO": d}})
    return feats


def depth_areas(dem, levels=STANDARD_DEPTHS, bias_cells=1, simplify_tol=0.0):
    """DEPARE: polygons between consecutive depth levels (DRVAL1 shallow bound, DRVAL2 deep bound), from the
    shoal-biased surface. The shallowest area starts at the grid's shoalest depth (negative = drying)."""
    from rasterio import features
    from rasterio.transform import from_origin

    from core.contours import simplify
    z = safety_surface(dem.z, bias_cells)
    ok = np.isfinite(z)
    if not ok.any():
        return []
    depth = -z
    inside = depth_levels(z, levels)
    edges = [float(np.nanmin(depth))] + inside + [float(np.nanmax(depth))]
    cls = np.zeros(z.shape, np.int32)
    for k in range(len(edges) - 1):
        lo, hi = edges[k], edges[k + 1]
        m = ok & (depth >= lo) & ((depth < hi) if k < len(edges) - 2 else (depth <= hi))
        cls[m] = k + 1
    tr = from_origin(dem.x0, dem.ytop, dem.cell, dem.cell)
    feats = []
    for geom, val in features.shapes(cls, mask=cls > 0, transform=tr, connectivity=4):
        k = int(val) - 1
        rings = [np.asarray(r, float) for r in geom["coordinates"]]
        rings = [simplify(r, simplify_tol) if simplify_tol > 0 else r for r in rings]
        rings = [r for r in rings if len(r) >= 4]
        if not rings:
            continue
        d1, d2 = edges[k], edges[k + 1]
        feats.append({"type": "Polygon", "parts": rings,
                      "attrs": {"OBJL": "DEPARE", "DRVAL1": round(d1, 2), "DRVAL2": round(d2, 2)}})
    return feats


def soundings_features(x, y, z, sel, datum_label=""):
    """SOUNDG points: VALSOU = depth (positive down) TRUNCATED to 0.1 m towards shoaler (12.37 → 12.3; a
    drying height −0.42 → −0.5), as charted depths are never deeper than measured; Z = elevation."""
    out = []
    for i in sel:
        d = -float(z[i])
        out.append({"type": "Point", "parts": [np.array([[x[i], y[i], z[i]]])],
                    "attrs": {"OBJL": "SOUNDG", "VALSOU": math.floor(d * 10.0 + 1e-6) / 10.0,
                              "VERDAT": datum_label}})
    return out
