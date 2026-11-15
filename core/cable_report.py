"""Report sheets of the cable analyses (depth of burial, free span, rock berm) — one cable or a batch."""
from __future__ import annotations

import numpy as np

KIND_TITLE = {"dob": "Depth of burial", "span": "Free span", "berm": "Rock berm as-built"}


def _r(v, d=3):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return round(f, d) if np.isfinite(f) else None


def stretches(name, res):
    """Rows of the Stretches sheet: exposed / below target (DoB), free / short spans, berm deficits."""
    k = res["kind"]
    out = []

    def row(typ, g, worst, kp_worst, label=None):
        out.append({"cable": name, "type": typ, "kp_from": _r(g["kp_from"], 4), "kp_to": _r(g["kp_to"], 4),
                    "length": _r(g["length"], 2), "worst": _r(worst), "kp_worst": _r(kp_worst, 4),
                    "e_from": _r(g.get("x_from"), 2), "n_from": _r(g.get("y_from"), 2),
                    "e_to": _r(g.get("x_to"), 2), "n_to": _r(g.get("y_to"), 2)})
    if k == "dob":
        for g in res.get("exposed_segments", []):
            row("Exposed", g, g.get("dob_min"), g.get("kp_at_min"))
        for g in res["segments"]:
            row("Below target", g, g.get("dob_min"), g.get("kp_at_min"))
    elif k == "span":
        for g in res["segments"]:
            row("Free span" if g["flag"] == "SPAN" else "Short span", g, g.get("gap_max"), g.get("kp_at_max"))
    else:
        for g in res["segments"]:
            row("Under-filled crest", g, g.get("error_min"), g.get("kp_at_min"))
        for g in res.get("cover_segments", []):
            row("Cover below minimum", g, g.get("cover_min"), g.get("kp_at_min"))
    out.sort(key=lambda r: (str(r["cable"]), r["kp_from"] if r["kp_from"] is not None else 0, r["type"]))
    return out


def summary_row(name, res):
    s = res["summary"]
    k = res["kind"]
    base = {"cable": name, "length": _r(s.get("length"), 1), "coverage": _r(s.get("coverage_pct"), 1)}
    if k == "dob":
        base.update(below=_r(s["non_compliant"], 1), exposed=_r(s["exposed"], 1), dob_min=_r(s["dob_min"]),
                    kp_min=_r(s["kp_at_min"], 4), dob_mean=_r(s["dob_mean"]), compliant=_r(s["compliant_pct"], 1))
    elif k == "span":
        base.update(spans=int(s["spans"]), span_len=_r(s["span_length_total"], 1), longest=_r(s["longest"], 1),
                    gap_max=_r(s["gap_max"]), short=int(s["short_spans"]))
        if "visible" in s:
            base["visible"] = _r(s["visible"], 1)
    else:
        base = {"cable": name, "footprint": _r(s.get("footprint_area"), 1), "coverage": _r(s.get("coverage_pct"), 1),
                "under_area": _r(s.get("under_area"), 1), "under_vol": _r(s.get("under_volume"), 2),
                "over_vol": _r(s.get("over_volume"), 2), "err_mean": _r(s.get("error_mean")),
                "cover_min": _r(s.get("cover_min"))}
    return base


SUMMARY_COLS = {
    "dob": [("cable", "Cable", "text"), ("length", "Length (m)", "m1"), ("coverage", "Coverage (%)", "pct"),
            ("compliant", "Meets target (%)", "pct"), ("below", "Below target (m)", "m1"),
            ("exposed", "Exposed (m)", "m1"), ("dob_min", "Min DoB (m)", "m"), ("kp_min", "KP of min DoB (km)", "kp"),
            ("dob_mean", "Mean DoB (m)", "m")],
    "span": [("cable", "Cable", "text"), ("length", "Length (m)", "m1"), ("coverage", "Coverage (%)", "pct"),
             ("spans", "Free spans (n)", "int"), ("span_len", "Free-span length (m)", "m1"),
             ("longest", "Longest free span (m)", "m1"), ("gap_max", "Max gap (m)", "m"),
             ("short", "Short spans (n)", "int"), ("visible", "Cable visible (m)", "m1")],
    "berm": [("cable", "Cable", "text"), ("footprint", "Footprint (m²)", "m1"), ("coverage", "Coverage (%)", "pct"),
             ("under_area", "Under-filled (m²)", "m1"), ("under_vol", "Under-filled (m³)", "m"),
             ("over_vol", "Over-filled (m³)", "m"), ("err_mean", "Mean as-built − design (m)", "m"),
             ("cover_min", "Min cover (m)", "m")],
}
STRETCH_COLS = [("cable", "Cable", "text"), ("type", "Type", "text"), ("kp_from", "KP from (km)", "kp"),
                ("kp_to", "KP to (km)", "kp"), ("length", "Length (m)", "m1"), ("worst", "Worst value (m)", "m"),
                ("kp_worst", "KP of worst (km)", "kp"), ("e_from", "E from", "xy"), ("n_from", "N from", "xy"),
                ("e_to", "E to", "xy"), ("n_to", "N to", "xy")]
