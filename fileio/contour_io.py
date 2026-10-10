"""Contour export: Shapefile (PolyLineZ + ELEV / DEPTH / TYPE) and DXF R12 (3D polylines, layers)."""
from __future__ import annotations

import os

import numpy as np

LAYER_MINOR = "CONTOUR_MINOR"
LAYER_MAJOR = "CONTOUR_MAJOR"
LAYER_LABEL = "CONTOUR_LABEL"
LAYER_ZERO = "CONTOUR_ZERO"          # 0 m line (coastline on topo-bathy data)
DEFAULT_LABELS = {"which": "major", "height": 2.0, "spacing": 100.0, "depth": True, "decimals": 1}


def label_positions(line, spacing, min_len, chord=None):
    """Points along a line (every `spacing`, starting at spacing/2) with an upright rotation angle (deg)."""
    xy = line[:, :2]
    seg = np.hypot(*np.diff(xy, axis=0).T)
    cum = np.r_[0, np.cumsum(seg)]
    L = cum[-1]
    if L < min_len:
        return []
    dists = np.arange(spacing / 2, L, spacing) if L >= spacing else np.array([L / 2])
    out = []
    for d in dists:
        i = int(np.clip(np.searchsorted(cum, d, side="right") - 1, 0, len(seg) - 1))
        t = (d - cum[i]) / max(seg[i], 1e-12)
        p = xy[i] + (xy[i + 1] - xy[i]) * t
        # direction over a chord about as long as the label, so wiggles do not tilt the text
        w = chord if chord else 0.0
        if w > 0:
            pa = np.array([np.interp(max(d - w / 2, 0), cum, xy[:, k]) for k in (0, 1)])
            pb = np.array([np.interp(min(d + w / 2, L), cum, xy[:, k]) for k in (0, 1)])
            dx, dy = pb - pa
        else:
            dx, dy = xy[i + 1] - xy[i]
        ang = np.degrees(np.arctan2(dy, dx))
        if ang > 90:
            ang -= 180
        elif ang <= -90:
            ang += 180
        if ang <= -85:              # near-vertical: always read bottom → top (consistent across the sheet)
            ang += 180
        out.append((p[0], p[1], ang))
    return out


def _closed(line):
    return len(line) > 3 and np.allclose(line[0, :2], line[-1, :2])


def write_shp(cs, path, crs=None, progress=None):
    import shapefile
    base = os.path.splitext(path)[0]
    w = shapefile.Writer(base, shapeType=shapefile.POLYLINEZ)
    w.field("ELEV", "N", 14, 3)
    w.field("DEPTH", "N", 14, 3)
    w.field("TYPE", "C", 5)
    n = max(len(cs.lines), 1)
    for i, (ln, lev, mj) in enumerate(zip(cs.lines, cs.levels, cs.major)):
        w.linez([[(float(x), float(y), float(z)) for x, y, z in ln]])
        w.record(float(lev), float(-lev), "zero" if abs(lev) < 1e-9 else ("major" if mj else "minor"))
        if progress and i % 200 == 0:
            progress(i / n)
    w.close()
    if crs is not None:
        with open(base + ".prj", "w", encoding="ascii", errors="ignore") as f:
            f.write(crs.to_wkt("WKT1_ESRI"))
    if progress:
        progress(1.0)


def write_dxf(cs, path, progress=None, labels=None):
    """DXF R12: every contour is a 3D POLYLINE (70 = 8, closed adds 1) with VERTEX z = contour elevation.
    labels: dict(which 'major'|'all'|'none', height m, spacing m, depth bool, decimals) → TEXT on CONTOUR_LABEL,
    placed at the contour's elevation, rotated along the line and kept upright."""
    lab = dict(DEFAULT_LABELS, **(labels or {}))
    out = []
    a = out.append
    a("0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n9\n$INSUNITS\n70\n6\n0\nENDSEC\n")
    a("0\nSECTION\n2\nTABLES\n")
    a("0\nTABLE\n2\nLTYPE\n70\n1\n0\nLTYPE\n2\nCONTINUOUS\n70\n0\n3\nSolid line\n72\n65\n73\n0\n40\n0.0\n0\nENDTAB\n")
    a("0\nTABLE\n2\nSTYLE\n70\n1\n0\nSTYLE\n2\nSTANDARD\n70\n0\n40\n0.0\n41\n1.0\n50\n0.0\n71\n0\n"
      "42\n2.5\n3\ntxt\n4\n\n0\nENDTAB\n")
    a("0\nTABLE\n2\nLAYER\n70\n4\n")
    a(f"0\nLAYER\n2\n{LAYER_MINOR}\n70\n0\n62\n8\n6\nCONTINUOUS\n")
    a(f"0\nLAYER\n2\n{LAYER_MAJOR}\n70\n0\n62\n7\n6\nCONTINUOUS\n")
    a(f"0\nLAYER\n2\n{LAYER_LABEL}\n70\n0\n62\n2\n6\nCONTINUOUS\n")
    a(f"0\nLAYER\n2\n{LAYER_ZERO}\n70\n0\n62\n4\n6\nCONTINUOUS\n")
    a("0\nENDTAB\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n")
    n = max(len(cs.lines), 1)
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("".join(out))
        for i, (ln, mj) in enumerate(zip(cs.lines, cs.major)):
            layer = LAYER_ZERO if abs(float(ln[0, 2])) < 1e-9 else (LAYER_MAJOR if mj else LAYER_MINOR)
            closed = _closed(ln)
            pts = ln[:-1] if closed else ln
            f.write(f"0\nPOLYLINE\n8\n{layer}\n66\n1\n70\n{8 + (1 if closed else 0)}\n10\n0.0\n20\n0.0\n30\n0.0\n")
            f.write("".join(f"0\nVERTEX\n8\n{layer}\n10\n{x:.3f}\n20\n{y:.3f}\n30\n{z:.3f}\n70\n32\n" for x, y, z in pts))
            f.write(f"0\nSEQEND\n8\n{layer}\n")
            if lab["which"] == "all" or (lab["which"] == "major" and mj):
                z = float(ln[0, 2])
                txt = f"{(-z if lab['depth'] else z):.{int(lab['decimals'])}f}"
                h = float(lab["height"])
                text_len = h * max(len(txt), 1) * 0.7
                for x, y, ang in label_positions(ln, float(lab["spacing"]), 4 * text_len, chord=text_len):
                    f.write(f"0\nTEXT\n8\n{LAYER_LABEL}\n10\n{x:.3f}\n20\n{y:.3f}\n30\n{z:.3f}\n40\n{h:.3f}\n"
                            f"1\n{txt}\n50\n{ang:.3f}\n7\nSTANDARD\n72\n1\n"
                            f"11\n{x:.3f}\n21\n{y:.3f}\n31\n{z:.3f}\n73\n2\n")
            if progress and i % 200 == 0:
                progress(i / n)
        f.write("0\nENDSEC\n0\nEOF\n")
    if progress:
        progress(1.0)


def export_contours(cs, path, crs=None, progress=None, labels=None):
    if path.lower().endswith(".dxf"):
        write_dxf(cs, path, progress, labels)
    else:
        write_shp(cs, path, crs, progress)
