"""Sand wave migration along a route from repeated surveys, and a forecast of cable burial.

Profiles of every epoch are sampled along the route and split into a long-wave part (running mean
over `hp_len` m — the regional seabed) and the bedform part (profile − long wave).

Migration per window (length `window`, every `spacing` m along the route), for each epoch pair:
  * 2D (default for surfaces): normalised cross-correlation of the two seabed patches (plane removed,
    Hann taper) within ±max_shift → displacement vector (dx, dy), speed, direction (azimuth the
    bedforms move TOWARDS), and its along-route component;
  * profile (fallback / no surfaces): 1D cross-correlation of the bedform profiles → along-route
    shift only.
Windows whose correlation peak is below `min_corr` or whose bedforms are lower than `min_height`
are marked unreliable and not used further.

Crests: local maxima of the bedform profile with prominence ≥ min_height, matched between the
first and the last epoch (nearest crest in the direction of the migration, within max_shift).

Forecast (needs a cable profile): the bedform part of the LAST survey is moved along the route by
the apparent along-route speed × years (interpolated between reliable windows; for oblique crests the
pattern passes a point on the route faster than the bedforms migrate), the long-wave part is kept, and
DoB = forecast seabed − cable top is evaluated for each horizon. This is a linear extrapolation
of the observed along-route migration with unchanged bedform shape — an estimate, not a prediction
of storms or bedform growth.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from core import cable


def _report(progress, f):
    if progress is not None:
        progress(float(min(max(f, 0.0), 1.0)))


def years_between(d0, d1):
    try:
        return (date.fromisoformat(str(d1)) - date.fromisoformat(str(d0))).days / 365.25
    except (TypeError, ValueError):
        return None


def _fill(z):
    ok = np.isfinite(z)
    if ok.sum() < 2:
        return np.where(ok, z, 0.0), ok
    x = np.arange(len(z))
    return np.interp(x, x[ok], z[ok]), ok


def split(z, step, hp_len):
    """→ (bedform part, long-wave part); NaN kept where the profile has no data."""
    from scipy.ndimage import uniform_filter1d
    zf, ok = _fill(z)
    w = max(3, int(round(hp_len / step)) | 1)
    lp = uniform_filter1d(zf, w, mode="nearest")
    hp = zf - lp
    return np.where(ok, hp, np.nan), lp


def _xcorr_1d(a, b_ext, max_lag):
    """Best lag of a inside b_ext (b_ext = b padded by max_lag each side) → (lag, peak corr)."""
    n = len(a)
    a = a - a.mean()
    na = np.sqrt((a * a).sum())
    best, bl = -2.0, 0
    cs = np.full(2 * max_lag + 1, np.nan)
    for i, lag in enumerate(range(-max_lag, max_lag + 1)):
        seg = b_ext[max_lag + lag:max_lag + lag + n]
        s = seg - seg.mean()
        d = na * np.sqrt((s * s).sum())
        if d > 0:
            cs[i] = float((a * s).sum() / d)
            if cs[i] > best:
                best, bl = cs[i], lag
    # parabolic refinement
    i = bl + max_lag
    frac = 0.0
    if 0 < i < len(cs) - 1 and np.all(np.isfinite(cs[i - 1:i + 2])):
        y0, y1, y2 = cs[i - 1], cs[i], cs[i + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            frac = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    return bl + frac, best


def _detrend(P):
    n, m = P.shape
    ok = np.isfinite(P)
    yy, xx = np.mgrid[0:n, 0:m]
    A = np.column_stack([xx[ok], yy[ok], np.ones(ok.sum())])
    coef, *_ = np.linalg.lstsq(A, P[ok], rcond=None)
    R = P - (coef[0] * xx + coef[1] * yy + coef[2])
    return np.where(ok, R, 0.0)


def _match_2d(T, S, max_px):
    """Normalised cross-correlation of template T (n×n) inside search area S ((n+2·max_px)²).
    → (drow, dcol, peak): T found at offset d from the centred position (sub-cell refined)."""
    n = T.shape[0]
    t = T - T.mean()
    tn = np.sqrt((t * t).sum())
    if tn == 0:
        return 0.0, 0.0, 0.0
    N = S.shape[0]
    F = np.fft.fft2(S, (N + n, N + n)) * np.conj(np.fft.fft2(t, (N + n, N + n)))
    num = np.real(np.fft.ifft2(F))[:N - n + 1, :N - n + 1]
    # local sums of S and S² over n×n windows (integral images)
    def box(A):
        c = np.pad(A, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        return c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]
    s1, s2 = box(S), box(S * S)
    var = np.maximum(s2 - s1 * s1 / (n * n), 1e-12)
    C = num / (tn * np.sqrt(var))
    i, j = np.unravel_index(int(np.argmax(C)), C.shape)
    peak = float(C[i, j])

    def refine(a, b, c):
        den = a - 2 * b + c
        return float(np.clip(0.5 * (a - c) / den, -0.5, 0.5)) if abs(den) > 1e-12 else 0.0

    fr = refine(C[i - 1, j], C[i, j], C[i + 1, j]) if 0 < i < C.shape[0] - 1 else 0.0
    fc = refine(C[i, j - 1], C[i, j], C[i, j + 1]) if 0 < j < C.shape[1] - 1 else 0.0
    return (i - max_px) + fr, (j - max_px) + fc, peak


def _crest_normal(T):
    """Dominant gradient direction of a patch (structure tensor) → (unit vector in (col, row) image axes,
    coherence 0…1). Coherence near 1 = long, parallel crests."""
    gy, gx = np.gradient(T)
    jxx, jyy, jxy = (gx * gx).sum(), (gy * gy).sum(), (gx * gy).sum()
    tr = jxx + jyy
    if tr <= 0:
        return np.array([1.0, 0.0]), 0.0
    disc = np.sqrt(max((jxx - jyy) ** 2 + 4 * jxy * jxy, 0.0))
    l1, l2 = (tr + disc) / 2.0, (tr - disc) / 2.0
    ang = 0.5 * np.arctan2(2 * jxy, jxx - jyy)
    return np.array([np.cos(ang), np.sin(ang)]), float((l1 - l2) / (l1 + l2)) if (l1 + l2) > 0 else 0.0


def migration(route, epochs, step=1.0, window=200.0, spacing=50.0, max_shift=40.0, hp_len=400.0, method="2d",
              cell=1.0, min_corr=0.6, min_height=0.3, kp_range=None, cable_route=None, target=1.5,
              horizons=(1, 2, 5, 10), progress=None):
    """epochs: [{"name", "date", "obj": Dem | Tin | PointCloud}] in time order (≥ 2)."""
    from scipy.signal import find_peaks

    from core.scour import clear_cache
    from core.scour import sample_grid as sample_on_grid       # TIN: rasterise only the window's triangles
    if len(epochs) < 2:
        raise ValueError("At least two surveys are needed")
    st = cable.stations(route, step, kp_range)
    ch = st["ch"]
    ne = len(epochs)
    Z = np.vstack([cable.seabed_along(e["obj"], route, st) for e in epochs])
    _report(progress, 0.2)
    HP, LP = zip(*(split(z, step, hp_len) for z in Z))
    HP, LP = np.vstack(HP), np.vstack(LP)
    use2d = method == "2d" and all(type(e["obj"]).__name__ in ("Dem", "Tin") for e in epochs)
    pairs = [(i, i + 1) for i in range(ne - 1)] + ([(0, ne - 1)] if ne > 2 else [])
    half = window / 2.0
    centres = np.arange(ch[0] + half, ch[-1] - half + 1e-6, spacing)
    if len(centres) == 0:
        centres = np.array([(ch[0] + ch[-1]) / 2.0])
    tan = cable._tangents(route, centres)
    cxy = route.xy_at(centres)
    lag_max = int(round(max_shift / step))
    windows = []
    done = 0
    total = max(len(pairs) * len(centres), 1)
    for (a, b) in pairs:
        yrs = years_between(epochs[a].get("date"), epochs[b].get("date"))
        for ci, c in enumerate(centres):
            i0 = int(np.searchsorted(ch, c - half))
            i1 = int(np.searchsorted(ch, c + half))
            seg = HP[a, i0:i1]
            row = {"pair": f"{epochs[a]['name']} → {epochs[b]['name']}", "a": a, "b": b,
                   "kp": float(route.kp_at(np.array([c]))[0]), "ch": float(c), "x": float(cxy[ci, 0]),
                   "y": float(cxy[ci, 1]), "years": yrs if yrs is not None else np.nan}
            good = np.isfinite(seg)
            height = float(np.nanpercentile(seg, 95) - np.nanpercentile(seg, 5)) if good.sum() > 10 else np.nan
            row["bedform_height"] = height
            if use2d:
                n = int(round(window / cell))
                mp = int(round(max_shift / cell))
                x0, ytop = cxy[ci, 0] - n * cell / 2, cxy[ci, 1] + n * cell / 2
                P1 = sample_on_grid(epochs[a]["obj"], x0, ytop, cell, n, n)
                P2 = sample_on_grid(epochs[b]["obj"], x0 - mp * cell, ytop + mp * cell, cell, n + 2 * mp, n + 2 * mp)
                frac = min(np.isfinite(P1).mean(), np.isfinite(P2).mean())
                if frac < 0.7:
                    row.update(dx=np.nan, dy=np.nan, corr=np.nan, note="too little data")
                else:
                    # one plane (from the template area) removed from both, so the search area keeps its slope
                    T = _detrend(P1)
                    plane = P1 - T
                    yy, xx = np.mgrid[0:n + 2 * mp, 0:n + 2 * mp]
                    ok = np.isfinite(P1)
                    A = np.column_stack([xx[mp:mp + n, mp:mp + n][ok] - mp, yy[mp:mp + n, mp:mp + n][ok] - mp,
                                         np.ones(ok.sum())])
                    coef, *_ = np.linalg.lstsq(A, P1[ok], rcond=None)
                    SP = P2 - (coef[0] * (xx - mp) + coef[1] * (yy - mp) + coef[2])
                    SP = np.where(np.isfinite(SP), SP, 0.0)
                    del plane
                    dr, dc, pk = _match_2d(T, SP, mp)
                    d = np.array([dc * cell, -dr * cell])                 # world (dx, dy)
                    nv, coh = _crest_normal(T)
                    nw = np.array([nv[0], -nv[1]])                         # image (col, row) → world (x, y)
                    if coh >= 0.5:
                        # long crests: a shift ALONG the crests is not observable (aperture problem) —
                        # keep the crest-normal component, the direction in which bedforms migrate
                        d = (d @ nw) * nw
                    row.update(dx=float(d[0]), dy=float(d[1]), corr=pk, crest_coherence=coh)
            else:
                ext0 = i0 - lag_max
                if ext0 < 0 or i1 + lag_max > len(ch) or good.mean() < 0.8:
                    row.update(dx=np.nan, dy=np.nan, corr=np.nan, note="too little data")
                else:
                    lag, pk = _xcorr_1d(np.nan_to_num(seg), np.nan_to_num(HP[b, ext0:i1 + lag_max]), lag_max)
                    s = lag * step
                    row.update(dx=float(s * tan[ci, 0]), dy=float(s * tan[ci, 1]), corr=pk)
            if np.isfinite(row.get("dx", np.nan)):
                d = np.array([row["dx"], row["dy"]])
                row["shift"] = float(np.hypot(*d))
                row["along"] = float(d @ tan[ci])
                row["across"] = float(d[0] * tan[ci, 1] - d[1] * tan[ci, 0])
                row["azimuth"] = float((np.degrees(np.arctan2(d[0], d[1])) + 360.0) % 360.0)
                # apparent along-route speed of the bedform pattern (what a point on the cable sees):
                # for crest-normal migration d it is |d|² / (d · t); the profile method measures it directly
                if use2d and row.get("crest_coherence", 0) >= 0.5:
                    app = row["shift"] ** 2 / row["along"] if abs(row["along"]) > 0.2 * row["shift"] else np.nan
                else:
                    app = row["along"]
                row["apparent_along"] = float(app)
                row["rate"] = row["shift"] / yrs if yrs else np.nan
                row["rate_along"] = row["along"] / yrs if yrs else np.nan
                row["rate_apparent"] = app / yrs if yrs and np.isfinite(app) else np.nan
                edge = row["shift"] > 0.95 * max_shift
                weak = not (row["corr"] >= min_corr)
                flat = not (height >= min_height)
                row["reliable"] = not (edge or weak or flat)
                row["note"] = ", ".join(t for t, f in (("at search limit", edge), ("weak correlation", weak),
                                                       ("no bedforms", flat)) if f)
            else:
                row.update(shift=np.nan, along=np.nan, across=np.nan, azimuth=np.nan, rate=np.nan,
                           rate_along=np.nan, apparent_along=np.nan, rate_apparent=np.nan, reliable=False)
            windows.append(row)
            done += 1
            _report(progress, 0.2 + 0.6 * done / total)
    # ---- crests (first vs last epoch)
    crests = []
    pk_idx = []
    for e in range(ne):
        h, _ = _fill(HP[e])
        p, pr = find_peaks(h, prominence=min_height, distance=max(1, int(round(20.0 / step))))
        pk_idx.append((p, pr["prominences"]))
    a, b = 0, ne - 1
    yrs_ab = years_between(epochs[a].get("date"), epochs[b].get("date"))
    main = [w for w in windows if w["a"] == a and w["b"] == b and w.get("reliable")]
    mean_dir = np.sign(np.nanmedian([w["apparent_along"] for w in main])) if main else 0.0
    pb, hb = pk_idx[b]
    for i, hgt in zip(*pk_idx[a]):
        cand = pb[np.abs(ch[pb] - ch[i]) <= max_shift] if len(pb) else np.array([], int)
        if mean_dir != 0 and len(cand):
            fwd = cand[(ch[cand] - ch[i]) * mean_dir >= -2 * step]
            cand = fwd if len(fwd) else cand
        row = {"kp": float(st["kp"][i]), "height": float(hgt), "crest_z": float(Z[a, i])}
        if len(cand):
            j = cand[np.argmin(np.abs(ch[cand] - ch[i]))]
            row.update(kp_last=float(st["kp"][j]), shift_along=float(ch[j] - ch[i]), crest_z_last=float(Z[b, j]),
                       height_last=float(hb[list(pb).index(j)]))
            row["rate_along"] = row["shift_along"] / yrs_ab if yrs_ab else np.nan
        crests.append(row)
    _report(progress, 0.85)
    # ---- forecast
    clear_cache()
    forecast, risk, years_to = [], [], None
    forecast_note = ""
    rel = [w for w in main if np.isfinite(w.get("rate_apparent", np.nan))]
    top_chk = _cable_on_stations(cable_route, st) if cable_route is not None and cable_route.z is not None else None
    if cable_route is not None:
        if cable_route.z is None:
            forecast_note = "Forecast skipped: the chosen cable has no cable-top Z"
        elif not np.isfinite(top_chk).any():
            forecast_note = "Forecast skipped: the cable profile is not within 10 m of the analysed route"
        elif not yrs_ab:
            forecast_note = "Forecast skipped: survey dates are missing"
        elif not rel:
            forecast_note = "Forecast skipped: no reliable migration window"
    if top_chk is not None and np.isfinite(top_chk).any() and len(rel) >= 1 and yrs_ab:
        cs = np.array([w["ch"] for w in rel])
        vs = np.array([w["rate_apparent"] for w in rel])
        o = np.argsort(cs)
        v = np.interp(ch, cs[o], vs[o])                       # m / year along the route
        # cable top on the analysis route's stations (closest point of the cable profile)
        top = top_chk
        hp_last, lp_last = HP[-1], LP[-1]
        hf, okb = _fill(hp_last)
        now = lp_last + hf
        dob_now = np.where(okb, now - top, np.nan)
        hz = sorted(set(float(h) for h in horizons if h > 0))
        tmax = max(hz) if hz else 10.0
        ts = np.arange(0.5, tmax + 1e-9, 0.5)
        years_to = np.full(len(ch), np.nan)
        for t in ts:
            zf = lp_last + np.interp(ch - v * t, ch, hf)
            dob = np.where(okb, zf - top, np.nan)
            newly = np.isnan(years_to) & (dob < 0) & (dob_now >= 0)
            years_to[newly] = t
        for t in hz:
            zf = lp_last + np.interp(ch - v * t, ch, hf)
            dob = np.where(okb, zf - top, np.nan)
            ok = np.isfinite(dob)
            j = int(np.nanargmin(dob)) if ok.any() else None
            forecast.append({"years": t, "dob_min": float(dob[j]) if j is not None else np.nan,
                             "kp_at_min": float(st["kp"][j]) if j is not None else np.nan,
                             "below_target_m": cable._length(st, ok & (dob < target - cable.EPS)),
                             "exposed_m": cable._length(st, ok & (dob < 0))})
        risk = cable.segments_from(st, np.isfinite(years_to), years_to, "years", "min", route)
        series_f = {"dob_now": dob_now, "cable_top": top, "rate_along": v, "years_to_exposure": years_to}
    else:
        series_f = {}
    _report(progress, 1.0)
    series = {f"z_{i}": Z[i] for i in range(ne)}
    series.update({f"bedform_{i}": HP[i] for i in range(ne)})
    series.update(series_f)
    rel_all = [w for w in windows if w["a"] == a and w["b"] == b and w.get("reliable")]
    summary = {"epochs": ne, "pairs": len(pairs), "windows": len(centres), "method": "2D" if use2d else "profile",
               "reliable_windows": len(rel_all), "years": yrs_ab if yrs_ab else np.nan,
               "rate_median": float(np.nanmedian([w["rate"] for w in rel_all])) if rel_all else np.nan,
               "rate_max": float(np.nanmax([w["rate"] for w in rel_all])) if rel_all else np.nan,
               "rate_along_median": float(np.nanmedian([w["rate_along"] for w in rel_all])) if rel_all else np.nan,
               "rate_apparent_median": float(np.nanmedian([w["rate_apparent"] for w in rel_all])) if rel_all
               else np.nan,
               "azimuth_median": _circ_median([w["azimuth"] for w in rel_all]) if rel_all else np.nan,
               "bedform_height_median": float(np.nanmedian([w["bedform_height"] for w in rel_all])) if rel_all
               else np.nan, "crests": len(crests)}
    if risk:
        summary["exposure_segments"] = len(risk)
        summary["first_exposure_years"] = float(np.nanmin(years_to))
    return {"kind": "sandwave", "route": route.name, "st": st, "series": series, "windows": windows,
            "forecast_note": forecast_note,
            "crests": crests, "forecast": forecast, "segments": risk, "summary": summary,
            "epoch_names": [e["name"] for e in epochs], "epoch_dates": [e.get("date") or "" for e in epochs],
            "params": {"step": step, "window": window, "spacing": spacing, "max_shift": max_shift, "hp_len": hp_len,
                       "method": "2D" if use2d else "profile", "cell": cell, "min_corr": min_corr,
                       "min_height": min_height, "target": target, "horizons": list(horizons)}}


def _circ_median(az):
    a = np.radians(np.asarray(az, float))
    a = a[np.isfinite(a)]
    if not len(a):
        return np.nan
    return float((np.degrees(np.arctan2(np.median(np.sin(a)), np.median(np.cos(a)))) + 360.0) % 360.0)


def _cable_on_stations(cable_route, st):
    """Cable-top elevation at the analysis stations: nearest point of the cable profile (≤ 10 m away)."""
    from scipy.spatial import cKDTree
    cc = np.r_[np.arange(0.0, cable_route.length, 0.5), cable_route.length]
    cxy = cable_route.xy_at(cc)
    cz = np.interp(cc, cable_route.cum, cable_route.z)
    d, j = cKDTree(cxy).query(np.column_stack([st["x"], st["y"]]))
    return np.where(d <= 10.0, cz[j], np.nan)
