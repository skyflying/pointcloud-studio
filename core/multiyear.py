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


def _long_runs(mask, w, min_len):
    """Keep only the True runs whose length (sum of station widths) is at least min_len."""
    out = np.zeros(len(mask), bool)
    for i0, i1 in cable.runs(mask):
        if min_len <= 0 or w[i0:i1 + 1].sum() >= min_len - 1e-9:
            out[i0:i1 + 1] = True
    return out


def peak_shave(z, ch, max_slope_deg=15.0):
    """Cut-only slope limit along the cable (peak shaving): z[i] ≤ z[j] + tan(α)·|ch[i] − ch[j]|.
    Exact in one forward and one backward pass (the converged result of the original iterative sweeps);
    each run without gaps is treated separately."""
    z = np.asarray(z, float).copy()
    ch = np.asarray(ch, float)
    k = float(np.tan(np.radians(max_slope_deg)))
    if not np.isfinite(k) or k <= 0:
        return z
    for i0, i1 in cable.runs(np.isfinite(z)):
        a = z[i0:i1 + 1]
        c = ch[i0:i1 + 1]
        fwd = k * c + np.minimum.accumulate(a - k * c)
        bwd = -k * c + np.minimum.accumulate((a + k * c)[::-1])[::-1]
        z[i0:i1 + 1] = np.minimum(a, np.minimum(fwd, bwd))
    return z


def sandwave_cover(isbl, ch, dsbl=None, req_sw=1.5, req_other=0.5, slope_deg=2.5, isbl_dsbl=7.5, grid=5.0):
    """Required burial along the cable: sand-wave areas (ISBL slope > slope_deg, or ISBL − DSBL > isbl_dsbl)
    req_sw, elsewhere req_other — classified on a `grid` m grid and interpolated (as the original script)."""
    ch = np.asarray(ch, float)
    stride = max(1, int(round(grid / max(np.median(np.diff(ch)) if len(ch) > 1 else 1.0, 1e-9))))
    idx = np.arange(0, len(ch), stride)
    if idx[-1] != len(ch) - 1:
        idx = np.r_[idx, len(ch) - 1]
    c10, z10 = ch[idx], np.asarray(isbl, float)[idx]
    with np.errstate(invalid="ignore"):
        slope = np.degrees(np.arctan(np.abs(np.gradient(z10, c10)))) if len(idx) > 1 else np.zeros(len(idx))
        sw = np.isfinite(slope) & (slope > slope_deg)
        if dsbl is not None:
            dd = z10 - np.asarray(dsbl, float)[idx]
            sw |= np.isfinite(dd) & (dd > isbl_dsbl)
    req10 = np.where(sw, req_sw, req_other).astype(float)
    return np.clip(np.interp(ch, c10, req10), min(req_sw, req_other), max(req_sw, req_other))


