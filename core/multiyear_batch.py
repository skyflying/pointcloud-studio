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


def stitch(routes, field, gap_tol=1.0):
    """Join routes sharing the value of `field` → [Route] (one per value, ≥ 2 members or 1).
    Members whose ends are more than gap_tol (m) apart are joined through a vertex without a cable level,
    so the gap is not assessed as cable (it would otherwise be a straight, interpolated fake cable)."""
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
        gaps = []
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
            dj = float(np.hypot(*(nxt[0, :2] - cur[-1, :2])))
            if dj <= EPS:
                nxt = nxt[1:]
            elif dj > gap_tol:
                mid = (cur[-1, :2] + nxt[0, :2]) / 2.0
                cur = np.vstack([cur, [[mid[0], mid[1], np.nan]]])
                gaps.append(round(dj, 2))
            cur = np.vstack([cur, nxt])
            used.add(j)
            order.append(members[j].name)
        z = cur[:, 2] if np.isfinite(cur[:, 2]).any() else None
        out.append(Route(f"{field} {val}", cur[:, :2], z=z,
                         meta={"members": order, "attributes": {field: val}, "string": True, "gaps": gaps}))
    return out


def compact(item_id, kind, res):
    """Keep what the batch tables and the workbook need (the per-year arrays are dropped to save memory;
    a single item is recomputed in full when it is opened)."""
    s = res["summary"]
    st = res["st"]
    row = {"item": item_id, "type": kind, "length_m": s["length"], "years": f"{min(s['years'])}–{max(s['years'])}",
           "first_reburial": s["first_reburial_year"], "first_exposed": s["first_exposed_year"],
           "first_span": s["first_span_year"], "ever_below_required_m": s["ever_reburial_m"]}
    if s.get("gaps_m"):
        row["gaps_not_assessed_m"] = s["gaps_m"]          # joined strings: gaps between member cables
    if s["mode"] == "cumulative":
        row.update(campaigns=len(s["campaign_years"]), campaign_years=", ".join(map(str, s["campaign_years"])),
                   total_reburial_m=s["total_reburial_m"], total_hours=s["total_hours"],
                   beyond_trencher=s["beyond_trencher"])
    else:
        row.update(worst_year=s["worst_year"], worst_reburial_m=s["worst_reburial_m"])
    if s.get("total_cost") is not None:
        row.update(total_cost=s["total_cost"], total_cost_pv=s["total_cost_pv"])
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


def _en(item, i0, i1):
    P = item["points"]
    n = len(P["kp"])
    if i0 is None or n == 0:
        return {}
    a, b = max(0, min(int(i0), n - 1)), max(0, min(int(i1), n - 1))
    return {"e_from": float(P["x"][a]), "n_from": float(P["y"][a]), "e_to": float(P["x"][b]), "n_to": float(P["y"][b])}


