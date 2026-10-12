"""Route import: Shapefile (polyline / point) and RPL-style CSV / TXT with column mapping."""
from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd

from core import crs as crsmod
from core.route import Route
from fileio.xyz_reader import DELIMITERS

ROUTE_ROLES = ["Easting (X)", "Northing (Y)", "Longitude", "Latitude", "KP", "Route ID",
               "Cable top (elevation)", "Cable top (depth, + down)", "DoB (reported)",
               "Seabed (elevation)", "Seabed (depth, + down)", "Ignore"]
Z_UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "ft": 0.3048}
DEFAULT_OPTS = {"decimal_comma": False, "comment": "", "z_unit": "m", "z_offset": 0.0, "z_ref": "top",
                "diameter": 0.2, "sort_kp": False}


def parse_angle(t):
    """Decimal degrees or degrees-minutes(-seconds) with an optional hemisphere letter:
    120.5 · -22.25 · 120°30'15.2"E · 120 30 15.2 E · 22 15.254N · N22°15.254' → float (S / W negative)."""
    if t is None:
        return np.nan
    s = str(t).strip().upper().replace(",", ".")
    if not s:
        return np.nan
    try:
        return float(s)
    except ValueError:
        pass
    sign = -1.0 if (s.startswith("-") or any(h in s for h in "SW")) else 1.0
    nums = re.findall(r"\d+(?:\.\d+)?", s)
    if not nums:
        return np.nan
    v = [float(n) for n in nums[:3]]
    deg = v[0] + (v[1] / 60.0 if len(v) > 1 else 0.0) + (v[2] / 3600.0 if len(v) > 2 else 0.0)
    return sign * deg


def _numcol(series, decimal_comma=False):
    s = series.astype(str).str.strip()
    if decimal_comma:
        s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").to_numpy()


def cable_level(values, is_depth, opts):
    """Raw cable values → top-of-cable elevation (unit, datum offset, depth sign, top / centre / bottom)."""
    z = np.asarray(values, float) * Z_UNITS.get(opts.get("z_unit", "m"), 1.0)
    z = -z if is_depth else z
    z = z + float(opts.get("z_offset", 0.0))
    ref = opts.get("z_ref", "top")
    d = float(opts.get("diameter", 0.2))
    if ref == "centre":
        z = z + d / 2.0
    elif ref == "bottom":
        z = z + d
    return z


def _level(values, is_depth, opts):
    z = np.asarray(values, float) * Z_UNITS.get(opts.get("z_unit", "m"), 1.0)
    return (-z if is_depth else z) + float(opts.get("z_offset", 0.0))
CABLE_ROLES = ("Cable top (elevation)", "Cable top (depth, + down)", "DoB (reported)")


Z_FIELD_KEYS = ("toc", "top of cable", "cable top", "cable z", "top z", "cable_z", "z", "elevation", "depth",
                "toc depth", "cable depth")
SEABED_KEYS = ("seabed", "msbl", "seabed z", "seabed depth", "water depth", "wd")
KP_KEYS = ("kp", "chainage", "km", "kmp")
NAME_KEYS = ("name", "route", "route_name", "cable", "id", "line", "label")
DOB_KEYS = ("dob", "burial", "depth of burial", "doc")


def _find(fields, keys):
    low = {str(f).lower().replace("_", " "): f for f in fields}
    for k in keys:
        if k in low:
            return low[k]
    for k in keys:
        for lf, f in low.items():
            if len(k) > 2 and lf.startswith(k):
                return f
    return None


def vector_fields(feats):
    """Attribute names of the features and automatic guesses for the route import."""
    fields = list(dict.fromkeys(k for f in feats for k in f["attrs"]))
    has_geom_z = any(p.shape[1] >= 3 and np.any(np.abs(p[:, 2]) > 1e-9) for f in feats for p in f["parts"])
    zf = _find(fields, Z_FIELD_KEYS)
    guess = {"name": _find(fields, NAME_KEYS), "kp": _find(fields, KP_KEYS), "dob": _find(fields, DOB_KEYS),
             "seabed": _find(fields, SEABED_KEYS),
             "z": "geometry" if has_geom_z else zf,
             "z_depth": bool(zf and "depth" in str(zf).lower()) and not has_geom_z}
    return fields, has_geom_z, guess


