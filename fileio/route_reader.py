"""Route import: Shapefile (polyline / point) and RPL-style CSV / TXT with column mapping."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from core import crs as crsmod
from core.route import Route
from fileio.xyz_reader import DELIMITERS

ROUTE_ROLES = ["Easting (X)", "Northing (Y)", "Longitude", "Latitude", "KP", "Route ID",
               "Cable top (elevation)", "Cable top (depth, + down)", "DoB (reported)", "Ignore"]
CABLE_ROLES = ("Cable top (elevation)", "Cable top (depth, + down)", "DoB (reported)")


def read_shp(path):
    """→ (routes, crs_guess, info). Polylines → one route per part; points → one route (in file order)."""
    import shapefile
    r = shapefile.Reader(path)
    fields = [f[0] for f in r.fields[1:]]
    name_field = next((f for f in fields if f.lower() in ("name", "route", "route_name", "cable", "id", "line")), None)
    routes = []
    base = os.path.splitext(os.path.basename(path))[0]
    pts = []
    for k, sr in enumerate(r.iterShapeRecords()):
        sh = sr.shape
        rec = sr.record.as_dict() if hasattr(sr.record, "as_dict") else {}
        label = str(rec.get(name_field)) if name_field and rec.get(name_field) not in (None, "") else f"{base} {k + 1}"
        if sh.shapeType in (shapefile.POLYLINE, shapefile.POLYLINEZ, shapefile.POLYLINEM):
            parts = list(sh.parts) + [len(sh.points)]
            for j in range(len(sh.parts)):
                xy = np.asarray(sh.points[parts[j]:parts[j + 1]], dtype=np.float64)[:, :2]
                if len(xy) >= 2:
                    nm = label if len(sh.parts) == 1 else f"{label} part {j + 1}"
                    zz = None
                    if sh.shapeType == shapefile.POLYLINEZ and getattr(sh, "z", None) is not None:
                        zz = np.asarray(sh.z[parts[j]:parts[j + 1]], dtype=np.float64)
                        if len(zz) != len(xy) or not np.any(np.abs(zz) > 1e-9):
                            zz = None                         # all-zero Z = 2D line stored as PolyLineZ
                    routes.append(Route(nm, xy, meta={"attributes": rec, "shp_z": zz}))
        elif sh.shapeType in (shapefile.POINT, shapefile.POINTZ, shapefile.POINTM):
            pts.append(sh.points[0][:2])
    if pts and len(pts) >= 2:
        routes.append(Route(base, np.asarray(pts, float)))
    prj = os.path.splitext(path)[0] + ".prj"
    crs = crsmod.guess_from_prj(prj) if os.path.isfile(prj) else None
    info = f"{len(r)} feature(s), {len(routes)} route(s)" + (", .prj found" if crs is not None else ", no .prj")
    return routes, crs, info


def read_route_table(path, skip_rows, delim_key, roles, encoding=None, kp_unit="km", start_kp=0.0):
    """CSV/TXT route (RPL). roles: one entry of ROUTE_ROLES per column. Returns (routes, geographic: bool)."""
    use = {i: r for i, r in enumerate(roles) if r != "Ignore"}
    df = pd.read_csv(path, sep=DELIMITERS[delim_key][1], header=None, skiprows=skip_rows, usecols=sorted(use),
                     encoding=encoding, engine="python" if delim_key == "whitespace" else "c",
                     on_bad_lines="skip", skip_blank_lines=True, dtype=str)
    col = {r: i for i, r in use.items()}
    geo = "Longitude" in col and "Latitude" in col
    xk, yk = ("Longitude", "Latitude") if geo else ("Easting (X)", "Northing (Y)")
    if xk not in col or yk not in col:
        raise ValueError("Assign Easting + Northing, or Longitude + Latitude")
    x = pd.to_numeric(df[col[xk]], errors="coerce").to_numpy()
    y = pd.to_numeric(df[col[yk]], errors="coerce").to_numpy()
    kp = pd.to_numeric(df[col["KP"]], errors="coerce").to_numpy() if "KP" in col else None
    if kp is not None and kp_unit == "m":
        kp = kp / 1000.0
    rid = df[col["Route ID"]].astype(str).to_numpy() if "Route ID" in col else np.full(len(x), "")
    ztop = None
    if "Cable top (elevation)" in col:
        ztop = pd.to_numeric(df[col["Cable top (elevation)"]], errors="coerce").to_numpy()
    elif "Cable top (depth, + down)" in col:
        ztop = -pd.to_numeric(df[col["Cable top (depth, + down)"]], errors="coerce").to_numpy()
    dob = pd.to_numeric(df[col["DoB (reported)"]], errors="coerce").to_numpy() if "DoB (reported)" in col else None
    ok = np.isfinite(x) & np.isfinite(y)
    if kp is not None:
        ok &= np.isfinite(kp)
    base = os.path.splitext(os.path.basename(path))[0]
    routes = []
    for name in dict.fromkeys(rid[ok]):                        # keep file order
        m = ok & (rid == name)
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