def workbook_sheets(batch, settings):
    """Formatted report sheets (core.report_xlsx) for a batch — also used for one cable (a batch of one)."""
    from core.report_xlsx import settings_sheet
    items = batch["items"]
    rows = [i["row"] for i in items]
    cum = any("campaigns" in r for r in rows)
    has_cost = any("total_cost" in r for r in rows)
    sc = [("item", "Cable / string", "text"), ("type", "Type", "text"), ("length_m", "Length (m)", "m1"),
          ("years", "Years", "text"), ("first_reburial", "First re-burial (year)", "year"),
          ("first_exposed", "First exposed (year)", "year"), ("first_span", "First free span (year)", "year"),
          ("ever_below_required_m", "Ever below trigger (m)", "m1")]
    if cum:
        sc += [("campaigns", "Campaigns (n)", "int"), ("campaign_years", "Campaign years", "text"),
               ("total_reburial_m", "Total re-burial (m)", "m1"), ("total_hours", "Trencher time (h)", "m1"),
               ("beyond_trencher", "Beyond trencher (n)", "int")]
    else:
        sc += [("worst_year", "Worst year", "year"), ("worst_reburial_m", "Worst-year re-burial (m)", "m1")]
    if has_cost:
        sc += [("total_cost", "Total cost", "money"), ("total_cost_pv", "Total cost, present value", "money")]
    if any("gaps_not_assessed_m" in r for r in rows):
        sc.append(("gaps_not_assessed_m", "Gaps not assessed (m)", "m1"))
    sheets = [{"name": "Summary", "columns": sc, "rows": rows,
               "highlight": lambda r: "bad" if r.get("first_exposed") else ("warn" if r.get("first_reburial") else "good")}]
    py = [dict(r) for i in items for r in i["table"]]
    pc = [("item", "Cable / string", "text"), ("year", "Year", "year"), ("data", "Data", "text"),
          ("dataset", "Seabed dataset", "text"), ("coverage_pct", "Coverage (%)", "pct"),
          ("min_cover", "Min cover (m)", "m"), ("kp_min_cover", "KP of min cover (km)", "kp"),
          ("below_target_m", "Below target (m)", "m1"), ("exposed_m", "Exposed (m)", "m1"),
          ("spans", "Free spans (n)", "int"), ("span_length_m", "Free-span length (m)", "m1"),
          ("longest_span_m", "Longest free span (m)", "m1"), ("max_gap", "Max gap (m)", "m"),
          ("reburial_m", "Re-burial needed (m)", "m1"), ("max_lowering", "Max lowering (m)", "m")]
    if cum:
        pc += [("reburied_m", "Re-buried (m)", "m1"), ("cable_lowered_max", "Cable lowered, max (m)", "m"),
               ("cumulative_reburial_m", "Cumulative re-burial (m)", "m1")]
        if any("settled_m" in r for r in py):
            pc.append(("settled_m", "Settled onto seabed (m)", "m1"))
    if has_cost:
        pc += [("cost", "Cost", "money"), ("cost_pv", "Cost, present value", "money")]
    sheets.append({"name": "Per year", "columns": pc, "rows": py,
                   "highlight": lambda r: "bad" if (r.get("exposed_m") or 0) > 0 else
                   ("warn" if (r.get("reburial_m") or 0) > 0 else None)})
    fr = []
    for i in items:
        for r in i["first"]:
            low = next((v for k, v in r.items() if k.startswith("lowering_")), None)
            fr.append({"item": i["item"], "first_year": r["first_year"], "kp_from": r["kp_from"], "kp_to": r["kp_to"],
                       "length_m": r["length_m"], "first_exposed": r.get("first_exposed"),
                       "first_span": r.get("first_span"), "lowering": low, "times": r.get("times_reburied"),
                       **_en(i, r.get("i0"), r.get("i1"))})
    fc = [("item", "Cable / string", "text"), ("first_year", "First needed (year)", "year"),
          ("kp_from", "KP from (km)", "kp"), ("kp_to", "KP to (km)", "kp"), ("length_m", "Length (m)", "m1"),
          ("first_exposed", "First exposed (year)", "year"), ("first_span", "First free span (year)", "year"),
          ("lowering", "Lowering still needed, last year (m)", "m")]
    if cum:
        fc.append(("times", "Times re-buried (n)", "int"))
    fc += [("e_from", "E from", "xy"), ("n_from", "N from", "xy"), ("e_to", "E to", "xy"), ("n_to", "N to", "xy")]
    sheets.append({"name": "Re-burial stretches", "columns": fc, "rows": fr, "empty": "no re-burial needed"})
    if cum:
        iv = [{"item": i["item"], **{k: v for k, v in r.items() if k not in ("i0", "i1", "item")},
               **_en(i, r.get("i0"), r.get("i1"))} for i in items for r in i["interventions"]]
        ic = [("item", "Cable / string", "text"), ("year", "Year", "year"), ("data", "Data", "text"),
              ("kp_from", "KP from (km)", "kp"), ("kp_to", "KP to (km)", "kp"), ("length_m", "Length (m)", "m1"),
              ("lowering_max", "Lowering, max (m)", "m"), ("lowering_mean", "Lowering, mean (m)", "m"),
              ("cover_after", "Cover after (m)", "m"), ("hours", "Trencher time (h)", "m1"),
              ("beyond_trencher", "Beyond trencher", "text"),
              ("e_from", "E from", "xy"), ("n_from", "N from", "xy"), ("e_to", "E to", "xy"), ("n_to", "N to", "xy")]
        sheets.append({"name": "Interventions", "columns": ic, "rows": iv,
                       "highlight": lambda r: "bad" if r.get("beyond_trencher") else None, "empty": "no interventions"})
    sg = [{"item": i["item"], "year": r["year"], "data": r.get("data"), "type": r["type"], "kp_from": r["kp_from"],
           "kp_to": r["kp_to"], "length_m": r["length_m"], "value_m": r.get("value_m"), **_en(i, r.get("i0"), r.get("i1"))}
          for i in items for r in i["segments"]]
    sheets.append({"name": "Stretches per year",
                   "columns": [("item", "Cable / string", "text"), ("year", "Year", "year"), ("data", "Data", "text"),
                               ("type", "Type", "text"), ("kp_from", "KP from (km)", "kp"), ("kp_to", "KP to (km)", "kp"),
                               ("length_m", "Length (m)", "m1"), ("value_m", "Worst value (m)", "m"),
                               ("e_from", "E from", "xy"), ("n_from", "N from", "xy"), ("e_to", "E to", "xy"),
                               ("n_to", "N to", "xy")],
                   "rows": sg, "highlight": lambda r: "bad" if r["type"] in ("exposed", "span", "free span") else None,
                   "empty": "none"})
    errs = batch.get("errors", [])
    notes = ["Elevations positive up; cover = seabed − cable top; gap = cable top − diameter − seabed.",
             "Worst value: re-burial / exposed = smallest cover, free span = largest gap (m)."]
    notes += [f"Not assessed — {a}: {b}" for a, b in errs]
    sheets.append(settings_sheet(list(settings.items()), notes))
    return sheets


def write_workbook(path, sheets, title=None):
    """Formatted Excel (needs openpyxl) — or one CSV per sheet next to `path`. Returns the files written."""
    from core import report_xlsx
    return report_xlsx.write(path, sheets, title or "Multi-year cable assessment", "PointCloud Studio")