def routes_from_features(feats, base, opts):
    """Lines → one route each; points → one route per value of the name field (or one for all), ordered by
    the KP field (or file order). opts: name / kp / z ('geometry', a field, or None) / z_depth / dob / seabed /
    seabed_depth fields + DEFAULT_OPTS (z unit, offset, top / centre / bottom, diameter)."""
    o = dict(DEFAULT_OPTS, **opts)

    def fnum(a, k):
        try:
            v = a.get(k) if k else None
            return float(str(v).replace(",", ".")) if v not in (None, "") else np.nan
        except (TypeError, ValueError):
            return np.nan
    routes = []
    lines = [f for f in feats if f["type"] == "LineString"]
    for k, f in enumerate(lines):
        a = f["attrs"]
        p = f["parts"][0]
        label = str(a.get(o.get("name"))) if o.get("name") and a.get(o.get("name")) not in (None, "") else f"{base} {k + 1}"
        z = None
        if o.get("z") == "geometry" and p.shape[1] >= 3 and np.any(np.abs(p[:, 2]) > 1e-9):
            z = cable_level(p[:, 2], o.get("z_depth", False), o)
        kp0 = fnum(a, o.get("kp"))
        r = Route(label, p[:, :2], z=z, meta={"attributes": a})
        if np.isfinite(kp0):
            r.start_kp = kp0
        routes.append(r)
    pts = [f for f in feats if f["type"] == "Point"]
    if pts and not lines:
        groups = {}
        for f in pts:
            key = str(f["attrs"].get(o["name"])) if o.get("name") else base
            groups.setdefault(key, []).append(f)
        for key, fs in groups.items():
            xy = np.array([f["parts"][0][0, :2] for f in fs])
            kp = np.array([fnum(f["attrs"], o.get("kp")) for f in fs]) if o.get("kp") else None
            if o.get("z") == "geometry":
                raw = np.array([f["parts"][0][0, 2] if f["parts"][0].shape[1] >= 3 else np.nan for f in fs])
            elif o.get("z"):
                raw = np.array([fnum(f["attrs"], o["z"]) for f in fs])
            else:
                raw = None
            z = cable_level(raw, o.get("z_depth", False), o) if raw is not None else None
            dob = np.array([fnum(f["attrs"], o.get("dob")) for f in fs]) if o.get("dob") else None
            if z is None and o.get("seabed") and dob is not None:
                sbv = _level(np.array([fnum(f["attrs"], o["seabed"]) for f in fs]), o.get("seabed_depth", False), o)
                z = sbv - dob
            order = np.arange(len(fs))
            if kp is not None and np.isfinite(kp).all():
                order = np.argsort(kp, kind="stable")
            if len(xy) < 2:
                continue
            r = Route(key if o.get("name") else base, xy[order], kp=kp[order] if kp is not None and
                      np.isfinite(kp).all() else None, z=z[order] if z is not None else None,
                      dob=dob[order] if dob is not None else None)
            routes.append(r)
    return routes


def read_shp(path, layer=None, opts=None):
    """Any vector file (Shapefile, GeoPackage, GeoJSON, KML/KMZ, GPX, DXF) → (routes, crs_guess, info).
    Lines → one route per line; points → routes built from the points (e.g. top-of-cable survey points),
    grouped by the name field and ordered by KP. The cable level comes from the geometry Z or a field
    (auto-detected; `opts` overrides — see routes_from_features)."""
    from fileio import vector_io
    feats, crs, vinfo = vector_io.read(path, layer)
    base = os.path.splitext(os.path.basename(path))[0]
    fields, has_z, guess = vector_fields(feats)
    o = dict(guess)
    o.update(opts or {})
    routes = routes_from_features(feats, base, o)
    if crs is None and path.lower().endswith(".shp"):
        prj = os.path.splitext(path)[0] + ".prj"
        crs = crsmod.guess_from_prj(prj) if os.path.isfile(prj) else None
    info = f"{vinfo} → {len(routes)} route(s)" + ("" if crs is not None else ", coordinate system not in the file")
    return routes, crs, info


