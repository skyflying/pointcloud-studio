"""Georeferencing of a reconstruction: camera GPS (EXIF), ground control points, or levelling only.

The model is moved with one similarity transform  X' = s·R·X + t  (cameras and points together)."""
from __future__ import annotations

import csv
import os

import numpy as np

from core.sfm.geometry import R_to_rvec, degenerate_layout, umeyama, umeyama_ransac, undistort_normalized


def apply_similarity(rec, s, R, t, extra=None):
    """Transform the reconstruction in place. extra: list of (n, 3) arrays transformed and returned."""
    rec.X = s * rec.X @ R.T + t
    for i in np.flatnonzero(rec.registered):
        Ri = rec.R(i)
        C = rec.centre(i)
        Rn = Ri @ R.T
        Cn = s * R @ C + t
        rec.cams[i, :3] = R_to_rvec(Rn)
        rec.cams[i, 3:] = -Rn @ Cn
    out = [s * np.asarray(e) @ R.T + t for e in (extra or [])]
    rec.transform = (s * R, t) if getattr(rec, "transform", None) is None else (
        s * R @ rec.transform[0], s * R @ rec.transform[1] + t)
    return out


def gps_to_project(lat, lon, alt, epsg):
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, int(epsg), always_xy=True)
    x, y = tr.transform(np.asarray(lon, float), np.asarray(lat, float))
    return np.column_stack([x, y, np.asarray(alt, float)])


def georef_gps(rec, exifs, epsg, thr=0.0, log=print):
    """Similarity from the reconstructed camera centres to their EXIF GPS positions."""
    reg = np.flatnonzero(rec.registered)
    have = [i for i in reg if "lat" in exifs[i] and "lon" in exifs[i]]
    if len(have) < 3:
        raise ValueError(f"Only {len(have)} registered image(s) have GPS positions (≥ 3 needed)")
    G = gps_to_project([exifs[i]["lat"] for i in have], [exifs[i]["lon"] for i in have],
                       [exifs[i].get("alt", 0.0) for i in have], epsg)
    if not any("alt" in exifs[i] for i in have):
        log("  GPS has no altitude — heights are relative")
    if degenerate_layout(G[:, :2], 0.05):
        log("  ⚠ the GPS positions lie almost on a line: the rotation about that line is uncertain — "
            "add GCPs for a reliable result")
    C = np.array([rec.centre(i) for i in have])
    if thr <= 0:
        s0, R0, t0 = umeyama(C, G)
        e0 = np.linalg.norm(s0 * C @ R0.T + t0 - G, axis=1)
        thr = max(3.0 * float(np.median(e0)), 2.0)
    s, R, t, inl = umeyama_ransac(C, G, thr)
    apply_similarity(rec, s, R, t)
    C2 = np.array([rec.centre(i) for i in have])
    err = np.linalg.norm(C2 - G, axis=1)
    rep = {"method": "camera GPS", "epsg": int(epsg), "cameras": len(have), "inliers": int(inl.sum()),
           "rmse_xy": float(np.sqrt(np.mean(((C2 - G)[inl, :2] ** 2).sum(1)))),
           "rmse_z": float(np.sqrt(np.mean((C2 - G)[inl, 2] ** 2))), "scale": float(s),
           "residuals": [{"image": os.path.basename(rec.paths[i]), "dx": float(d[0]), "dy": float(d[1]),
                          "dz": float(d[2]), "inlier": bool(k)}
                         for i, d, k in zip(have, C2 - G, inl)]}
    log(f"Georeferenced with {inl.sum()}/{len(have)} camera GPS positions: RMSE XY {rep['rmse_xy']:.2f} m, "
        f"Z {rep['rmse_z']:.2f} m (consumer GPS: metre level)")
    del err
    return rep


# ---------------------------------------------------------------- GCPs
def read_gcp_coordinates(path):
    """CSV / TXT: name, X (Easting), Y (Northing), Z — header optional, any of , ; tab space."""
    out = {}
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        txt = f.read()
    delim = max((",", ";", "\t"), key=txt.count) if any(d in txt for d in ",;\t") else None
    for row in (csv.reader(txt.splitlines(), delimiter=delim) if delim else (l.split() for l in txt.splitlines())):
        row = [c.strip() for c in row if c.strip()]
        if len(row) < 4:
            continue
        try:
            out[row[0]] = np.array([float(row[1]), float(row[2]), float(row[3])])
        except ValueError:
            continue                                              # header
    if not out:
        raise ValueError("No GCP rows (name, X, Y, Z) found")
    return out


def read_gcp_observations(path):
    """CSV / TXT: image file name, GCP name, pixel x, pixel y (original image pixels)."""
    out = []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        txt = f.read()
    delim = max((",", ";", "\t"), key=txt.count) if any(d in txt for d in ",;\t") else None
    for row in (csv.reader(txt.splitlines(), delimiter=delim) if delim else (l.split() for l in txt.splitlines())):
        row = [c.strip() for c in row if c.strip()]
        if len(row) < 4:
            continue
        try:
            out.append((os.path.basename(row[0]), row[1], float(row[2]), float(row[3])))
        except ValueError:
            continue
    return out


