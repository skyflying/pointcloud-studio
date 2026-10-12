"""Multi-year cable assessment: one cable profile against the seabed of many years (surveys and / or
modelled predictions).

For every year, at stations every `step` m along the cable:
    cover  = seabed − cable top                       (depth of burial; < 0 = exposed)
    gap    = (cable top − diameter) − seabed           (> gap_min = free span; ≥ len_min = reportable span)
    need   = cover < required cover → RE-BURIAL with a jetting trencher (the cable is lowered / sand jetted
             back until the required cover is reached); lowering needed = required cover − cover

Re-burial stretches closer than `merge_gap` are joined (a trencher does not stop for a few metres), and
the trenching time is estimated from the trencher progress rate.

mode = "independent": every year against the original cable level (what would be needed if nothing had
       been done before).
mode = "cumulative":  years in order; where re-burial is needed it is carried out in that year — the cable
       is lowered to `reburial_to` cover (limited by the trencher's maximum burial depth `max_cover`) — and
       the following years start from the lowered cable. Interventions, repeats and totals are reported.

The cable is otherwise kept at its level: a cable undermined by scour sags in reality, so spans and gaps are
upper estimates.
"""
from __future__ import annotations

import numpy as np

from core import cable


def _report(progress, f):
    if progress is not None:
        progress(float(min(max(f, 0.0), 1.0)))


def _merge(mask, st, merge_gap):
    """Close gaps shorter than merge_gap (m) between True runs."""
    m = mask.copy()
    r = cable.runs(m)
    for (a0, a1), (b0, _b1) in zip(r[:-1], r[1:]):
        if st["ch"][b0] - st["ch"][a1] <= merge_gap:
            m[a1:b0] = True
    return m