def read_route_table(path, skip_rows, delim_key, roles, encoding=None, kp_unit="km", start_kp=0.0, opts=None):
    """CSV/TXT route (RPL, SBI / TSS cable data). roles: one entry of ROUTE_ROLES per column.
    opts: see DEFAULT_OPTS (decimal comma, comment prefix, Z unit / datum offset, Z refers to the top / centre /
    bottom of the cable, cable diameter, sort by KP). Returns (routes, geographic: bool)."""
    o = dict(DEFAULT_OPTS, **(opts or {}))
    use = {i: r for i, r in enumerate(roles) if r != "Ignore"}
    df = pd.read_csv(path, sep=DELIMITERS[delim_key][1], header=None, skiprows=skip_rows, usecols=sorted(use),
                     encoding=encoding, engine="python" if delim_key == "whitespace" else "c",
                     on_bad_lines="skip", skip_blank_lines=True, dtype=str,
                     comment=(o["comment"][:1] or None))
    col = {r: i for i, r in use.items()}
    geo = "Longitude" in col and "Latitude" in col
    xk, yk = ("Longitude", "Latitude") if geo else ("Easting (X)", "Northing (Y)")
    if xk not in col or yk not in col:
        raise ValueError("Assign Easting + Northing, or Longitude + Latitude")
    dc = o["decimal_comma"]
    if geo:
        x = np.array([parse_angle(v) for v in df[col[xk]]], float)
        y = np.array([parse_angle(v) for v in df[col[yk]]], float)
    else:
        x = _numcol(df[col[xk]], dc)
        y = _numcol(df[col[yk]], dc)
    kp = _numcol(df[col["KP"]], dc) if "KP" in col else None
    if kp is not None and kp_unit == "m":
        kp = kp / 1000.0
    rid = df[col["Route ID"]].astype(str).to_numpy() if "Route ID" in col else np.full(len(x), "")
    ztop = None
    if "Cable top (elevation)" in col:
        ztop = cable_level(_numcol(df[col["Cable top (elevation)"]], dc), False, o)
    elif "Cable top (depth, + down)" in col:
        ztop = cable_level(_numcol(df[col["Cable top (depth, + down)"]], dc), True, o)
    dob = (_numcol(df[col["DoB (reported)"]], dc) * Z_UNITS.get(o["z_unit"], 1.0)) if "DoB (reported)" in col else None
    sb = None
    if "Seabed (elevation)" in col:
        sb = _level(_numcol(df[col["Seabed (elevation)"]], dc), False, o)
    elif "Seabed (depth, + down)" in col:
        sb = _level(_numcol(df[col["Seabed (depth, + down)"]], dc), True, o)
    if ztop is None and sb is not None and dob is not None:
        ztop = sb - dob                          # cable top from the seabed and the reported burial
    ok = np.isfinite(x) & np.isfinite(y)
    if kp is not None:
        ok &= np.isfinite(kp)
    base = os.path.splitext(os.path.basename(path))[0]
    routes = []
    for name in dict.fromkeys(rid[ok]):                        # keep file order
        m = np.flatnonzero(ok & (rid == name))
        if o["sort_kp"] and kp is not None:
            m = m[np.argsort(kp[m], kind="stable")]
        xy = np.column_stack([x[m], y[m]])
        if len(xy) < 2:
            continue
        r = Route(f"{base} {name}".strip() if name else base, xy, kp[m] if kp is not None else None,
                  start_kp=start_kp, z=ztop[m] if ztop is not None else None,
                  dob=dob[m] if dob is not None else None)
        routes.append(r)
    if not routes:
        raise ValueError("No route with at least two valid vertices was found")
    return routes, geo


HEADER_KEYS = [("DoB (reported)", ("dob", "burial", "depth of burial", "doc")),
               ("Seabed (depth, + down)", ("seabed depth", "water depth", "wd", "msbl depth", "sb depth")),
               ("Seabed (elevation)", ("seabed", "msbl", "sb z", "seabed z", "seabed level")),
               ("Cable top (elevation)", ("toc", "top of cable", "cable top", "cable z", "top z", "z", "elevation")),
               ("Cable top (depth, + down)", ("toc depth", "cable depth", "top depth", "depth")),
               ("KP", ("kp", "chainage", "km", "kmp")), ("Latitude", ("lat",)), ("Longitude", ("lon", "long")),
               ("Easting (X)", ("east", "x", "e")), ("Northing (Y)", ("north", "y", "n")),
               ("Route ID", ("id", "name", "cable", "route", "line"))]


def _header_roles(header):
    roles = []
    for h in header:
        t = h.strip().lower().replace("_", " ")
        role = "Ignore"
        for r, keys in HEADER_KEYS:
            if any(t == k or t.startswith(k + " ") or t.startswith(k + "(") or (len(k) > 2 and t.startswith(k))
                   for k in keys):
                role = r
                break
        roles.append(role)
    return roles


