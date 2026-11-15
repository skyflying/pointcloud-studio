"""IHO S-102 Edition 3.0.0 (Bathymetric Surface) writer — HDF5 through h5py.

Layout and attribute types follow the S-102 3.0.0 product specification and GDAL's S102 writer (GDAL ≥ 3.13,
frmts/hdf5/s102dataset.cpp, MIT licence), so the file passes GDAL's validate_s102.py and opens in GDAL's S102
driver. One feature instance (BathymetryCoverage.01) with depth (positive down) and, optionally, uncertainty;
regular grid, cell-centre values, row 0 = south. Horizontal CRS must be EPSG:4326 or WGS 84 / UTM (326xx,
327xx) — reproject first (core.nautical.product_grid)."""
from __future__ import annotations

import datetime as _dt
import os

import numpy as np

NODATA = 1000000.0
PRODUCT = "INT.IHO.S-102.3.0.0"
MIN_DEPTH, MAX_DEPTH = -14.0, 11050.0


def allowed_epsg(epsg):
    return epsg in (4326, 5041, 5042) or 32601 <= epsg <= 32660 or 32701 <= epsg <= 32760


def product_name(code="TW00", name="CABLE"):
    """S-102 file name: 102 + 4-character producer code + up to 12 characters (validator pattern)."""
    import re
    code = (re.sub(r"[^A-Za-z0-9]", "", code or "") + "XXXX")[:4]
    name = re.sub(r"[^A-Za-z0-9_]", "_", name or "DATA")[:12] or "DATA"
    return f"102{code}{name}.h5"


def _vstr(h5py):
    return h5py.string_dtype(encoding="ascii")


def _enum(h5py, names, base="u1"):
    return h5py.enum_dtype({n: v for v, n in names if n}, basetype=base)