def assess(route, years, diameter=0.2, gap_min=0.1, len_min=10.0, target=1.5, required=1.0, merge_gap=20.0,
           rate=0.0, step=1.0, kp_range=None, width=0.5, mode="independent", reburial_to=None, max_cover=0.0,
           min_len=5.0, progress=None):
    """years: [{"name", "year" (int), "simulated" (bool), "obj": Dem | Tin | PointCloud}].
    rate: trencher progress in m / h (0 = no time estimate)."""
    if route.z is None:
        raise ValueError(f"'{route.name}' has no cable-top Z — import the cable profile with a cable-top column")
    if not years:
        raise ValueError("Choose at least one year")
    years = sorted(years, key=lambda y: (y["year"], y["name"]))
    st = cable.stations(route, step, kp_range)
    top0 = cable.cable_top(route, st["ch"])
    top = top0.copy()                              # current cable level (lowered by re-burial in cumulative mode)
    to = max(reburial_to if reburial_to is not None else required, required)
    if max_cover and max_cover > 0:
        to = min(to, max_cover)
    interventions = []
    times = np.zeros(len(st["ch"]), int)
    rb_first = np.full(len(st["ch"]), np.nan)        # per station: first / last year re-buried (or needing it)
    rb_last = np.full(len(st["ch"]), np.nan)
    need_count = np.zeros(len(st["ch"]), int)
    cum_len = cum_h = 0.0
    lo, hi = cable._edges(st["ch"])
    w = hi - lo
    rows, segs, series = [], [], {}
    need_any = np.zeros(len(st["ch"]), bool)
    first_need = np.full(len(st["ch"]), np.nan)
    first_exp = np.full(len(st["ch"]), np.nan)
    first_span = np.full(len(st["ch"]), np.nan)
    for k, y in enumerate(years):
        sb = cable.seabed_along(y["obj"], route, st, width)
        cover = sb - top                       # against the current cable level
        gap = (top - diameter) - sb
        ok = np.isfinite(cover)
        exposed = ok & (cover < -cable.EPS)
        below = ok & (cover < target - cable.EPS)
        span_m = ok & (gap > gap_min + cable.EPS)
        span_segs = cable.segments_from(st, span_m, gap, "gap", "max", route)
        for s in span_segs:
            s["flag"] = "SPAN" if s["length"] >= len_min else "short"
        need = ok & (cover < required - cable.EPS)
        work = _merge(need, st, merge_gap) & ok
        for i0, i1 in cable.runs(work):                 # too short to mobilise a trencher for
            if w[i0:i1 + 1].sum() < min_len:
                work[i0:i1 + 1] = False
        need = need & work
        need_count += work.astype(int)
        rb_first[np.isnan(rb_first) & work] = y["year"]
        rb_last[work] = y["year"]
        lower = np.where(need, required - cover, 0.0)
        exp_segs = cable.segments_from(st, exposed, cover, "cover", "min", route)
        work_segs = cable.segments_from(st, work, lower, "lowering", "max", route)
        L_work = float(w[work].sum())
        long_spans = [s for s in span_segs if s["flag"] == "SPAN"]
        row = {"year": int(y["year"]), "data": "simulated" if y.get("simulated") else "survey", "dataset": y["name"],
               "coverage_pct": float(100 * w[ok].sum() / w.sum()) if w.sum() else 0.0,
               "min_cover": float(np.nanmin(cover)) if ok.any() else np.nan,
               "kp_min_cover": float(st["kp"][int(np.nanargmin(cover))]) if ok.any() else np.nan,
               "below_target_m": float(w[below].sum()), "exposed_m": float(w[exposed].sum()),
               "exposed_segments": len(exp_segs), "spans": len(long_spans),
               "span_length_m": float(sum(s["length"] for s in long_spans)),
               "longest_span_m": float(max((s["length"] for s in span_segs), default=0.0)),
               "max_gap": float(np.nanmax(gap)) if span_m.any() else 0.0,
               "reburial_m": L_work, "reburial_segments": len(work_segs),
               "max_lowering": float(lower.max()) if need.any() else 0.0,
               "mean_lowering": float((lower * w)[need].sum() / w[need].sum()) if need.any() else 0.0}
        if rate and rate > 0:
            row["trenching_h"] = L_work / rate
        rows.append(row)
        for kind_, ss, key in (("exposed", exp_segs, "cover_min"), ("re-burial", work_segs, "lowering_max")):
            for s in ss:
                segs.append({"year": int(y["year"]), "data": row["data"], "type": kind_, "kp_from": s["kp_from"],
                             "kp_to": s["kp_to"], "length_m": s["length"], "value_m": s.get(key, np.nan),
                             "i0": s["i0"], "i1": s["i1"]})
        for s in span_segs:
            segs.append({"year": int(y["year"]), "data": row["data"],
                         "type": "free span" if s["flag"] == "SPAN" else "short span", "kp_from": s["kp_from"],
                         "kp_to": s["kp_to"], "length_m": s["length"], "value_m": s.get("gap_max", np.nan),
                         "i0": s["i0"], "i1": s["i1"]})
        for arr, m in ((first_need, need), (first_exp, exposed), (first_span, span_m)):
            arr[np.isnan(arr) & m] = y["year"]
        need_any |= need
        top_before = top.copy()
        if mode == "cumulative" and work.any():
            # re-bury in this year: lower the cable to `to` cover wherever it has less (a trencher only lowers)
            new = sb - to
            lowered = work & np.isfinite(new) & (new < top)
            for s in work_segs:
                sl = slice(s["i0"], s["i1"] + 1)
                lw = np.where(lowered[sl], top[sl] - new[sl], 0.0)
                beyond = bool(max_cover and max_cover > 0 and np.nanmax(required - cover[sl]) > max_cover - 1e-9) \
                    if np.isfinite(cover[sl]).any() else False
                interventions.append({"year": int(y["year"]), "data": row["data"], "kp_from": s["kp_from"],
                                      "kp_to": s["kp_to"], "length_m": s["length"],
                                      "lowering_max": float(lw.max()) if len(lw) else 0.0,
                                      "lowering_mean": float(lw[lw > 0].mean()) if (lw > 0).any() else 0.0,
                                      "cover_after": float(to), "hours": s["length"] / rate if rate else np.nan,
                                      "beyond_trencher": "yes" if beyond else "",
                                      "i0": s["i0"], "i1": s["i1"]})
            top = np.where(lowered, new, top)
            times += work.astype(int)
            cum_len += L_work
            cum_h += L_work / rate if rate else 0.0
            row["reburied_m"] = L_work
            row["cable_lowered_max"] = float(np.nanmax(np.where(lowered, top_before - top, 0.0)))
        if mode == "cumulative":
            row["cumulative_reburial_m"] = cum_len
            if rate:
                row["cumulative_hours"] = cum_h
        series[y["year"]] = {"seabed": sb, "cover": cover, "gap": gap, "work": work, "simulated": bool(y.get("simulated")),
                             "top": top_before, "top_after": top.copy()}
        _report(progress, (k + 1) / len(years))
    # re-burial stretches over all years (joined like the trencher would), with the first year each needs it
    first = []
    allw = _merge(need_any, st, merge_gap)
    last = series[years[-1]["year"]]
    for i0, i1 in cable.runs(allw):
        sl = slice(i0, i1 + 1)

        def fmin(a):
            v = a[sl]
            return float(np.nanmin(v)) if np.isfinite(v).any() else np.nan
        lw = np.where(np.isfinite(last["cover"][sl]), required - last["cover"][sl], np.nan)
        first.append({"first_year": int(fmin(first_need)) if np.isfinite(fmin(first_need)) else None,
                      "kp_from": float(np.interp(lo[i0], st["ch"], st["kp"])),
                      "kp_to": float(np.interp(hi[i1], st["ch"], st["kp"])), "length_m": float(w[sl].sum()),
                      "first_exposed": fmin(first_exp), "first_span": fmin(first_span),
                      f"lowering_{years[-1]['year']}": float(np.nanmax(lw)) if np.isfinite(lw).any() else np.nan,
                      "times_reburied": int(times[sl].max()) if mode == "cumulative" else None,
                      "i0": int(i0), "i1": int(i1)})
    first.sort(key=lambda r: (r["first_year"] or 9999, r["kp_from"]))
    worst = max(rows, key=lambda r: (round(r["reburial_m"] / 10.0), r["max_lowering"]))
    summary = {"years": [r["year"] for r in rows], "n_years": len(rows), "length": float(w.sum()), "mode": mode,
               "interventions": len(interventions), "campaign_years": sorted({i["year"] for i in interventions}),
               "total_reburial_m": cum_len, "total_hours": cum_h if rate else None,
               "beyond_trencher": sum(1 for i in interventions if i["beyond_trencher"]),
               "first_reburial_year": int(np.nanmin(first_need)) if np.isfinite(first_need).any() else None,
               "first_exposed_year": int(np.nanmin(first_exp)) if np.isfinite(first_exp).any() else None,
               "first_span_year": int(np.nanmin(np.where(np.isfinite(first_span), first_span, np.inf)))
               if np.isfinite(first_span).any() else None,
               "worst_year": worst["year"], "worst_reburial_m": worst["reburial_m"],
               "ever_reburial_m": float(w[need_any].sum())}
    for r_ in first:
        if r_["times_reburied"] is None:
            del r_["times_reburied"]
    return {"kind": "multiyear", "route": route.name, "st": st, "cable_top": top0, "cable_top_final": top,
            "series": series, "table": rows, "segments": segs, "first": first, "interventions": interventions,
            "points": {"count": times if mode == "cumulative" else need_count, "first_year": rb_first,
                       "last_year": rb_last},
            "summary": summary,
            "params": {"diameter": diameter, "gap_min": gap_min, "len_min": len_min, "target": target,
                       "required": required, "merge_gap": merge_gap, "rate": rate, "step": step, "mode": mode,
                       "reburial_to": to, "max_cover": max_cover, "min_len": min_len}}