def guess_roles(rows, header=None):
    """Column roles: header names first, then a geometric test (KP steps = distances between vertices)."""
    ncols = max((len(r) for r in rows), default=0)
    if header and len(header) == ncols:
        roles = _header_roles(header)
        if ("Easting (X)" in roles and "Northing (Y)" in roles) or ("Longitude" in roles and "Latitude" in roles):
            return roles
    num = []
    for c in range(ncols):
        vals = []
        for r in rows:
            try:
                vals.append(float(r[c]))
            except (ValueError, IndexError):
                vals.append(np.nan)
        num.append(np.array(vals))
    roles = ["Ignore"] * ncols
    numeric = [c for c in range(ncols) if np.isfinite(num[c]).mean() > 0.8]
    # projected coordinates + KP: find (k, x, y) with diff(k) ≈ distance (KP in km or m)
    best = None
    for k in numeric:
        if not np.all(np.diff(num[k][np.isfinite(num[k])]) >= 0):
            continue
        for a in numeric:
            for b in numeric:
                if len({k, a, b}) < 3:
                    continue
                d = np.hypot(np.diff(num[a]), np.diff(num[b]))
                dk = np.diff(num[k])
                ok = np.isfinite(d) & np.isfinite(dk) & (d > 0)
                if ok.sum() < 2:
                    continue
                for scale in (1000.0, 1.0):
                    err = np.median(np.abs(dk[ok] * scale - d[ok]) / d[ok])
                    if err < 0.05 and (best is None or err < best[0]):
                        best = (err, k, a, b)
    big = [c for c in numeric if np.nanmedian(np.abs(num[c])) > 1000]
    if best is not None:
        _, k, a, b = best
        # easting is the smaller coordinate in TM / UTM systems (northing ~ millions)
        if np.nanmedian(num[a]) > np.nanmedian(num[b]):
            a, b = b, a
        roles[k], roles[a], roles[b] = "KP", "Easting (X)", "Northing (Y)"
    elif len(big) >= 2:
        a, b = big[0], big[1]
        if np.nanmedian(num[a]) > np.nanmedian(num[b]):
            a, b = b, a
        roles[a], roles[b] = "Easting (X)", "Northing (Y)"
    else:                                    # geographic: longitude has |v| > 90 somewhere (east Asia: 100–180)
        lon = [c for c in numeric if 90 < np.nanmedian(np.abs(num[c])) <= 180]
        lat = [c for c in numeric if np.nanmedian(np.abs(num[c])) <= 90 and c not in lon]
        if lon and lat:
            roles[lon[0]] = "Longitude"
            # latitude: the remaining column that is NOT monotonic-and-starting-like-a-KP if possible
            pick = [c for c in lat if not (np.all(np.diff(num[c]) >= 0) and num[c][0] < 1)] or lat
            roles[pick[-1] if len(pick) > 1 else pick[0]] = "Latitude"
            for c in lat:
                if roles[c] == "Ignore" and np.all(np.diff(num[c]) >= 0):
                    roles[c] = "KP"
                    break
    for c in range(ncols):                   # remaining text column → route id
        if roles[c] == "Ignore" and np.isfinite(num[c]).mean() < 0.2:
            roles[c] = "Route ID"
            break
    return roles


def reproject_routes(routes, src, dst):
    for r in routes:
        x, y = crsmod.transform_xy(r.xy[:, 0], r.xy[:, 1], src, dst)
        r.xy = np.column_stack([x, y])
    return routes


def _num(t):
    try:
        float(t)
        return True
    except ValueError:
        return False


def _data_row(tokens):
    """Route rows may contain text (cable IDs): ≥ 2 numeric fields is enough."""
    return len(tokens) >= 2 and sum(_num(t) for t in tokens) >= 2


def detect_delimiter(lines):
    from fileio.xyz_reader import split_line
    sample = [l for l in lines if l.strip()][-30:]
    best, best_score = "whitespace", -1
    for key in ("comma", "tab", "semicolon", "whitespace"):
        rows = [split_line(l, key) for l in sample]
        score = sum(_data_row(r) for r in rows)
        # prefer the delimiter that also gives a consistent column count
        counts = [len(r) for r in rows if _data_row(r)]
        if counts:
            score += 0.5 * (len(counts) - len(set(counts)) == len(counts) - 1)
        if score > best_score:
            best, best_score = key, score
    return best


def detect_start_row(lines, delim_key):
    from fileio.xyz_reader import split_line
    parsed = [split_line(l, delim_key) for l in lines]
    ok = [_data_row(r) for r in parsed]
    for i in range(len(lines)):
        if ok[i]:
            window = range(i, min(i + 3, len(lines)))
            if all(ok[j] and len(parsed[j]) == len(parsed[i]) for j in window):
                return i
    return 0


def guess_kp_unit(rows, roles):
    """'m' if KP steps equal metres between vertices, else 'km'."""
    try:
        k = roles.index("KP")
        a, b = roles.index("Easting (X)"), roles.index("Northing (Y)")
    except ValueError:
        return "km"
    v = np.array([[float(r[k]), float(r[a]), float(r[b])] for r in rows if len(r) > max(k, a, b)
                  and all(_num(r[i]) for i in (k, a, b))])
    if len(v) < 2:
        return "km"
    d = np.hypot(np.diff(v[:, 1]), np.diff(v[:, 2]))
    dk = np.diff(v[:, 0])
    ok = d > 0
    return "m" if ok.any() and np.median(np.abs(dk[ok] - d[ok]) / d[ok]) < 0.05 else "km"
