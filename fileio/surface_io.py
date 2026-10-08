"""TIN / DEM exporters."""
from __future__ import annotations

import os
from datetime import datetime

import numpy as np

NODATA = -9999.0
TIN_FORMATS = {".xml": "LandXML (*.xml)", ".dxf": "DXF 3DFACE (*.dxf)", ".obj": "Wavefront OBJ (*.obj)",
               ".ply": "PLY binary (*.ply)"}
DEM_FORMATS = {".tif": "GeoTIFF (*.tif)", ".asc": "ESRI ASCII Grid (*.asc)", ".xyz": "XYZ grid points (*.xyz)"}


def _report(progress, f):
    if progress is not None:
        progress(min(max(f, 0.0), 1.0))


def _used(tin):
    """Only vertices referenced by triangles, re-indexed."""
    used = np.unique(tin.triangles)
    remap = np.full(len(tin.vertices), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return tin.vertices[used], remap[tin.triangles]


# ---------------------------------------------------------------- TIN
def write_landxml(tin, path, name="TIN", progress=None):
    V, F = _used(tin)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        now = datetime.now()
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2" '
                f'date="{now:%Y-%m-%d}" time="{now:%H:%M:%S}">\n')
        f.write('  <Units><Metric areaUnit="squareMeter" linearUnit="meter" volumeUnit="cubicMeter" '
                'temperatureUnit="celsius" pressureUnit="milliBars"/></Units>\n')
        f.write('  <Application name="PointCloud Studio"/>\n  <Surfaces>\n')
        f.write(f'    <Surface name="{name}">\n      <Definition surfType="TIN">\n        <Pnts>\n')
        ids = np.arange(1, len(V) + 1)
        # LandXML point order is Northing Easting Elevation
        for s in range(0, len(V), 500_000):
            blk = np.column_stack([ids[s:s + 500_000], V[s:s + 500_000, 1], V[s:s + 500_000, 0], V[s:s + 500_000, 2]])
            np.savetxt(f, blk, fmt='          <P id="%d">%.4f %.4f %.4f</P>')
            _report(progress, 0.5 * (s + len(blk)) / len(V))
        f.write("        </Pnts>\n        <Faces>\n")
        for s in range(0, len(F), 500_000):
            np.savetxt(f, F[s:s + 500_000] + 1, fmt="          <F>%d %d %d</F>")
            _report(progress, 0.5 + 0.5 * (s + 500_000) / len(F))
        f.write("        </Faces>\n      </Definition>\n    </Surface>\n  </Surfaces>\n</LandXML>\n")


def write_dxf(tin, path, layer="TIN", progress=None):
    V, F = _used(tin)
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("0\nSECTION\n2\nENTITIES\n")
        fmt = ("0\n3DFACE\n8\n" + layer + "\n"
               "10\n%.4f\n20\n%.4f\n30\n%.4f\n11\n%.4f\n21\n%.4f\n31\n%.4f\n"
               "12\n%.4f\n22\n%.4f\n32\n%.4f\n13\n%.4f\n23\n%.4f\n33\n%.4f")
        for s in range(0, len(F), 200_000):
            ff = F[s:s + 200_000]
            a, b, c = V[ff[:, 0]], V[ff[:, 1]], V[ff[:, 2]]
            np.savetxt(f, np.hstack([a, b, c, c]), fmt=fmt)
            _report(progress, (s + len(ff)) / len(F))
        f.write("0\nENDSEC\n0\nEOF\n")


def write_obj(tin, path, progress=None):
    V, F = _used(tin)
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("# PointCloud Studio TIN (world coordinates)\n")
        np.savetxt(f, V, fmt="v %.4f %.4f %.4f")
        _report(progress, 0.5)
        np.savetxt(f, F + 1, fmt="f %d %d %d")
    _report(progress, 1.0)


def write_ply(tin, path, progress=None):
    V, F = _used(tin)
    with open(path, "wb") as f:
        f.write(("ply\nformat binary_little_endian 1.0\ncomment PointCloud Studio TIN\n"
                 f"element vertex {len(V)}\nproperty double x\nproperty double y\nproperty double z\n"
                 f"element face {len(F)}\nproperty list uchar int vertex_indices\nend_header\n").encode())
        f.write(np.ascontiguousarray(V, dtype="<f8").tobytes())
        rec = np.empty(len(F), dtype=[("n", "u1"), ("i", "<i4", 3)])
        rec["n"] = 3
        rec["i"] = F
        f.write(rec.tobytes())
    _report(progress, 1.0)


def export_tin(tin, path, progress=None):
    ext = os.path.splitext(path)[1].lower()
    {".xml": write_landxml, ".dxf": write_dxf, ".obj": write_obj, ".ply": write_ply}[ext](
        tin, path, progress=progress)


# ---------------------------------------------------------------- DEM
def write_geotiff(dem, path, crs=None, progress=None):
    import rasterio
    from rasterio.transform import from_origin
    z = np.where(np.isnan(dem.z), NODATA, dem.z).astype(np.float32)
    profile = dict(driver="GTiff", width=z.shape[1], height=z.shape[0], count=1, dtype="float32",
                   nodata=NODATA, transform=from_origin(dem.x0, dem.ytop, dem.cell, dem.cell),
                   compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256)
    if crs is not None:
        profile["crs"] = crs
    if z.shape[0] < 256 or z.shape[1] < 256:
        profile.update(tiled=False)
        profile.pop("blockxsize")
        profile.pop("blockysize")
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(z, 1)
        tags = dict(method=dem.method, software="PointCloud Studio")
        if dem.has_true_position:
            tags.update(shoal_biased="true", shoalest="max Z" if dem.meta.get("shoal_high", True) else "min Z")
        ds.update_tags(**tags)
    _report(progress, 1.0)


def write_ascii_grid(dem, path, progress=None):
    z = np.where(np.isnan(dem.z), NODATA, dem.z)
    nr, nc = z.shape
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(f"ncols {nc}\nnrows {nr}\nxllcorner {dem.x0:.6f}\n"
                f"yllcorner {dem.ytop - nr * dem.cell:.6f}\ncellsize {dem.cell:.6f}\nNODATA_value {NODATA:g}\n")
        for s in range(0, nr, 2000):
            np.savetxt(f, z[s:s + 2000], fmt="%.3f")
            _report(progress, (s + 2000) / nr)


def write_xyz_grid(dem, path, progress=None):
    """Cell centres, or — for a shoalest/true-position DEM — the true XY of each selected sounding."""
    xs, ys = dem.centers()
    X, Y = np.meshgrid(xs, ys)
    ok = np.isfinite(dem.z)
    if dem.has_true_position:
        real = np.isfinite(dem.tx)
        X = np.where(real, dem.tx, X)      # hole-filled cells (no real sounding) fall back to the centre
        Y = np.where(real, dem.ty, Y)
    with open(path, "w", encoding="ascii", newline="\n") as f:
        np.savetxt(f, np.column_stack([X[ok], Y[ok], dem.z[ok]]), fmt="%.3f %.3f %.3f")
    _report(progress, 1.0)


def export_dem(dem, path, crs=None, progress=None):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".tif", ".tiff"):
        write_geotiff(dem, path, crs, progress)
    elif ext == ".asc":
        write_ascii_grid(dem, path, progress)
    else:
        write_xyz_grid(dem, path, progress)