WORST_NOTE = {"dob": "Worst value = smallest DoB in the stretch (negative = cable above the seabed).",
              "span": "Worst value = largest gap under the cable in the stretch.",
              "berm": "Worst value = largest shortfall (as-built − design) or smallest cover in the stretch."}


def bins_rows(name, res):
    k = res["kind"]
    out = []
    for b in res["bins"]:
        r = {"cable": name, "kp_from": _r(b["kp_from"], 4), "kp_to": _r(b["kp_to"], 4),
             "coverage": _r(100.0 * b["covered"] / b["length"], 1) if b.get("length") else None}
        if k == "dob":
            r.update(vmin=_r(b.get("dob_min")), vmean=_r(b.get("dob_mean")), ok=_r(b.get("ok_pct"), 1))
        elif k == "span":
            r.update(vmin=_r(b.get("gap_min")), vmean=_r(b.get("gap_mean")), vmax=_r(b.get("gap_max")))
        else:
            r.update({a: _r(v) for a, v in b.items() if a not in ("kp_from", "kp_to", "length", "covered")})
        out.append(r)
    return out


def bins_cols(kind, rows):
    head = [("cable", "Cable", "text"), ("kp_from", "KP from (km)", "kp"), ("kp_to", "KP to (km)", "kp"),
            ("coverage", "Coverage (%)", "pct")]
    if kind == "dob":
        return head + [("vmin", "Min DoB (m)", "m"), ("vmean", "Mean DoB (m)", "m"), ("ok", "Meets target (%)", "pct")]
    if kind == "span":
        return head + [("vmin", "Min gap (m)", "m"), ("vmean", "Mean gap (m)", "m"), ("vmax", "Max gap (m)", "m")]
    keys = [k for k in (rows[0] if rows else {}) if k not in ("cable", "kp_from", "kp_to", "coverage")]

    def lab(k):
        for suf, unit, fmt in (("_m3", "m³", "m"), ("_m2", "m²", "m1"), ("_pct", "%", "pct"), ("_m", "m", "m")):
            if k.endswith(suf):
                return k[:-len(suf)].replace("_", " ").capitalize() + f" ({unit})", fmt
        return k.replace("_", " ").capitalize() + " (m)", "m"
    return head + [(k, *lab(k)) for k in keys]


def sheets(items, crs_label="", notes=()):
    """items: [(cable name, result)] of one kind. Returns the report sheets."""
    from core.report_xlsx import settings_sheet
    kind = items[0][1]["kind"]
    srows = [summary_row(n, r) for n, r in items]
    cols = [c for c in SUMMARY_COLS[kind] if any(r.get(c[0]) is not None for r in srows) or c[0] == "cable"]

    def hl_sum(r):
        if kind == "dob":
            return "bad" if (r.get("exposed") or 0) > 0 else ("warn" if (r.get("below") or 0) > 0 else "good")
        if kind == "span":
            return "bad" if (r.get("spans") or 0) > 0 else "good"
        return "bad" if (r.get("under_area") or 0) > 0 else "good"
    st_rows = [x for n, r in items for x in stretches(n, r)]
    bad_types = {"Exposed", "Free span", "Under-filled crest", "Cover below minimum"}
    sh = [{"name": "Summary", "columns": cols, "rows": srows, "highlight": hl_sum},
          {"name": "Stretches", "columns": STRETCH_COLS, "rows": st_rows,
           "highlight": lambda r: "bad" if r["type"] in bad_types else "warn", "empty": "no stretches"}]
    brows = [x for n, r in items for x in bins_rows(n, r)]
    sh.append({"name": "KP bins", "columns": bins_cols(kind, brows), "rows": brows})
    p = items[0][1].get("params", {})
    labels = {"target": "Target DoB (m)", "step": "Station step (m)", "bin_m": "KP bin (m)",
              "width": "Seabed corridor ± (m)", "diameter": "Cable diameter (m)", "gap_min": "Minimum gap (m)",
              "len_min": "Minimum free-span length (m)", "search": "Cable search ± (m)", "side": "Seabed side band (m)",
              "method": "Method", "tol": "Tolerance (m)"}
    setting_items = [("Analysis", KIND_TITLE[kind]), ("Cables", len(items)), ("Coordinate system", crs_label)]
    setting_items += [(labels.get(a, a), ("A — cable profile + seabed" if v == "profile" else
                                         "B — point cloud" if v == "points" else v)) for a, v in p.items()]
    base_notes = ["Elevations positive up. DoB = seabed − cable top; gap = cable top − diameter − seabed.",
                  WORST_NOTE[kind]]
    sh.append(settings_sheet(setting_items, base_notes + list(notes)))
    return sh
