"""DEM / grid import: GeoTIFF, ESRI ASCII Grid, regular XYZ grid → core.surface.Dem (north-up, square cells)."""
from __future__ import annotations

import os

import numpy as np

from core.surface import Dem

GRID_EXT = (".tif", ".tiff", ".asc", ".grd", ".xyz", ".txt", ".csv")


def is_esri_ascii(path):
    try:
        with open(path, "r", encoding="ascii", errors="ignore") as f:
            return f.readline().strip().lower().startswith("ncols")
    except OSError:
        return False


def read_raster(path, progress=None):
    """GeoTIFF / ESRI ASCII (any GDAL raster). Returns Dem with meta['crs'] (pyproj CRS or None)."""
    import rasterio
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    with rasterio.open(path) as ds:
        z = ds.read(1, masked=True).astype(np.float32).filled(np.nan)
        tr = ds.transform
        src_crs = ds.crs
        nodata = ds.nodata
        if nodata is not None:
            z[z == np.float32(nodata)] = np.nan
        # non north-up or non-square pixels → resample to a square north-up grid
        if abs(tr.b) > 1e-12 or abs(tr.d) > 1e-12 or abs(abs(tr.a) - abs(tr.e)) > 1e-9 * abs(tr.a) or tr.e > 0:
            cell = min(abs(tr.a), abs(tr.e)) if tr.b == 0 else abs(tr.a)
            dst_tr, w, h = calculate_default_transform(src_crs, src_crs, ds.width, ds.height, *ds.bounds,
                                                       resolution=(cell, cell))
            out = np.full((h, w), np.nan, np.float32)
            reproject(z, out, src_transform=tr, src_crs=src_crs, dst_transform=dst_tr, dst_crs=src_crs,
                      src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
            z, tr = out, dst_tr
    crs = None
    if src_crs is not None:
        try:
            from pyproj import CRS
            crs = CRS.from_user_input(src_crs.to_wkt())
        except Exception:
            crs = None
    dem = Dem(z, float(tr.c), float(tr.f), float(abs(tr.a)), "imported",
              meta={"crs": crs, "path": path, "source": os.path.basename(path)})
    if progress:
        progress(1.0)
    return dem


def read_xyz_grid(path, skip_rows, delim_key, column_roles, encoding=None, progress=None, z_is_depth=False):
    """Regular grid stored as X Y Z points → Dem. Cell size = most common spacing of unique X."""
    from fileio.xyz_reader import read_xyz
    pc = read_xyz(path, skip_rows, delim_key, column_roles, encoding, z_is_depth=z_is_depth)
    x, y, z = pc.xyz[:, 0], pc.xyz[:, 1], pc.xyz[:, 2]

    def spacing(v):
        u = np.unique(np.round(v, 6))
        d = np.diff(u)
        d = d[d > 1e-9]
        if len(d) == 0:
            raise ValueError("Cannot determine grid spacing")
        vals, counts = np.unique(np.round(d, 6), return_counts=True)
        return float(vals[np.argmax(counts)])

    cell = min(spacing(x), spacing(y))
    x0, ytop = x.min() - cell / 2, y.max() + cell / 2
    nc = int(round((x.max() - x.min()) / cell)) + 1
    nr = int(round((y.max() - y.min()) / cell)) + 1
    if nc * nr > 200_000_000:
        raise ValueError(f"Grid too large ({nc}×{nr}) — is this really a regular grid?")
    col = np.clip(np.round((x - x0) / cell - 0.5).astype(np.int64), 0, nc - 1)
    row = np.clip(np.round((ytop - y) / cell - 0.5).astype(np.int64), 0, nr - 1)
    grid = np.full((nr, nc), np.nan, np.float32)
    grid[row, col] = z
    if progress:
        progress(1.0)
    return Dem(grid, float(x0), float(ytop), float(cell), "imported",
               meta={"crs": None, "path": path, "source": os.path.basename(path),
                     "filled": float(np.isfinite(grid).mean())})


def reproject_dem(dem, src_crs, dst_crs):
    """Warp a Dem into another CRS (bilinear), keeping roughly the same resolution."""
    import rasterio
    from rasterio.crs import CRS as RCRS
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    nr, nc = dem.z.shape
    s = RCRS.from_wkt(src_crs.to_wkt())
    d = RCRS.from_wkt(dst_crs.to_wkt())
    tr = from_origin(dem.x0, dem.ytop, dem.cell, dem.cell)
    bounds = (dem.x0, dem.ytop - nr * dem.cell, dem.x0 + nc * dem.cell, dem.ytop)
    dst_tr, w, h = calculate_default_transform(s, d, nc, nr, *bounds)
    cell = abs(dst_tr.a)
    dst_tr, w, h = calculate_default_transform(s, d, nc, nr, *bounds, resolution=(cell, cell))
    out = np.full((h, w), np.nan, np.float32)
    reproject(dem.z, out, src_transform=tr, src_crs=s, dst_transform=dst_tr, dst_crs=d,
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
    meta = dict(dem.meta, reprojected_from=str(src_crs.name))
    return Dem(out, float(dst_tr.c), float(dst_tr.f), float(abs(dst_tr.a)), dem.method, meta=meta)
