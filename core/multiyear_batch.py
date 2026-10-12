"""Batch multi-year assessment of many cables (and of cable strings joined from them) + Excel workbook.

Strings: cable segments sharing a value of a "string" attribute are joined into one continuous cable by
nearest-endpoint stitching — start at the outer end (the endpoint whose nearest other endpoint is
farthest away), then repeatedly append the closest remaining segment, reversed if its far end is closer.
Chainage / KP of a joined string runs from 0 at its start.
"""
from __future__ import annotations

import numpy as np

from core.route import Route

EPS = 1e-6


def attribute_fields(routes):
    """Attribute names available on the routes (from GIS imports)."""
    out = []
    for r in routes:
        for k in (r.meta.get("attributes") or {}):
            if k not in out:
                out.append(k)
    return out


def _outer_start(segs):
    """(segment index, reversed) of the chain start: the endpoint whose nearest other endpoint is farthest."""
    ends = []
    for i, s in enumerate(segs):
        ends.append((i, False, s[0, :2]))
        ends.append((i, True, s[-1, :2]))
    if len(ends) <= 2:
        return 0, False
    P = np.array([e[2] for e in ends])
    d = np.hypot(P[:, None, 0] - P[None, :, 0], P[:, None, 1] - P[None, :, 1])
    np.fill_diagonal(d, np.inf)
    for k in range(0, len(ends), 2):              # the two ends of one segment are not "other" endpoints
        d[k, k + 1] = d[k + 1, k] = np.inf
    k = int(np.argmax(d.min(axis=1)))
    return ends[k][0], ends[k][1]


def stitch(routes, field):
    """Join routes sharing the value of `field` → [Route] (one per value, ≥ 2 members or 1)."""
    groups = {}
    for r in routes:
        v = (r.meta.get("attributes") or {}).get(field)
        if v in (None, ""):
            continue
        groups.setdefault(str(v), []).append(r)
    out = []
    for val, members in groups.items():
        segs = []
        for r in members:
            z = r.z if r.z is not None else np.full(len(r.xy), np.nan)
            segs.append(np.column_stack([r.xy, z]))
        i0, rev = _outer_start(segs)
        cur = segs[i0][::-1] if rev else segs[i0]
        used = {i0}
        order = [members[i0].name]
        while len(used) < len(segs):
            end = cur[-1, :2]
            best = None
            for j, s in enumerate(segs):
                if j in used:
                    continue
                d0 = np.hypot(*(s[0, :2] - end))
                d1 = np.hypot(*(s[-1, :2] - end))
                d, rv = (d1, True) if d1 < d0 else (d0, False)
                if best is None or d < best[0]:
                    best = (d, j, rv)
            _d, j, rv = best
            nxt = segs[j][::-1] if rv else segs[j]
            if np.hypot(*(nxt[0, :2] - cur[-1, :2])) <= EPS:
                nxt = nxt[1:]
            cur = np.vstack([cur, nxt])
            used.add(j)
            order.append(members[j].name)
        z = cur[:, 2] if np.isfinite(cur[:, 2]).any() else None
        out.append(Route(f"{field} {val}", cur[:, :2], z=z,
                         meta={"members": order, "attributes": {field: val}, "string": True}))
    return out


def compact(item_id, kind, res):
    """Keep what the batch tables and the workbook need (the per-year arrays are dropped to save memory;
    a single item is recomputed in full when it is opened)."""
    s = res["summary"]
    st = res["st"]
    row = {"item": item_id, "type": kind, "length_m": s["length"], "years": f"{min(s['years'])}–{max(s['years'])}",
           "first_reburial": s["first_reburial_year"], "first_exposed": s["first_exposed_year"],
           "first_span": s["first_span_year"], "ever_below_required_m": s["ever_reburial_m"]}
    if s["mode"] == "cumulative":
        row.update(campaigns=len(s["campaign_years"]), campaign_years=", ".join(map(str, s["campaign_years"])),
                   total_reburial_m=s["total_reburial_m"], total_hours=s["total_hours"],
                   beyond_trencher=s["beyond_trencher"])
    else:
        row.update(worst_year=s["worst_year"], worst_reburial_m=s["worst_reburial_m"])
    tag = lambda rows: [dict(r, item=item_id) for r in rows]          # noqa: E731
    pts = res["points"]
    points = {"kp": st["kp"], "x": st["x"], "y": st["y"], "cable_top_initial": res["cable_top"],
              "cable_top_final": res["cable_top_final"], "count": pts["count"], "first_year": pts["first_year"],
              "last_year": pts["last_year"]}
    return {"item": item_id, "type": kind, "row": row, "table": tag(res["table"]),
            "interventions": tag(res["interventions"]), "first": tag(res["first"]), "segments": tag(res["segments"]),
            "points": points}


