"""Structures: point positions with a name and a foundation diameter (wind turbines, substations, piles).

Readers: CSV / TXT with column roles, and Shapefile points (name and diameter fields detected).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

ROLES = ["Name", "Easting (X)", "Northing (Y)", "Longitude", "Latitude", "Diameter", "Ignore"]
HEADER_KEYS = [("Diameter", ("diameter", "diam", "dia", "od", "pile d", "mp d")),
               ("Name", ("name", "id", "wtg", "label", "turbine", "structure", "location", "loc")),
               ("Latitude", ("lat",)), ("Longitude", ("lon", "long")),
               ("Easting (X)", ("east", "x", "e")), ("Northing (Y)", ("north", "y", "n"))]


@dataclass
class Structures:
    names: list
    xy: np.ndarray                      # (N, 2) project coordinates
    diameter: np.ndarray                # (N,) foundation diameter (m)
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.xy = np.asarray(self.xy, dtype=np.float64).reshape(-1, 2)
        self.diameter = np.asarray(self.diameter, dtype=np.float64).reshape(-1)
        self.names = [str(n) for n in self.names]

    def __len__(self):
        return len(self.names)

    def index(self, name):
        return self.names.index(name)


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
    ncols = max((len(r) for r in rows), default=0)
    if header and len(header) == ncols:
        roles = _header_roles(header)
        if ("Easting (X)" in roles and "Northing (Y)" in roles) or ("Longitude" in roles and "Latitude" in roles):
            return roles
    roles = ["Ignore"] * ncols

    def num(c):
        out = []
        for r in rows:
            try:
                out.append(float(r[c]))
            except (ValueError, IndexError):
                out.append(np.nan)
        return np.array(out)

    cols = [num(c) for c in range(ncols)]
    numeric = [c for c in range(ncols) if np.isfinite(cols[c]).mean() > 0.8]
    text = [c for c in range(ncols) if np.isfinite(cols[c]).mean() < 0.2]
    big = [c for c in numeric if np.nanmedian(np.abs(cols[c])) > 1000]
    if len(big) >= 2:
        a, b = big[0], big[1]
        if np.nanmedian(cols[a]) > np.nanmedian(cols[b]):
            a, b = b, a
        roles[a], roles[b] = "Easting (X)", "Northing (Y)"
    else:
        lon = [c for c in numeric if 90 < np.nanmedian(np.abs(cols[c])) <= 180]
        lat = [c for c in numeric if np.nanmedian(np.abs(cols[c])) <= 90 and c not in lon]
        if lon and lat:
            roles[lon[0]], roles[lat[0]] = "Longitude", "Latitude"
    small = [c for c in numeric if roles[c] == "Ignore" and 0.5 <= np.nanmedian(cols[c]) <= 20]
    if small:
        roles[small[0]] = "Diameter"
    if text:
        roles[text[0]] = "Name"
    return roles


def read_table(path, skip_rows, delim_key, roles, encoding=None, default_diameter=8.0):
    """→ (Structures, geographic: bool)"""
    import pandas as pd

    from fileio.xyz_reader import DELIMITERS
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
    ok = np.isfinite(x) & np.isfinite(y)
    if not ok.any():
        raise ValueError("No valid positions")
    names = (df[col["Name"]].astype(str).to_numpy() if "Name" in col
             else np.array([f"S{i + 1:02d}" for i in range(len(x))]))
    d = (pd.to_numeric(df[col["Diameter"]], errors="coerce").to_numpy() if "Diameter" in col
         else np.full(len(x), default_diameter))
    d = np.where(np.isfinite(d) & (d > 0), d, default_diameter)
    return Structures(list(names[ok]), np.column_stack([x[ok], y[ok]]), d[ok]), geo


def read_shp(path, default_diameter=8.0):
    """Shapefile points → (Structures, crs or None, info)."""
    import shapefile

    from core import crs as crsmod
    r = shapefile.Reader(path)
    fields = [f[0] for f in r.fields[1:]]
    low = {f.lower(): f for f in fields}
    nf = next((low[k] for k in ("name", "id", "wtg", "label", "turbine", "structure", "location") if k in low), None)
    df_ = next((low[k] for k in low if any(k.startswith(p) for p in ("diam", "dia", "od"))), None)
    names, xy, dia = [], [], []
    for k, sr in enumerate(r.iterShapeRecords()):
        sh = sr.shape
        if not sh.points:
            continue
        rec = sr.record.as_dict() if hasattr(sr.record, "as_dict") else {}
        p = np.asarray(sh.points, float)[:, :2].mean(axis=0)       # point, or centroid of other shapes
        names.append(str(rec.get(nf)) if nf and rec.get(nf) not in (None, "") else f"S{k + 1:02d}")
        xy.append(p)
        try:
            dv = float(rec.get(df_)) if df_ else np.nan
        except (TypeError, ValueError):
            dv = np.nan
        dia.append(dv if np.isfinite(dv) and dv > 0 else default_diameter)
    if not names:
        raise ValueError("The shapefile has no features")
    prj = os.path.splitext(path)[0] + ".prj"
    crs = crsmod.guess_from_prj(prj) if os.path.isfile(prj) else None
    info = (f"{len(names)} structure(s)" + (f", names from '{nf}'" if nf else "")
            + (f", diameter from '{df_}'" if df_ else ", no diameter field") + (", .prj found" if crs else ", no .prj"))
    return Structures(names, np.asarray(xy), np.asarray(dia)), crs, info


def reproject(st, src, dst):
    from core import crs as crsmod
    x, y = crsmod.transform_xy(st.xy[:, 0], st.xy[:, 1], src, dst)
    st.xy = np.column_stack([x, y])
    return st
