"""Scour around foundations (monopiles, jackets legs, piles), one or more survey epochs.

For every structure a local grid (cell ≈ D/40, 0.1–0.5 m) is cut from each epoch's surface. The
scour depth is the reference level minus the seabed (positive = below the reference):

    reference = "farfield"  median seabed of the same epoch in a ring ff_in…ff_out × D around the pile
              = "surface"   a baseline surface (e.g. the pre-installation survey), cell by cell
              = "level"     a fixed elevation

Within the analysis radius (r_max × D, outside the pile itself) the result per structure and epoch:
maximum scour depth (with its distance and bearing from the pile centre), mean depth, scour extent
(largest distance from the pile of the scour area connected to the pile — depth > threshold),
scour area and scour volume (positive depth × cell area), plus radial profiles on n rays.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from core.surface import Dem, Tin, rasterize_tin


def _report(progress, f):
    if progress is not None:
        progress(float(min(max(f, 0.0), 1.0)))


_TRI_BOX = {}


def _tri_boxes(tin):
    """Triangle bounding boxes of a TIN, cached per TIN (several epochs are used in turn)."""
    key = id(tin)
    hit = _TRI_BOX.get(key)
    if hit is None or hit[0] is not tin:
        V, F = tin.vertices, tin.triangles
        xs, ys = V[F, 0], V[F, 1]
        hit = (tin, xs.min(1), xs.max(1), ys.min(1), ys.max(1))
        if len(_TRI_BOX) >= 8:
            _TRI_BOX.clear()
        _TRI_BOX[key] = hit
    return hit[1:]


def clear_cache():
    _TRI_BOX.clear()


def sample_grid(surface, x0, ytop, cell, nr, nc):
    """Surface on a grid; for a TIN only the triangles overlapping the grid are rasterised (fast for
    small windows of a large TIN)."""
    if isinstance(surface, Tin):
        x1, y0 = x0 + nc * cell, ytop - nr * cell
        tx0, tx1, ty0, ty1 = _tri_boxes(surface)
        keep = (tx1 >= x0) & (tx0 <= x1) & (ty1 >= y0) & (ty0 <= ytop)
        return rasterize_tin(surface.vertices, surface.triangles[keep], x0, ytop, cell, nr, nc).astype(np.float64)
    from core.volume import sample_on_grid
    return sample_on_grid(surface, x0, ytop, cell, nr, nc)


def local_grid(surface, cx, cy, half, cell):
    """Square grid (2·half) centred on (cx, cy) → (Z, x0, ytop, n). NaN where the surface is undefined."""
    n = int(np.ceil(2 * half / cell))
    x0, ytop = cx - n * cell / 2.0, cy + n * cell / 2.0
    return sample_grid(surface, x0, ytop, cell, n, n), x0, ytop, n


def _bilinear(Z, x0, ytop, cell, x, y):
    from core.filters import surface_z
    return surface_z(Dem(Z.astype(np.float32), x0, ytop, cell, "l"), x, y)


def _years(d0, d1):
    try:
        a, b = date.fromisoformat(str(d0)), date.fromisoformat(str(d1))
        return (b - a).days / 365.25
    except (TypeError, ValueError):
        return None


def scour_analysis(structures, indices, epochs, baseline=None, r_max=5.0, n_rays=16, threshold=0.5, margin=0.0,
                   cell=None, progress=None):
    """structures: Structures; indices: which structures; epochs: [{"name", "date" ('YYYY-MM-DD' or None),
    "obj": Dem | Tin}] in time order; baseline: {"mode": "farfield", "ff_in": 8, "ff_out": 10} |
    {"mode": "surface", "obj": Dem | Tin, "name"} | {"mode": "level", "level": z}. r_max in diameters."""
    from scipy import ndimage
    base = baseline or {"mode": "farfield", "ff_in": 8.0, "ff_out": 10.0}
    out = []
    table = []
    total = max(len(indices) * len(epochs), 1)
    k = 0
    for si in indices:
        name, (cx, cy), D = structures.names[si], structures.xy[si], float(structures.diameter[si])
        R = r_max * D
        half = max(R, base.get("ff_out", 0) * D if base["mode"] == "farfield" else 0) + D * 0.5
        c = cell or float(np.clip(D / 40.0, 0.1, 0.5))
        n = int(np.ceil(2 * half / c))
        x0, ytop = cx - n * c / 2.0, cy + n * c / 2.0
        xs = x0 + (np.arange(n) + 0.5) * c - cx
        ys = ytop - (np.arange(n) + 0.5) * c - cy
        DX, DY = np.meshgrid(xs, ys)
        Rr = np.hypot(DX, DY)
        BRG = (np.degrees(np.arctan2(DX, DY)) + 360.0) % 360.0         # bearing from north, clockwise
        r_in = D / 2.0 + margin
        ring = (Rr >= r_in) & (Rr <= R)
        # "touching the pile": MBES data often stops short of the pile wall — allow a gap of 0.25 D
        inner = (Rr >= r_in) & (Rr <= r_in + max(2.5 * c, 0.25 * D))
        # stored grids are cropped to the analysis radius (the far-field ring is only needed for the level)
        m = int(np.ceil((R + c) / c))
        mid = n // 2
        crop = (slice(max(mid - m, 0), min(mid + m, n)), slice(max(mid - m, 0), min(mid + m, n)))
        cx0 = x0 + crop[1].start * c
        cytop = ytop - crop[0].start * c
        cn = crop[0].stop - crop[0].start
        ref_grid = None
        if base["mode"] == "surface":
            ref_grid, *_ = local_grid(base["obj"], cx, cy, n * c / 2.0, c)
        th = np.arange(n_rays) * 360.0 / n_rays
        rr = np.arange(r_in, R + 1e-9, c)
        rx = np.sin(np.radians(th))[:, None] * rr[None, :]
        ry = np.cos(np.radians(th))[:, None] * rr[None, :]
        srec = {"name": name, "x": float(cx), "y": float(cy), "diameter": D, "r_max": R, "cell": c,
                "x0": cx0, "ytop": cytop, "n": cn, "bearings": th, "r": rr, "epochs": []}
        prev = None
        for e in epochs:
            Z, *_ = local_grid(e["obj"], cx, cy, n * c / 2.0, c)
            if base["mode"] == "farfield":
                ff = (Rr >= base["ff_in"] * D) & (Rr <= base["ff_out"] * D) & np.isfinite(Z)
                ref = float(np.median(Z[ff])) if ff.sum() >= 10 else np.nan
                REF = np.full_like(Z, ref)
            elif base["mode"] == "surface":
                REF = ref_grid
                ref = float(np.nanmedian(ref_grid[ring])) if np.isfinite(ref_grid[ring]).any() else np.nan
            else:
                ref = float(base["level"])
                REF = np.full_like(Z, ref)
            depth = REF - Z
            valid = ring & np.isfinite(depth)
            row = {"structure": name, "epoch": e["name"], "date": e.get("date") or "", "reference": ref,
                   "coverage_pct": float(100.0 * valid.sum() / max(ring.sum(), 1))}
            if valid.any():
                dv = np.where(valid, depth, -np.inf)
                j = np.unravel_index(int(np.argmax(dv)), dv.shape)
                row.update(max_depth=float(depth[j]), r_at_max=float(Rr[j]), bearing_at_max=float(BRG[j]),
                           mean_depth=float(np.mean(depth[valid])))
                sc = valid & (depth > threshold)
                lab, nl = ndimage.label(sc)
                touch = np.unique(lab[inner & sc])
                touch = touch[touch > 0]
                conn = np.isin(lab, touch) if len(touch) else np.zeros_like(sc)
                row["extent"] = float(Rr[conn].max()) if conn.any() else 0.0
                row["extent_D"] = row["extent"] / D
                row["scour_area"] = float(conn.sum() * c * c)
                row["scour_volume"] = float(np.sum(np.clip(depth[valid], 0, None)) * c * c)
            else:
                row.update(max_depth=np.nan, r_at_max=np.nan, bearing_at_max=np.nan, mean_depth=np.nan,
                           extent=np.nan, extent_D=np.nan, scour_area=np.nan, scour_volume=np.nan)
            if prev is not None:
                row["d_max_depth"] = row["max_depth"] - prev["max_depth"]
                row["d_volume"] = row["scour_volume"] - prev["scour_volume"]
                yrs = _years(prev["date"], row["date"])
                row["years"] = yrs if yrs is not None else np.nan
                row["max_depth_rate"] = (row["d_max_depth"] / yrs) if yrs else np.nan
            prev = row
            table.append(row)
            zr = _bilinear(Z, x0, ytop, c, (cx + rx).ravel(), (cy + ry).ravel()).reshape(rx.shape)
            dr = (REF[0, 0] - zr) if base["mode"] != "surface" else \
                (_bilinear(REF, x0, ytop, c, (cx + rx).ravel(), (cy + ry).ravel()).reshape(rx.shape) - zr)
            # only the depth grid is kept (seabed = reference − depth); z only with a baseline surface
            srec["epochs"].append({"name": e["name"], "date": e.get("date") or "", "row": row,
                                   "ray_z": zr.astype(np.float32), "ray_depth": dr.astype(np.float32),
                                   "depth": np.where(valid, depth, np.nan)[crop].astype(np.float32),
                                   "ref": ref if base["mode"] != "surface" else None,
                                   "z": (np.where(valid, Z, np.nan)[crop].astype(np.float32)
                                         if base["mode"] == "surface" else None)})
            k += 1
            _report(progress, k / total)
        out.append(srec)
    clear_cache()
    return {"kind": "scour", "structures": out, "table": table,
            "params": {"r_max_D": r_max, "n_rays": n_rays, "threshold": threshold, "margin": margin,
                       "baseline": {kk: v for kk, v in base.items() if kk != "obj"},
                       "epochs": [e["name"] for e in epochs]}}