def write_gcp_observations(path, obs):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["image", "gcp", "px", "py"])
        for row in obs:
            w.writerow([row[0], row[1], f"{row[2]:.2f}", f"{row[3]:.2f}"])


def triangulate_gcps(rec, observations, original_sizes):
    """Model coordinates of each GCP from ≥ 2 registered images. observations: (image, gcp, px, py) in
    ORIGINAL pixels; original_sizes: image index → (width, height) of the original file."""
    name_to_i = {os.path.basename(p): i for i, p in enumerate(rec.paths)}
    per = {}
    for img, g, px, py in observations:
        i = name_to_i.get(img)
        if i is None or not rec.registered[i]:
            continue
        intr = rec.intr_of(i)
        ow = original_sizes[i][0]
        s = intr.width / ow if ow else 1.0
        xn = undistort_normalized(np.array([[px * s, py * s]]), intr)[0]
        per.setdefault(g, []).append((i, xn))
    out = {}
    for g, lst in per.items():
        if len(lst) < 2:
            continue
        A = []
        for i, xn in lst:
            P = np.hstack([rec.R(i), rec.t(i).reshape(3, 1)])
            A += [xn[0] * P[2] - P[0], xn[1] * P[2] - P[1]]
        _, _, vt = np.linalg.svd(np.asarray(A))
        Xh = vt[-1]
        if abs(Xh[3]) > 1e-12:
            out[g] = (Xh[:3] / Xh[3], len(lst))
    return out


def georef_gcp(rec, coords, observations, original_sizes, check=(), log=print):
    """Similarity from triangulated GCPs to their surveyed coordinates. check: names used only as check points."""
    tri = triangulate_gcps(rec, observations, original_sizes)
    ctrl = [g for g in tri if g in coords and g not in check]
    if len(ctrl) < 3:
        raise ValueError(f"{len(ctrl)} GCP(s) seen in ≥ 2 registered images (≥ 3 needed). "
                         f"Marked: {', '.join(sorted(tri)) or 'none'}")
    src = np.array([tri[g][0] for g in ctrl])
    dst = np.array([coords[g] for g in ctrl])
    if degenerate_layout(dst, 0.02):
        raise ValueError("The GCPs lie on a line — use at least 3 well spread points")
    s, R, t = umeyama(src, dst)
    apply_similarity(rec, s, R, t)
    rows = []
    tri2 = triangulate_gcps(rec, observations, original_sizes)
    for g in sorted(tri2):
        if g not in coords:
            continue
        d = tri2[g][0] - coords[g]
        rows.append({"gcp": g, "role": "check" if g in check else "control", "images": tri2[g][1],
                     "dx": float(d[0]), "dy": float(d[1]), "dz": float(d[2])})
    ctl = np.array([[r["dx"], r["dy"], r["dz"]] for r in rows if r["role"] == "control"])
    chk = np.array([[r["dx"], r["dy"], r["dz"]] for r in rows if r["role"] == "check"]).reshape(-1, 3)
    rep = {"method": "GCP", "control": len(ctl), "check": len(chk), "scale": float(s),
           "rmse_xy": float(np.sqrt(np.mean((ctl[:, :2] ** 2).sum(1)))),
           "rmse_z": float(np.sqrt(np.mean(ctl[:, 2] ** 2))), "residuals": rows}
    if len(chk):
        rep["check_rmse_xy"] = float(np.sqrt(np.mean((chk[:, :2] ** 2).sum(1))))
        rep["check_rmse_z"] = float(np.sqrt(np.mean(chk[:, 2] ** 2)))
    log(f"Georeferenced with {len(ctl)} GCPs: RMSE XY {rep['rmse_xy']:.3f}, Z {rep['rmse_z']:.3f}"
        + (f" · check points XY {rep['check_rmse_xy']:.3f}, Z {rep['check_rmse_z']:.3f}" if len(chk) else ""))
    return rep


def level_model(rec, mode="nadir", scale_to=None, log=print):
    """No positioning: rotate so that 'up' is +Z (nadir: cameras look down; upright: image tops are up),
    centre the model and optionally scale by a known camera-to-camera distance (scale_to = (i, j, metres))."""
    reg = np.flatnonzero(rec.registered)
    if mode == "upright":
        up = -np.mean([rec.R(i)[1] for i in reg], axis=0)       # image −y axis in world coordinates
    else:
        up = -np.mean([rec.R(i)[2] for i in reg], axis=0)       # opposite of the viewing direction
    up /= np.linalg.norm(up)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(up, z)
    c = float(up @ z)
    if np.linalg.norm(v) < 1e-12:
        R = np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    else:
        vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        R = np.eye(3) + vx + vx @ vx * (1 / (1 + c))
    s = 1.0
    if scale_to is not None:
        i, j, d = scale_to
        dm = np.linalg.norm(rec.centre(i) - rec.centre(j))
        if dm > 0:
            s = d / dm
    pts = rec.X[rec.valid]
    t = -s * R @ (np.median(pts, axis=0) if len(pts) else np.zeros(3))
    apply_similarity(rec, s, R, t)
    log(f"Model levelled ({mode}); coordinates are local" + (f", scaled ×{s:.4g}" if scale_to else
                                                             " and NOT to scale"))
    return {"method": f"levelled ({mode})", "scale": float(s), "metric": scale_to is not None}
