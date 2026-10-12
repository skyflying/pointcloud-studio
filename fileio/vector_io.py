"""Vector files: Shapefile, GeoPackage, GeoJSON, KML / KMZ, GPX and DXF — read and write.

Pure Python (pyshp for Shapefiles, the standard library for the rest): no GDAL / OGR, so the
program stays small and no second copy of PROJ / GDAL libraries is bundled.

A feature is a dict
    {"type": "Point" | "LineString" | "Polygon", "parts": [array (N, 2|3)], "attrs": {...}}
Multi-geometries are split into one feature per part (polygons: one feature per outer ring, holes
kept as further parts of that feature). Coordinates are x / y (/ z) in the file's CRS; geographic
formats (GeoJSON, KML, GPX) are lon / lat.

read(path, layer=None) -> (features, crs or None, info)
layers(path)           -> list of layer names (GeoPackage tables, KML folders, DXF layers; else [])
write(path, features, crs=None, fields=None, layer="layer") -> format chosen by the extension
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import struct
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

READ_EXT = (".shp", ".gpkg", ".geojson", ".json", ".kml", ".kmz", ".gpx", ".dxf")
WRITE_EXT = (".shp", ".gpkg", ".geojson", ".kml", ".dxf")
READ_FILTER = ("Vector files (*.shp *.gpkg *.geojson *.json *.kml *.kmz *.gpx *.dxf);;Shapefile (*.shp);;"
               "GeoPackage (*.gpkg);;GeoJSON (*.geojson *.json);;KML / KMZ (*.kml *.kmz);;GPX (*.gpx);;DXF (*.dxf)")
WRITE_FILTER = ("Shapefile (*.shp);;GeoPackage (*.gpkg);;GeoJSON (*.geojson);;KML — Google Earth (*.kml);;"
                "DXF (*.dxf)")
GEOGRAPHIC = (".geojson", ".json", ".kml", ".kmz", ".gpx")


def is_vector(path):
    return path.lower().endswith(READ_EXT)


def ext_from_filter(path, flt):
    """Add the extension of the chosen file-dialog filter when the name has none of ours."""
    if path.lower().endswith(WRITE_EXT):
        return path
    m = re.search(r"\*(\.\w+)", flt or "")
    return path + (m.group(1) if m else ".gpkg")


# ================================================================ helpers
def _arr(coords):
    a = np.asarray(coords, dtype=np.float64)
    return a.reshape(-1, a.shape[-1]) if a.size else np.empty((0, 2))


def _same(a, b):
    """Exactly the same vertex (np.allclose has a relative tolerance of ~26 m at TWD97 coordinates)."""
    return bool(np.array_equal(np.asarray(a)[:2], np.asarray(b)[:2]))


def _signed_area(r):
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(np.sum(x[:-1] * y[1:] - x[1:] * y[:-1]) + x[-1] * y[0] - x[0] * y[-1])


def _polys_from_rings(rings, attrs, outer_is_cw):
    """Group rings into polygons: a ring with the outer orientation starts a new polygon."""
    out = []
    for r in rings:
        if len(r) < 3:
            continue
        cw = _signed_area(r) < 0
        if not out or cw == outer_is_cw:
            out.append({"type": "Polygon", "parts": [r], "attrs": dict(attrs)})
        else:
            out[-1]["parts"].append(r)
    return out


def _crs_epsg(code):
    from core import crs as crsmod
    try:
        return crsmod.from_epsg(int(code))
    except Exception:  # noqa: BLE001
        return None


def _wgs84():
    return _crs_epsg(4326)


# ================================================================ Shapefile
def _read_shp(path):
    import shapefile

    from core import crs as crsmod
    r = shapefile.Reader(path)
    fields = [f[0] for f in r.fields[1:]]
    feats = []
    for sr in r.iterShapeRecords():
        sh = sr.shape
        if not sh.points:
            continue
        attrs = dict(zip(fields, list(sr.record)))
        pts = np.asarray(sh.points, float)[:, :2]
        if getattr(sh, "z", None) is not None and len(sh.z) == len(pts):
            pts = np.column_stack([pts, np.asarray(sh.z, float)])
        parts = list(sh.parts) + [len(pts)]
        rings = [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
        t = sh.shapeType
        if t in (shapefile.POINT, shapefile.POINTZ, shapefile.POINTM, shapefile.MULTIPOINT, shapefile.MULTIPOINTZ,
                 shapefile.MULTIPOINTM):
            feats += [{"type": "Point", "parts": [p[None, :]], "attrs": dict(attrs)} for p in pts]
        elif t in (shapefile.POLYLINE, shapefile.POLYLINEZ, shapefile.POLYLINEM):
            feats += [{"type": "LineString", "parts": [p], "attrs": dict(attrs)} for p in rings if len(p) >= 2]
        elif t in (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
            feats += _polys_from_rings(rings, attrs, outer_is_cw=True)       # shapefile: outer rings clockwise
    prj = os.path.splitext(path)[0] + ".prj"
    crs = crsmod.guess_from_prj(prj) if os.path.isfile(prj) else None
    return feats, crs


def _write_shp(path, feats, crs, fields):
    import shapefile
    gt = {f["type"] for f in feats} or {"LineString"}
    if len(gt) > 1:
        raise ValueError("A shapefile holds one geometry type — export points, lines and polygons separately")
    g = gt.pop()
    has_z = any(p.shape[1] >= 3 for f in feats for p in f["parts"])
    st = {"Point": (shapefile.POINTZ, shapefile.POINT), "LineString": (shapefile.POLYLINEZ, shapefile.POLYLINE),
          "Polygon": (shapefile.POLYGONZ, shapefile.POLYGON)}[g][0 if has_z else 1]
    base = os.path.splitext(path)[0]
    w = shapefile.Writer(base, shapeType=st, encoding="utf-8")
    names = _fields(feats, fields)
    short = {}
    for n, typ in names:
        s = re.sub(r"[^A-Za-z0-9_]", "_", n)[:10] or "F"
        k = 1
        while s in short.values():
            s = f"{s[:8]}{k:02d}"
            k += 1
        short[n] = s
        if typ == "N":
            w.field(s, "N", 18, 6)
        else:
            w.field(s, "C", 254)
    for f in feats:
        parts = [p if p.shape[1] >= 3 or not has_z else np.column_stack([p, np.zeros(len(p))]) for p in f["parts"]]
        if g == "Point":
            p = parts[0][0]
            (w.pointz(*p[:3]) if has_z else w.point(*p[:2]))
        elif g == "LineString":
            (w.linez if has_z else w.line)([[tuple(q) for q in p] for p in parts])
        else:
            rings = []
            for k, p in enumerate(parts):
                cw = _signed_area(p) < 0
                if (k == 0) != cw:                       # outer clockwise, holes counter-clockwise
                    p = p[::-1]
                if not _same(p[0], p[-1]):
                    p = np.vstack([p, p[:1]])
                rings.append([tuple(q) for q in p])
            (w.polyz if has_z else w.poly)(rings)
        w.record(*[_cell(f["attrs"].get(n), t) for n, t in names])
    w.close()
    if crs is not None:
        with open(base + ".prj", "w", encoding="ascii", errors="ignore") as fh:
            fh.write(crs.to_wkt("WKT1_ESRI"))


# ================================================================ WKB (GeoPackage)
def _parse_wkb(b, off=0):
    """→ (list of (type, [arrays]), new offset). Handles ISO (+1000/2000/3000) and EWKB-style flags."""
    bo = "<" if b[off] == 1 else ">"
    t = struct.unpack_from(bo + "I", b, off + 1)[0]
    off += 5
    zf = mf = False
    if t & 0x80000000:
        zf = True
    if t & 0x40000000:
        mf = True
    t &= 0x0FFFFFFF
    if t >= 3000:
        zf = mf = True
        t -= 3000
    elif t >= 2000:
        mf = True
        t -= 2000
    elif t >= 1000:
        zf = True
        t -= 1000
    dim = 2 + zf + mf

    def pts(n):
        nonlocal off
        a = np.frombuffer(b, dtype=bo + "f8", count=n * dim, offset=off).reshape(n, dim)
        off += 8 * n * dim
        return a[:, :3] if zf else a[:, :2]

    if t == 1:
        return [("Point", [pts(1)])], off
    if t == 2:
        n = struct.unpack_from(bo + "I", b, off)[0]
        off += 4
        return [("LineString", [pts(n)])], off
    if t == 3:
        nr = struct.unpack_from(bo + "I", b, off)[0]
        off += 4
        rings = []
        for _ in range(nr):
            n = struct.unpack_from(bo + "I", b, off)[0]
            off += 4
            rings.append(pts(n))
        return [("Polygon", rings)], off
    if t in (4, 5, 6, 7):
        n = struct.unpack_from(bo + "I", b, off)[0]
        off += 4
        out = []
        for _ in range(n):
            g, off = _parse_wkb(b, off)
            out += g
        return out, off
    raise ValueError(f"Unsupported WKB geometry type {t}")


def _wkb(gtype, parts):
    z = parts[0].shape[1] >= 3
    code = {"Point": 1, "LineString": 2, "Polygon": 3}[gtype] + (1000 if z else 0)
    out = [struct.pack("<BI", 1, code)]

    def put(a):
        a = np.asarray(a, "<f8")[:, :3 if z else 2]
        out.append(np.ascontiguousarray(a).tobytes())

    if gtype == "Point":
        put(parts[0][:1])
    elif gtype == "LineString":
        out.append(struct.pack("<I", len(parts[0])))
        put(parts[0])
    else:
        out.append(struct.pack("<I", len(parts)))
        for k, r in enumerate(parts):
            if (k == 0) == (_signed_area(r) < 0):         # GeoPackage / OGC: outer counter-clockwise
                r = r[::-1]
            if not _same(r[0], r[-1]):
                r = np.vstack([r, r[:1]])
            out.append(struct.pack("<I", len(r)))
            put(r)
    return b"".join(out)


def _ro_uri(path):
    """Read-only SQLite URI with the path properly percent-encoded (spaces, #, %, ?, non-ASCII names and
    Windows drive letters would otherwise break or redirect the URI)."""
    from pathlib import Path
    return Path(os.path.abspath(path)).as_uri() + "?mode=ro"


def _gpkg_layers(path):
    con = sqlite3.connect(_ro_uri(path), uri=True)
    try:
        return [r[0] for r in con.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features' "
                                          "ORDER BY table_name")]
    finally:
        con.close()


def _read_gpkg(path, layer=None):
    con = sqlite3.connect(_ro_uri(path), uri=True)
    try:
        layers = [r[0] for r in con.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'")]
        if not layers:
            raise ValueError("The GeoPackage has no vector layer")
        layer = layer if layer in layers else layers[0]
        col, srs = con.execute("SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name=?",
                               (layer,)).fetchone()
        crs = None
        row = con.execute("SELECT organization, organization_coordsys_id, definition FROM gpkg_spatial_ref_sys "
                          "WHERE srs_id=?", (srs,)).fetchone()
        if row:
            org, code, wkt = row
            if org and org.upper() == "EPSG" and code and code > 0:
                crs = _crs_epsg(code)
            if crs is None and wkt and wkt.strip() not in ("", "undefined"):
                from core import crs as crsmod
                crs = crsmod.from_text(wkt)
        cur = con.execute(f'SELECT * FROM "{layer}"')
        names = [d[0] for d in cur.description]
        gi = names.index(col)
        feats = []
        for rec in cur:
            blob = rec[gi]
            if blob is None:
                continue
            blob = bytes(blob)
            if blob[:2] != b"GP":
                raise ValueError("Not a GeoPackage geometry")
            flags = blob[3]
            env = (flags >> 1) & 0x07
            hdr = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env, 0)
            if flags & 0x10:                                 # empty geometry
                continue
            geoms, _ = _parse_wkb(blob, hdr)
            attrs = {n: v for k, (n, v) in enumerate(zip(names, rec)) if k != gi and not isinstance(v, bytes)}
            for gt, parts in geoms:
                if gt == "Point":
                    feats.append({"type": "Point", "parts": [parts[0][:1]], "attrs": dict(attrs)})
                elif gt == "LineString" and len(parts[0]) >= 2:
                    feats.append({"type": "LineString", "parts": parts, "attrs": dict(attrs)})
                elif gt == "Polygon" and parts:
                    feats.append({"type": "Polygon", "parts": parts, "attrs": dict(attrs)})
        return feats, crs, layer
    finally:
        con.close()


def _write_gpkg(path, feats, crs, fields, layer):
    if os.path.exists(path):
        os.remove(path)
    gt = {f["type"] for f in feats} or {"LineString"}
    gname = gt.pop() if len(gt) == 1 else "GEOMETRY"
    gname = {"Point": "POINT", "LineString": "LINESTRING", "Polygon": "POLYGON"}.get(gname, gname)
    has_z = any(p.shape[1] >= 3 for f in feats for p in f["parts"])
    layer = re.sub(r"[^A-Za-z0-9_]", "_", layer) or "layer"
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA application_id = 1196444487")       # 'GPKG'
        con.execute("PRAGMA user_version = 10300")
        con.executescript("""
        CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
            organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL,
            description TEXT);
        CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
            identifier TEXT UNIQUE, description TEXT DEFAULT '',
            last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER);
        CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL, column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name));
        """)
        con.executemany("INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)", [
            ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", ""),
            ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "")])
        srs = -1
        if crs is not None:
            from core import crs as crsmod
            code = crsmod.epsg_of(crs)
            srs = int(code) if code else 100000
            con.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
                        (crsmod.label(crs)[:200], srs, "EPSG" if code else "NONE", int(code) if code else srs,
                         crs.to_wkt("WKT1_GDAL"), ""))
        names = _fields(feats, fields)
        cols = ", ".join(f'"{n}" {"REAL" if t == "N" else "TEXT"}' for n, t in names)
        con.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY AUTOINCREMENT, geom {gname}'
                    + (f", {cols}" if cols else "") + ")")
        allp = [p for f in feats for p in f["parts"]]
        lo = np.min([p[:, :2].min(axis=0) for p in allp], axis=0) if allp else (0, 0)
        hi = np.max([p[:, :2].max(axis=0) for p in allp], axis=0) if allp else (0, 0)
        con.execute("INSERT INTO gpkg_contents (table_name, data_type, identifier, min_x, min_y, max_x, max_y, "
                    "srs_id) VALUES (?,?,?,?,?,?,?,?)", (layer, "features", layer, float(lo[0]), float(lo[1]),
                                                          float(hi[0]), float(hi[1]), srs))
        con.execute("INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)", (layer, "geom", gname, srs,
                                                                               1 if has_z else 0, 0))
        q = f'INSERT INTO "{layer}" (geom' + "".join(f', "{n}"' for n, _t in names) + ") VALUES (?" + \
            ",?" * len(names) + ")"
        rows = []
        for f in feats:
            parts = [p if (p.shape[1] >= 3) == has_z else (np.column_stack([p, np.zeros(len(p))]) if has_z
                                                             else p[:, :2]) for p in f["parts"]]
            blob = b"GP" + struct.pack("<BBi", 0, 0x01, srs) + _wkb(f["type"], parts)
            rows.append([sqlite3.Binary(blob)] + [_cell(f["attrs"].get(n), t) for n, t in names])
        con.executemany(q, rows)
        con.commit()
    finally:
        con.close()


# ================================================================ GeoJSON
def _read_geojson(path):
    with open(path, encoding="utf-8-sig") as fh:
        doc = json.load(fh)
    crs = None
    name = (((doc.get("crs") or {}).get("properties") or {}).get("name") or "") if isinstance(doc, dict) else ""
    m = re.search(r"EPSG:*:?(\d+)", name)
    if m and m.group(1) != "4326":
        crs = _crs_epsg(m.group(1))
    if crs is None and not ("CRS84" in name or name == ""):
        crs = _crs_epsg(4326)
    if crs is None:
        crs = _wgs84()
    feats = []

    def add(geom, attrs):
        if not geom:
            return
        t, c = geom.get("type"), geom.get("coordinates")
        if t == "Point":
            feats.append({"type": "Point", "parts": [_arr([c])], "attrs": dict(attrs)})
        elif t == "MultiPoint":
            feats.extend({"type": "Point", "parts": [_arr([p])], "attrs": dict(attrs)} for p in c)
        elif t == "LineString":
            feats.append({"type": "LineString", "parts": [_arr(c)], "attrs": dict(attrs)})
        elif t == "MultiLineString":
            feats.extend({"type": "LineString", "parts": [_arr(p)], "attrs": dict(attrs)} for p in c)
        elif t == "Polygon":
            feats.append({"type": "Polygon", "parts": [_arr(r) for r in c], "attrs": dict(attrs)})
        elif t == "MultiPolygon":
            feats.extend({"type": "Polygon", "parts": [_arr(r) for r in p], "attrs": dict(attrs)} for p in c)
        elif t == "GeometryCollection":
            for g in geom.get("geometries", []):
                add(g, attrs)

    if doc.get("type") == "FeatureCollection":
        for f in doc.get("features", []):
            add(f.get("geometry"), f.get("properties") or {})
    elif doc.get("type") == "Feature":
        add(doc.get("geometry"), doc.get("properties") or {})
    else:
        add(doc, {})
    return feats, crs


def _transform_feats(feats, src, dst):
    """All coordinates of all features in ONE transformation (a transformer per feature is far too slow
    for thousands of features)."""
    from core import crs as crsmod
    if src is None or dst is None or crsmod.same(src, dst) or not feats:
        return feats
    parts = [p for f in feats for p in f["parts"]]
    n = [len(p) for p in parts]
    allp = np.vstack([p[:, :2] for p in parts])
    x, y = crsmod.transform_xy(allp[:, 0], allp[:, 1], src, dst)
    xy = np.column_stack([x, y])
    out, k, s0 = [], 0, 0
    for f in feats:
        new = []
        for p in f["parts"]:
            m = n[k]
            q = xy[s0:s0 + m]
            new.append(np.column_stack([q, p[:, 2]]) if p.shape[1] >= 3 else q)
            s0 += m
            k += 1
        out.append({"type": f["type"], "parts": new, "attrs": f["attrs"]})
    return out


def _to_wgs84(feats, crs):
    return _transform_feats(feats, crs, _wgs84())


def _write_geojson(path, feats, crs, fields):
    """RFC 7946: coordinates in WGS 84 longitude / latitude (transformed from the project system)."""
    feats = _to_wgs84(feats, crs)
    names = _fields(feats, fields)

    def r(a):
        return [[round(float(v), 9 if i < 2 else 3) for i, v in enumerate(q)] for q in a]

    out = []
    for f in feats:
        if f["type"] == "Point":
            g = {"type": "Point", "coordinates": r(f["parts"][0])[0]}
        elif f["type"] == "LineString":
            g = {"type": "LineString", "coordinates": r(f["parts"][0])}
        else:
            rings = []
            for k, p in enumerate(f["parts"]):
                if (k == 0) == (_signed_area(p) < 0):        # RFC 7946: outer counter-clockwise
                    p = p[::-1]
                if not _same(p[0], p[-1]):
                    p = np.vstack([p, p[:1]])
                rings.append(r(p))
            g = {"type": "Polygon", "coordinates": rings}
        out.append({"type": "Feature", "geometry": g,
                    "properties": {n: _cell(f["attrs"].get(n), t, json_ok=True) for n, t in names}})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": out}, fh, ensure_ascii=False)


# ================================================================ KML / KMZ
def _kml_root(path):
    if path.lower().endswith(".kmz"):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            if name is None:
                raise ValueError("No KML inside the KMZ")
            data = z.read(name)
    else:
        with open(path, "rb") as fh:
            data = fh.read()
    return ET.fromstring(data)


def _strip(tag):
    return tag.split("}", 1)[-1]


def _kml_coords(text):
    vals = []
    for tok in (text or "").split():
        c = [float(v) for v in tok.split(",") if v != ""]
        if len(c) >= 2:
            vals.append(c[:3])
    if not vals:
        return np.empty((0, 2))
    w = max(len(v) for v in vals)
    return np.array([v + [0.0] * (w - len(v)) for v in vals], float)


def _kml_folders(root):
    out = []
    for el in root.iter():
        if _strip(el.tag) == "Folder":
            nm = next((c.text for c in el if _strip(c.tag) == "name"), None)
            if nm:
                out.append(nm.strip())
    return out


def _read_kml(path, layer=None):
    root = _kml_root(path)
    feats = []

    def walk(el, folder):
        for c in el:
            t = _strip(c.tag)
            if t in ("Folder", "Document"):
                nm = next((x.text for x in c if _strip(x.tag) == "name"), None)
                walk(c, (nm or "").strip() if t == "Folder" else folder)
            elif t == "Placemark":
                if layer and folder != layer:
                    continue
                attrs = {"folder": folder}
                for x in c:
                    xt = _strip(x.tag)
                    if xt in ("name", "description") and x.text:
                        attrs[xt] = x.text.strip()
                    elif xt == "ExtendedData":
                        for d in x.iter():
                            if _strip(d.tag) == "Data":
                                val = next((v.text for v in d if _strip(v.tag) == "value"), None)
                                attrs[d.get("name")] = val
                            elif _strip(d.tag) == "SimpleData":
                                attrs[d.get("name")] = d.text
                for g in c.iter():
                    gt = _strip(g.tag)
                    co = next((x for x in g if _strip(x.tag) == "coordinates"), None)
                    if gt == "Point" and co is not None:
                        a = _kml_coords(co.text)
                        if len(a):
                            feats.append({"type": "Point", "parts": [a[:1]], "attrs": dict(attrs)})
                    elif gt == "LineString" and co is not None:
                        a = _kml_coords(co.text)
                        if len(a) >= 2:
                            feats.append({"type": "LineString", "parts": [a], "attrs": dict(attrs)})
                    elif gt == "Polygon":
                        rings = []
                        for b in g:
                            if _strip(b.tag) in ("outerBoundaryIs", "innerBoundaryIs"):
                                for lr in b.iter():
                                    if _strip(lr.tag) == "coordinates":
                                        rings.append(_kml_coords(lr.text))
                        if rings:
                            feats.append({"type": "Polygon", "parts": rings, "attrs": dict(attrs)})
    walk(root, "")
    return feats, _wgs84()


def _write_kml(path, feats, crs, fields, layer):
    from xml.sax.saxutils import escape
    feats = _to_wgs84(feats, crs)
    names = _fields(feats, fields)

    def co(a):
        return " ".join(f"{q[0]:.9f},{q[1]:.9f}" for q in a)

    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           f"<name>{escape(layer)}</name>",
           '<Style id="s"><LineStyle><color>ff2a9fff</color><width>2</width></LineStyle>'
           '<PolyStyle><color>402a9fff</color></PolyStyle></Style>']
    for f in feats:
        nm = next((f["attrs"].get(k) for k in ("name", "NAME", "Name", "TYPE", "type") if f["attrs"].get(k)), "")
        ext = "".join(f'<Data name="{escape(str(n))}"><value>{escape(str(_cell(f["attrs"].get(n), t)))}</value></Data>'
                      for n, t in names if f["attrs"].get(n) is not None)
        if f["type"] == "Point":
            g = f"<Point><coordinates>{co(f['parts'][0][:1])}</coordinates></Point>"
        elif f["type"] == "LineString":
            g = f"<LineString><tessellate>1</tessellate><coordinates>{co(f['parts'][0])}</coordinates></LineString>"
        else:
            rings = []
            for k, p in enumerate(f["parts"]):
                if not _same(p[0], p[-1]):
                    p = np.vstack([p, p[:1]])
                tag = "outerBoundaryIs" if k == 0 else "innerBoundaryIs"
                rings.append(f"<{tag}><LinearRing><coordinates>{co(p)}</coordinates></LinearRing></{tag}>")
            g = "<Polygon>" + "".join(rings) + "</Polygon>"
        out.append(f"<Placemark><name>{escape(str(nm))}</name><styleUrl>#s</styleUrl>"
                   f"<ExtendedData>{ext}</ExtendedData>{g}</Placemark>")
    out.append("</Document></kml>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


# ================================================================ GPX
def _read_gpx(path):
    root = ET.parse(path).getroot()
    feats = []

    def pt(e):
        lat, lon = float(e.get("lat")), float(e.get("lon"))
        ele = next((float(x.text) for x in e if _strip(x.tag) == "ele" and x.text), None)
        return [lon, lat] + ([ele] if ele is not None else [])

    def name(e):
        return next((x.text for x in e if _strip(x.tag) == "name"), "") or ""

    for e in root:
        t = _strip(e.tag)
        if t == "wpt":
            feats.append({"type": "Point", "parts": [_arr([pt(e)])], "attrs": {"name": name(e)}})
        elif t == "rte":
            p = [pt(x) for x in e if _strip(x.tag) == "rtept"]
            if len(p) >= 2:
                feats.append({"type": "LineString", "parts": [_arr(p)], "attrs": {"name": name(e)}})
        elif t == "trk":
            for seg in (x for x in e if _strip(x.tag) == "trkseg"):
                p = [pt(x) for x in seg if _strip(x.tag) == "trkpt"]
                if len(p) >= 2:
                    feats.append({"type": "LineString", "parts": [_arr(p)], "attrs": {"name": name(e)}})
    return feats, _wgs84()


# ================================================================ DXF (ASCII)
def _dxf_pairs(path):
    with open(path, "rb") as fh:
        head = fh.read(22)
    if head.startswith(b"AutoCAD Binary DXF"):
        raise ValueError("Binary DXF is not supported — save the drawing as ASCII DXF")
    for enc in ("utf-8", "cp950", "latin-1"):
        try:
            with open(path, encoding=enc) as fh:
                lines = fh.read().splitlines()
            break
        except UnicodeDecodeError:
            continue
    for i in range(0, len(lines) - 1, 2):
        yield lines[i].strip(), lines[i + 1].strip()


def _dxf_entities(path):
    ents, cur, in_ent = [], None, False
    for code, val in _dxf_pairs(path):
        if code == "2" and val == "ENTITIES":
            in_ent = True
            continue
        if not in_ent:
            continue
        if code == "0":
            if cur is not None:
                ents.append(cur)
            if val == "ENDSEC":
                break
            cur = {"etype": val, "g": []}
        elif cur is not None:
            cur["g"].append((code, val))
    return ents


def _dxf_layers(path):
    return sorted({v for e in _dxf_entities(path) for c, v in e["g"] if c == "8"})


def _hatch_rings(g):
    """Boundary loops of a HATCH entity → list of rings (N, 2). Polyline loops and line / arc edges."""
    rings = []
    i = 0
    n = len(g)

    def take(code):
        nonlocal i
        while i < n and g[i][0] != code:
            i += 1
        if i >= n:
            raise IndexError
        v = g[i][1]
        i += 1
        return v
    try:
        nloops = int(take("91"))
        for _ in range(nloops):
            flag = int(take("92"))
            if flag & 2:                                        # polyline loop
                bulge = int(take("72"))
                take("73")
                nv = int(take("93"))
                pts = []
                for _v in range(nv):
                    x = float(take("10"))
                    y = float(take("20"))
                    if bulge and i < n and g[i][0] == "42":
                        i += 1
                    pts.append((x, y))
                rings.append(np.array(pts))
            else:                                               # edge loop
                ne = int(take("93"))
                pts = []
                for _e in range(ne):
                    et = int(take("72"))
                    if et == 1:                                 # line
                        pts.append((float(take("10")), float(take("20"))))
                        take("11")
                        take("21")
                    elif et == 2:                               # circular arc
                        cx, cy = float(take("10")), float(take("20"))
                        r = float(take("40"))
                        a0, a1 = np.radians(float(take("50"))), np.radians(float(take("51")))
                        ccw = int(take("73"))
                        if not ccw:
                            a0, a1 = -a0, -a1
                        if a1 <= a0:
                            a1 += 2 * np.pi
                        for t in np.linspace(a0, a1, 24, endpoint=False):
                            pts.append((cx + r * np.cos(t), cy + r * np.sin(t)))
                    else:                                       # ellipse / spline: not read → skip loop
                        pts = []
                        break
                if len(pts) >= 3:
                    rings.append(np.array(pts))
            take("97")
    except (IndexError, ValueError):
        pass
    return [r for r in rings if len(r) >= 3]


def _read_dxf(path, layer=None):
    feats = []
    ents = _dxf_entities(path)
    i = 0
    while i < len(ents):
        e = ents[i]
        g = e["g"]
        lay = next((v for c, v in g if c == "8"), "0")
        attrs = {"layer": lay, "entity": e["etype"]}
        use = layer in (None, "", lay)

        def vals(code):
            return [float(v) for c, v in g if c == code]
        t = e["etype"]
        if t == "POINT" and use:
            feats.append({"type": "Point", "parts": [_arr([[vals("10")[0], vals("20")[0],
                                                             (vals("30") or [0.0])[0]]])], "attrs": attrs})
        elif t == "LINE" and use:
            feats.append({"type": "LineString", "parts": [_arr([[vals("10")[0], vals("20")[0], (vals("30") or [0])[0]],
                                                                [vals("11")[0], vals("21")[0], (vals("31") or [0])[0]]])],
                          "attrs": attrs})
        elif t == "LWPOLYLINE" and use:
            xs, ys = vals("10"), vals("20")
            elev = (vals("38") or [0.0])[0]
            closed = int((vals("70") or [0])[0]) & 1
            a = np.column_stack([xs, ys, np.full(len(xs), elev)])
            if len(a) >= 2:
                if closed and len(a) >= 3:
                    feats.append({"type": "Polygon", "parts": [a], "attrs": attrs})
                else:
                    feats.append({"type": "LineString", "parts": [a], "attrs": attrs})
        elif t == "HATCH" and use:
            rings = _hatch_rings(g)
            if rings:
                elev = (vals("30") or [0.0])[0]
                k = int(np.argmax([abs(_signed_area(r)) for r in rings]))      # largest loop = outer
                order = [rings[k]] + [r for j, r in enumerate(rings) if j != k]
                parts = [np.column_stack([r, np.full(len(r), elev)]) for r in order]
                feats.append({"type": "Polygon", "parts": parts, "attrs": attrs})
        elif t == "POLYLINE":
            closed = int((vals("70") or [0])[0]) & 1
            pts = []
            j = i + 1
            while j < len(ents) and ents[j]["etype"] == "VERTEX":
                vg = ents[j]["g"]
                vv = {c: float(v) for c, v in vg if c in ("10", "20", "30")}
                pts.append([vv.get("10", 0.0), vv.get("20", 0.0), vv.get("30", 0.0)])
                j += 1
            if use and len(pts) >= 2:
                a = _arr(pts)
                feats.append({"type": "Polygon" if closed and len(a) >= 3 else "LineString", "parts": [a],
                              "attrs": attrs})
            i = j
        i += 1
    return feats, None


def _write_dxf(path, feats):
    out = ["0", "SECTION", "2", "ENTITIES"]
    for f in feats:
        lay = re.sub(r"[^A-Za-z0-9_\-]", "_", str(f["attrs"].get("TYPE") or f["attrs"].get("type") or "PCS"))[:31]
        for p in f["parts"]:
            p = p if p.shape[1] >= 3 else np.column_stack([p, np.zeros(len(p))])
            if f["type"] == "Point":
                out += ["0", "POINT", "8", lay, "10", f"{p[0, 0]:.4f}", "20", f"{p[0, 1]:.4f}", "30", f"{p[0, 2]:.4f}"]
            else:
                closed = f["type"] == "Polygon"
                out += ["0", "POLYLINE", "8", lay, "66", "1", "10", "0", "20", "0", "30", "0", "70",
                        str(8 | (1 if closed else 0))]
                for q in p:
                    out += ["0", "VERTEX", "8", lay, "10", f"{q[0]:.4f}", "20", f"{q[1]:.4f}", "30", f"{q[2]:.4f}",
                            "70", "32"]
                out += ["0", "SEQEND", "8", lay]
    out += ["0", "ENDSEC", "0", "EOF"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


# ================================================================ attributes
def _fields(feats, fields=None):
    """[(name, 'N' | 'C')] — given, or inferred from the attribute values."""
    if fields:
        return list(fields)
    names = []
    for f in feats:
        for k in f["attrs"]:
            if k not in names:
                names.append(k)
    out = []
    for n in names:
        vs = [f["attrs"].get(n) for f in feats if f["attrs"].get(n) is not None]
        num = vs and all(isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool) for v in vs)
        out.append((n, "N" if num else "C"))
    return out


def _cell(v, t, json_ok=False):
    if v is None:
        return None
    if isinstance(v, (float, np.floating)) and not np.isfinite(v):
        return None
    if t == "N":
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    return v if (json_ok and isinstance(v, (int, float))) else str(v)


# ================================================================ public API
def layers(path):
    e = os.path.splitext(path)[1].lower()
    try:
        if e == ".gpkg":
            return _gpkg_layers(path)
        if e in (".kml", ".kmz"):
            return _kml_folders(_kml_root(path))
        if e == ".dxf":
            return _dxf_layers(path)
    except Exception:  # noqa: BLE001
        return []
    return []


def read(path, layer=None):
    """→ (features, crs or None, info text)."""
    e = os.path.splitext(path)[1].lower()
    if e == ".shp":
        feats, crs = _read_shp(path)
    elif e == ".gpkg":
        feats, crs, layer = _read_gpkg(path, layer)
    elif e in (".geojson", ".json"):
        feats, crs = _read_geojson(path)
    elif e in (".kml", ".kmz"):
        feats, crs = _read_kml(path, layer)
    elif e == ".gpx":
        feats, crs = _read_gpx(path)
    elif e == ".dxf":
        feats, crs = _read_dxf(path, layer)
    else:
        raise ValueError(f"Unsupported vector format: {e}")
    kinds = {}
    for f in feats:
        kinds[f["type"]] = kinds.get(f["type"], 0) + 1
    info = ", ".join(f"{n} {k.lower()}{'s' if n != 1 else ''}" for k, n in kinds.items()) or "no features"
    if layer:
        info += f" (layer '{layer}')"
    return feats, crs, info


def write(path, feats, crs=None, fields=None, layer="layer"):
    e = os.path.splitext(path)[1].lower()
    if not feats:
        raise ValueError("Nothing to export")
    feats = [{"type": f["type"], "parts": [np.asarray(p, float) for p in f["parts"]], "attrs": f.get("attrs", {})}
             for f in feats]
    if e == ".shp":
        _write_shp(path, feats, crs, fields)
    elif e == ".gpkg":
        _write_gpkg(path, feats, crs, fields, layer)
    elif e in (".geojson", ".json"):
        _write_geojson(path, feats, crs, fields)
    elif e == ".kml":
        _write_kml(path, feats, crs, fields, layer)
    elif e == ".dxf":
        _write_dxf(path, feats)
    else:
        raise ValueError(f"Unsupported export format: {e}")
    return path


def reproject(feats, src, dst):
    """Features from CRS src to dst (no-op if either is None or both are the same)."""
    return _transform_feats(feats, src, dst)


def polygons(feats):
    """[(name, outer ring (N, 2))] of the polygon features."""
    out = []
    for k, f in enumerate(feats):
        if f["type"] == "Polygon" and len(f["parts"][0]) >= 3:
            nm = next((str(f["attrs"].get(a)) for a in ("name", "NAME", "Name", "id", "ID", "layer")
                       if f["attrs"].get(a) not in (None, "")), f"polygon {k + 1}")
            out.append((nm, np.asarray(f["parts"][0], float)[:, :2]))
    return out