def run_batch(items, years, params, progress=None):
    """items: [(id, 'cable' | 'string', Route)]; years / params as core.multiyear.assess.
    Returns {"items": [compact], "errors": [(id, message)]}."""
    from core import multiyear
    out, errors = [], []
    n = max(len(items), 1)
    for k, (iid, kind, route) in enumerate(items):
        sub = (lambda f, k=k: progress((k + f) / n)) if progress else None
        try:
            res = multiyear.assess(route, years, progress=sub, **params)
            out.append(compact(iid, kind, res))
        except Exception as e:  # noqa: BLE001 — one bad cable must not stop the batch
            errors.append((iid, f"{type(e).__name__}: {e}"))
        if progress:
            progress((k + 1) / n)
    return {"items": out, "errors": errors, "params": params, "years": [y["year"] for y in years]}


# ================================================================ workbook
def _clean(rows):
    out = []
    for r in rows:
        d = {}
        for k, v in r.items():
            if k in ("i0", "i1"):
                continue
            if isinstance(v, (float, np.floating)) and not np.isfinite(v):
                v = None
            elif isinstance(v, np.generic):
                v = v.item()
            d[k] = v
        out.append(d)
    return out


def workbook_sheets(batch, settings):
    """{sheet name: list of row dicts} for a batch (also used for one cable: a batch of one)."""
    items = batch["items"]
    sheets = {"Summary": _clean([i["row"] for i in items]),
              "PerYear": _clean([r for i in items for r in i["table"]]),
              "Interventions": _clean([r for i in items for r in i["interventions"]]),
              "ReburialStretches": _clean([r for i in items for r in i["first"]]),
              "Segments": _clean([r for i in items for r in i["segments"]])}
    pts = []
    for i in items:
        P = i["points"]
        for k in range(len(P["kp"])):
            pts.append({"item": i["item"], "kp": float(P["kp"][k]), "x": float(P["x"][k]), "y": float(P["y"][k]),
                        "cable_top_initial": float(P["cable_top_initial"][k]),
                        "cable_top_final": float(P["cable_top_final"][k]), "reburial_count": int(P["count"][k]),
                        "first_year": float(P["first_year"][k]), "last_year": float(P["last_year"][k])})
    sheets["Points"] = _clean(pts)
    sheets["Errors"] = [{"item": a, "error": b} for a, b in batch.get("errors", [])]
    sheets["Settings"] = [{"item": k, "value": str(v)} for k, v in settings.items()]
    return {k: v for k, v in sheets.items() if v or k in ("Summary", "Settings")}


def write_workbook(path, sheets):
    """Excel (.xlsx, needs openpyxl) — or, without openpyxl, one CSV per sheet next to `path`.
    Returns the list of files written."""
    import os

    import pandas as pd
    base = os.path.splitext(path)[0]
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        files = []
        for name, rows in sheets.items():
            f = f"{base}_{name}.csv"
            pd.DataFrame(rows).to_csv(f, index=False, encoding="utf-8-sig")
            files.append(f)
        return files
    with pd.ExcelWriter(base + ".xlsx", engine="openpyxl") as xw:
        for name, rows in sheets.items():
            df = pd.DataFrame(rows)
            if name == "Points" and len(df) > 1_000_000:          # Excel row limit
                df = df.iloc[:1_000_000]
            df.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max(len(str(col[0].value or "")), *(len(f"{c.value}") for c in col[1:50] if c.value is not None)) \
                    if len(col) > 1 else len(str(col[0].value or ""))
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 40)
    return [base + ".xlsx"]