def assess(route, years, diameter=0.2, gap_min=0.1, len_min=10.0, target=1.5, required=1.0, merge_gap=20.0,
           rate=0.0, step=1.0, kp_range=None, width=0.5, mode="independent", reburial_to=None, max_cover=0.0,
           min_len=5.0, dsbl=None, isbl=None, trigger="cover", min_run=0.0, settle_short=False,
           action="to_cover", lower_by=1.5, cost_per_m=0.0, discount_rate=0.0, base_year=None,
           cable_level="route", req_sandwave=1.5, req_other=0.5, sw_slope_deg=2.5, sw_isbl_dsbl=7.5,
           peak_deg=15.0, sw_grid=5.0, progress=None):
    """years: [{"name", "year" (int), "simulated" (bool), "obj": Dem | Tin | PointCloud | RasterSource}].
    rate: trencher progress in m / h (0 = no time estimate).

    Rules (see the module text; the 'reference' rules follow the original burial-check script):
      trigger      "cover": re-burial where cover < required · "exposed": where the cable is exposed (cover < 0)
      min_run      a triggering stretch must be at least this long, continuously (e.g. 7 m); shorter ones
                   are not re-buried
      settle_short cumulative: a cable exposed over a stretch shorter than min_run (not re-buried) settles
                   onto the seabed (its level becomes the seabed level)
      action       "to_cover": cable := seabed − reburial_to · "lower_by": cable := cable − lower_by
      cost_per_m   re-burial cost per metre; discounted with discount_rate to base_year (default: first year)
      isbl         installation seabed (optional). cable_level="isbl": the initial cable level is the
                   ISBL (peak-shaved: cut-only, slope ≤ peak_deg) minus the required burial — req_sandwave in
                   sand-wave areas (ISBL slope > sw_slope_deg or ISBL − DSBL > sw_isbl_dsbl, on a sw_grid m
                   grid), req_other elsewhere. The route then needs no cable-top Z."""
    if cable_level == "isbl" and isbl is None:
        raise ValueError("Choose the ISBL (installation seabed) to derive the cable level from it")
    if route.z is None and cable_level != "isbl":
        raise ValueError(f"'{route.name}' has no cable-top Z — import the cable profile with a cable-top column, "
                         "or derive the cable level from the ISBL")
    if not years:
        raise ValueError("Choose at least one year")
    years = sorted(years, key=lambda y: (y["year"], y["name"]))
    st = cable.stations(route, step, kp_range)
    isbl_z = cable.seabed_along(isbl, route, st, width) if isbl is not None else None
    dsbl_z = cable.seabed_along(dsbl, route, st, width) if dsbl is not None else None
    isbl_shaved = req_cover = None
    if cable_level == "isbl":
        isbl_shaved = peak_shave(isbl_z, st["ch"], peak_deg)
        req_cover = sandwave_cover(isbl_shaved, st["ch"], dsbl_z, req_sandwave, req_other, sw_slope_deg,
                                   sw_isbl_dsbl, sw_grid)
        top0 = isbl_shaved - req_cover
    else:
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
    on_cable = np.isfinite(top0)                   # stations with a cable level (joined strings: not in the gaps)
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
        need = ok & ((cover < -cable.EPS) if trigger == "exposed" else (cover < required - cable.EPS))
        if min_run and min_run > 0:                    # only continuous stretches of at least min_run trigger
            need = _long_runs(need, w, min_run)
        work = _merge(need, st, merge_gap) & ok
        for i0, i1 in cable.runs(work):                 # too short to mobilise a trencher for
            if w[i0:i1 + 1].sum() < min_len:
                work[i0:i1 + 1] = False
        need = need & work
        need_count += work.astype(int)
        rb_first[np.isnan(rb_first) & work] = y["year"]
        rb_last[work] = y["year"]
        # lowering the trencher has to achieve: to the required cover (independent: what the year needs), or —
        # cumulative — to the cover the cable is actually re-buried to (as in the interventions table)
        goal = to if mode == "cumulative" else required
        if action == "lower_by" and mode == "cumulative":
            lower = np.where(need, float(lower_by), 0.0)
        else:
            lower = np.where(need, np.maximum(goal - cover, 0.0), 0.0)
        exp_segs = cable.segments_from(st, exposed, cover, "cover", "min", route)
        work_segs = cable.segments_from(st, work, lower, "lowering", "max", route)
        L_work = float(w[work].sum())
        long_spans = [s for s in span_segs if s["flag"] == "SPAN"]
        row = {"year": int(y["year"]), "data": "simulated" if y.get("simulated") else "survey", "dataset": y["name"],
               "coverage_pct": float(100 * w[ok].sum() / w[on_cable].sum()) if w[on_cable].sum() else 0.0,
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
            # re-bury in this year: lower the cable to `to` cover wherever it has less (a trencher only lowers).
            # Where the cable already lies at the deepest the trencher can reach (`to` limited by max_cover),
            # nothing can be done: such stretches are reported as beyond the trencher, not as re-burial work.
            if action == "lower_by":
                new = top - float(lower_by)
                if max_cover and max_cover > 0:
                    new = np.maximum(new, sb - max_cover)
            else:
                new = sb - to
            lowered = work & np.isfinite(new) & (new < top - 1e-6)
            done_len = 0.0
            did_mask = np.zeros(len(top), bool)
            for s in work_segs:
                sl = slice(s["i0"], s["i1"] + 1)
                did = bool(lowered[sl].any())
                lw = np.where(lowered[sl], top[sl] - new[sl], 0.0)
                cov_after = np.where(np.isfinite(new[sl]), sb[sl] - np.where(lowered[sl], new[sl], top[sl]), np.nan)
                short = bool(np.isfinite(cov_after).any() and np.nanmin(cov_after) < required - 1e-6)
                beyond = short and bool(max_cover and max_cover > 0)
                if not did and not beyond:
                    continue
                interventions.append({"year": int(y["year"]), "data": row["data"], "kp_from": s["kp_from"],
                                      "kp_to": s["kp_to"], "length_m": s["length"] if did else 0.0,
                                      "lowering_max": float(lw.max()) if len(lw) else 0.0,
                                      "lowering_mean": float(lw[lw > 0].mean()) if (lw > 0).any() else 0.0,
                                      "cover_after": float(np.nanmin(cov_after)) if np.isfinite(cov_after).any()
                                      else float(to),
                                      "hours": (s["length"] / rate if rate else np.nan) if did else 0.0,
                                      "beyond_trencher": ("yes — cover stays below the required cover"
                                                          + ("" if did else " (cable already at the trencher's "
                                                                            "maximum burial; no work possible)"))
                                      if beyond else "",
                                      "i0": s["i0"], "i1": s["i1"]})
                if did:
                    done_len += s["length"]
                    did_mask[sl] = True
            top = np.where(lowered, new, top)
            times += did_mask.astype(int)
            row["cost"] = done_len * cost_per_m if cost_per_m else 0.0
            cum_len += done_len
            cum_h += done_len / rate if rate else 0.0
            row["reburied_m"] = done_len
            row["cable_lowered_max"] = float(np.nanmax(np.where(lowered, top_before - top, 0.0)))
        if mode == "cumulative" and settle_short:
            # exposed over a short stretch (not re-buried): the cable settles onto the seabed
            short_exp = exposed & ~_long_runs(exposed, w, min_run if min_run else 0.0) & ~work
            if short_exp.any():
                top = np.where(short_exp & np.isfinite(sb), sb, top)
                row["settled_m"] = float(w[short_exp].sum())
        if cost_per_m:
            if "cost" not in row:
                row["cost"] = (L_work if mode != "cumulative" else 0.0) * cost_per_m
            by = base_year if base_year else years[0]["year"]
            row["cost_pv"] = row["cost"] / (1.0 + discount_rate) ** max(0, int(y["year"]) - int(by))
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
        # lowering still needed in the last year (none where the cover is already enough)
        lw = np.where(np.isfinite(last["cover"][sl]), np.maximum(required - last["cover"][sl], 0.0), np.nan)
        first.append({"first_year": int(fmin(first_need)) if np.isfinite(fmin(first_need)) else None,
                      "kp_from": float(np.interp(lo[i0], st["ch"], st["kp"])),
                      "kp_to": float(np.interp(hi[i1], st["ch"], st["kp"])), "length_m": float(w[sl].sum()),
                      "first_exposed": fmin(first_exp), "first_span": fmin(first_span),
                      f"lowering_{years[-1]['year']}": float(np.nanmax(lw)) if np.isfinite(lw).any() else np.nan,
                      "times_reburied": int(times[sl].max()) if mode == "cumulative" else None,
                      "i0": int(i0), "i1": int(i1)})
    first.sort(key=lambda r: (r["first_year"] or 9999, r["kp_from"]))
    worst = max(rows, key=lambda r: (round(r["reburial_m"] / 10.0), r["max_lowering"]))
    summary = {"years": [r["year"] for r in rows], "n_years": len(rows), "length": float(w[on_cable].sum()),
               "mode": mode, "gaps_m": float(w[~on_cable].sum()),
               "interventions": sum(1 for i in interventions if i["length_m"] > 0),
               "campaign_years": sorted({i["year"] for i in interventions if i["length_m"] > 0}),
               "total_reburial_m": cum_len, "total_hours": cum_h if rate else None,
               "beyond_trencher": sum(1 for i in interventions if i["beyond_trencher"]),
               "first_reburial_year": int(np.nanmin(first_need)) if np.isfinite(first_need).any() else None,
               "first_exposed_year": int(np.nanmin(first_exp)) if np.isfinite(first_exp).any() else None,
               "first_span_year": int(np.nanmin(np.where(np.isfinite(first_span), first_span, np.inf)))
               if np.isfinite(first_span).any() else None,
               "worst_year": worst["year"], "worst_reburial_m": worst["reburial_m"],
               "ever_reburial_m": float(w[need_any].sum())}
    if cost_per_m:
        summary["total_cost"] = float(sum(r.get("cost", 0.0) for r in rows)) if mode == "cumulative" else None
        summary["total_cost_pv"] = float(sum(r.get("cost_pv", 0.0) for r in rows)) if mode == "cumulative" else None
        summary["worst_year_cost"] = float(max((r.get("cost", 0.0) for r in rows), default=0.0))
    for r_ in first:
        if r_["times_reburied"] is None:
            del r_["times_reburied"]
    return {"kind": "multiyear", "route": route.name, "st": st, "cable_top": top0, "cable_top_final": top,
            "dsbl": dsbl_z, "isbl": isbl_z, "isbl_shaved": isbl_shaved, "req_cover": req_cover,
            "series": series, "table": rows, "segments": segs, "first": first, "interventions": interventions,
            "points": {"count": times if mode == "cumulative" else need_count, "first_year": rb_first,
                       "last_year": rb_last},
            "summary": summary,
            "params": {"diameter": diameter, "gap_min": gap_min, "len_min": len_min, "target": target,
                       "required": required, "merge_gap": merge_gap, "rate": rate, "step": step, "mode": mode,
                       "reburial_to": to, "max_cover": max_cover, "min_len": min_len, "trigger": trigger,
                       "min_run": min_run, "settle_short": settle_short, "action": action, "lower_by": lower_by,
                       "cost_per_m": cost_per_m, "discount_rate": discount_rate, "cable_level": cable_level,
                       "req_sandwave": req_sandwave, "req_other": req_other}}