def write_s102(path, elev, unc, transform, epsg, vertical_datum, issue_date=None, vertical_uncertainty=-1.0,
               horizontal_uncertainty=-1.0, block=100, progress=None):
    """elev: (rows, cols) elevation (up +, NaN = no data) on a north-up grid with rasterio-style `transform`
    (origin top-left, pixel corners). unc: same shape (95 % vertical uncertainty, m) or None.
    vertical_datum: S-100 vertical datum code (1–30 or 44). Returns a list of warnings."""
    import h5py
    from pyproj import CRS, Transformer
    epsg = int(epsg)
    if not allowed_epsg(epsg):
        raise ValueError(f"S-102 needs EPSG:4326 or a WGS 84 / UTM zone (326xx, 327xx), not EPSG:{epsg}")
    vd = int(vertical_datum)
    if not (1 <= vd <= 30 or vd == 44):
        raise ValueError("S-102 vertical datum must be an S-100 code 1–30 or 44")
    warnings = []
    elev = np.asarray(elev, np.float32)
    rows, cols = elev.shape
    a, e = float(transform.a), float(transform.e)
    if abs(transform.b) > 1e-12 or abs(transform.d) > 1e-12 or e >= 0:
        raise ValueError("The grid must be north-up without rotation")
    dx, dy = a, -e
    # pixel corner → pixel centre; S-102 grids start in the south-west
    min_x = transform.c + dx / 2.0
    max_y = transform.f - dy / 2.0
    min_y = max_y - (rows - 1) * dy
    max_x = min_x + (cols - 1) * dx
    # centimetric values (S-102: precision not better than 1 cm); depths rounded towards SHOALER, uncertainty up
    dd = np.floor(-elev.astype(np.float64) * 100.0 + 1e-6) / 100.0
    depth = np.where(np.isfinite(elev), dd, NODATA).astype(np.float32)[::-1]          # row 0 = south
    if unc is not None:
        u = np.ceil(np.asarray(unc, np.float64) * 100.0 - 1e-6) / 100.0
        u = u.astype(np.float32)[::-1]
        u = np.where(np.isfinite(u) & (depth != NODATA), u, NODATA).astype(np.float32)
        if (u[u != NODATA] < 0).any():
            raise ValueError("Negative uncertainty values")
    valid = depth != NODATA
    dmin = float(depth[valid].min()) if valid.any() else NODATA
    dmax = float(depth[valid].max()) if valid.any() else NODATA
    if valid.any() and not (dmin >= MIN_DEPTH and dmax <= MAX_DEPTH):
        warnings.append(f"Depths {dmin:.2f}…{dmax:.2f} m are outside the S-102 range [{MIN_DEPTH:g}, {MAX_DEPTH:g}]")
    crs = CRS.from_epsg(epsg)
    geographic = crs.is_geographic
    # extent in WGS 84 longitude / latitude (of the pixel corners)
    to_ll = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    ex = np.array([transform.c, transform.c + cols * dx])
    ey = np.array([transform.f - rows * dy, transform.f])
    gx, gy = np.meshgrid(np.linspace(ex[0], ex[1], 21), np.linspace(ey[0], ey[1], 21))
    lon, lat = to_ll.transform(gx.ravel(), gy.ravel())
    w_lon, e_lon, s_lat, n_lat = float(np.min(lon)), float(np.max(lon)), float(np.min(lat)), float(np.max(lat))
    issue = issue_date or _dt.date.today().strftime("%Y%m%d")
    vs = _vstr(h5py)
    if os.path.exists(path):
        os.remove(path)
    with h5py.File(path, "w") as f:
        A = f.attrs
        A.create("productSpecification", PRODUCT, dtype=vs)
        A.create("issueDate", issue, dtype=vs)
        A.create("horizontalCRS", epsg, dtype="<i4")
        for k, v in (("westBoundLongitude", w_lon), ("southBoundLatitude", s_lat), ("eastBoundLongitude", e_lon),
                     ("northBoundLatitude", n_lat)):
            A.create(k, v, dtype="<f4")
        A.create("verticalCS", 6498, dtype="<i4")                                  # depth, metre, down
        A.create("verticalCoordinateBase", 2,
                 dtype=_enum(h5py, [(1, "seaSurface"), (2, "verticalDatum"), (3, "seaBottom")]))
        A.create("verticalDatumReference", 1, dtype=_enum(h5py, [(1, "s100VerticalDatum"), (2, "EPSG")]))
        A.create("verticalDatum", vd, dtype="<u2")

        g = f.create_group("BathymetryCoverage")
        G = g.attrs
        G.create("commonPointRule", 2, dtype=_enum(h5py, [(1, "average"), (2, "low"), (3, "high"), (4, "all")]))
        G.create("dataCodingFormat", 2, dtype=_enum(h5py, list(enumerate(
            ["fixedStations", "regularGrid", "ungeorectifiedGrid", "movingPlatform", "irregularGrid",
             "variableCellSize", "TIN", "stationwiseFixed", "featureOrientedRegularGrid"], start=1))))
        G.create("dataOffsetCode", 5, dtype=_enum(h5py, list(enumerate(
            ['XMin, YMin ("Lower left") corner ("Cell origin")', 'XMax, YMax ("Upper right") corner',
             'XMax, YMin ("Lower right") corner', 'XMin, YMax ("Upper left") corner',
             "Barycenter (centroid) of cell"], start=1))))
        G.create("dimension", 2, dtype="u1")
        G.create("horizontalPositionUncertainty", float(horizontal_uncertainty), dtype="<f4")
        G.create("verticalUncertainty", float(vertical_uncertainty), dtype="<f4")
        G.create("interpolationType", 1, dtype=_enum(h5py, [(1, "nearestneighbor"), (5, "bilinear"),
                                                            (6, "biquadratic"), (7, "bicubic"), (9, "barycentric"),
                                                            (10, "discrete")]))
        G.create("numInstances", 1, dtype="u1")
        G.create("sequencingRule.scanDirection", "Longitude, Latitude" if geographic else "Easting, Northing",
                 dtype=vs)
        G.create("sequencingRule.type", 1, dtype=_enum(h5py, list(enumerate(
            ["linear", "boustrophedonic", "CantorDiagonal", "spiral", "Morton", "Hilbert"], start=1))))
        g.create_dataset("axisNames", data=np.array(["Latitude", "Longitude"] if geographic
                                                    else ["Easting", "Northing"], dtype=object), dtype=vs)

        fi = g.create_group("BathymetryCoverage.01")
        F = fi.attrs
        for k, v in (("westBoundLongitude", min_x), ("southBoundLatitude", min_y), ("eastBoundLongitude", max_x),
                     ("northBoundLatitude", max_y)):
            F.create(k, v, dtype="<f4")
        F.create("gridOriginLongitude", min_x, dtype="<f8")
        F.create("gridOriginLatitude", min_y, dtype="<f8")
        F.create("gridSpacingLongitudinal", dx, dtype="<f8")
        F.create("gridSpacingLatitudinal", dy, dtype="<f8")
        F.create("numPointsLongitudinal", cols, dtype="<u4")
        F.create("numPointsLatitudinal", rows, dtype="<u4")
        F.create("startSequence", "0,0", dtype=vs)
        F.create("numGRP", 1, dtype="u1")

        grp = fi.create_group("Group_001")
        grp.attrs.create("timePoint", "00010101T000000Z", dtype=vs)
        fields = [("depth", "<f4")] + ([("uncertainty", "<f4")] if unc is not None else [])
        cdt = np.dtype(fields)
        fill = np.zeros((), dtype=cdt)
        for n_, _t in fields:
            fill[n_] = NODATA
        b = int(min(max(block, 100), 4096))
        ds = grp.create_dataset("values", shape=(rows, cols), dtype=cdt, chunks=(min(rows, b), min(cols, b)),
                                compression="gzip", compression_opts=6, fillvalue=fill)
        step = max(1, 2_000_000 // max(cols, 1))
        for r0 in range(0, rows, step):
            r1 = min(rows, r0 + step)
            buf = np.empty((r1 - r0, cols), dtype=cdt)
            buf["depth"] = depth[r0:r1]
            if unc is not None:
                buf["uncertainty"] = u[r0:r1]
            ds[r0:r1] = buf
            if progress:
                progress(r1 / rows)
        grp.attrs.create("minimumDepth", dmin, dtype="<f4")
        grp.attrs.create("maximumDepth", dmax, dtype="<f4")
        if unc is not None:
            uv = u[u != NODATA]
            grp.attrs.create("minimumUncertainty", float(uv.min()) if len(uv) else NODATA, dtype="<f4")
            grp.attrs.create("maximumUncertainty", float(uv.max()) if len(uv) else NODATA, dtype="<f4")
        else:
            grp.attrs.create("minimumUncertainty", NODATA, dtype="<f4")
            grp.attrs.create("maximumUncertainty", NODATA, dtype="<f4")

        gf = f.create_group("Group_F")
        gf.create_dataset("featureCode", data=np.array(["BathymetryCoverage"], dtype=object), dtype=vs)
        rec = np.dtype([(n_, vs) for n_ in ("code", "name", "uom.name", "fillValue", "datatype", "lower", "upper",
                                             "closure")])
        rows_f = [("depth", "depth", "metres", "1000000", "H5T_FLOAT", str(int(MIN_DEPTH)), str(int(MAX_DEPTH)),
                   "closedInterval"),
                  ("uncertainty", "uncertainty", "metres", "1000000", "H5T_FLOAT", "0", "", "geSemiInterval")]
        rows_f = rows_f[:len(fields)]
        gf.create_dataset("BathymetryCoverage", data=np.array(rows_f, dtype=rec))
        gf.create_dataset("QualityOfBathymetryCoverage",
                          data=np.array([("iD", "ID", "", "0", "H5T_INTEGER", "1", "", "geSemiInterval")], dtype=rec))
    return warnings
