"""Photogrammetry pipeline (classic SfM-MVS, no learned models):

photos or video frames → features → matching → incremental SfM + bundle adjustment → georeferencing
(camera GPS / GCPs / levelling) → dense matching (SGBM) + fusion → LAS (RGB) + cameras + report.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from core.sfm.images import list_images, read_exif


class Cancelled(Exception):
    pass


@dataclass
class PhotoParams:
    images: list = field(default_factory=list)       # image files (after frame extraction for video)
    out_dir: str = ""
    name: str = "photogrammetry"
    # features / matching
    max_dim: int = 2000                              # working image size (longest side, px)
    n_features: int = 8000
    feature: str = "sift"                            # sift | orb
    pair_mode: str = "auto"                          # auto | exhaustive | sequential | gps
    window: int = 8                                  # sequential neighbours
    # camera
    focal_mm: float = 0.0                            # override: focal length in mm (with sensor_mm)
    sensor_mm: float = 0.0                           # sensor width in mm
    focal_px: float = 0.0                            # override: focal length in px of the ORIGINAL image
    one_camera: bool = True                          # all images from one camera (shared intrinsics)
    refine_intrinsics: bool = True
    max_error_px: float = 4.0
    min_angle_deg: float = 2.0
    # georeferencing
    georef: str = "auto"                             # auto | gps | gcp | level | none
    epsg: int = 0
    level: str = "nadir"                             # nadir | upright
    gcp_coords: str = ""
    gcp_obs: list = field(default_factory=list)      # [(image name, gcp, px, py)] original pixels
    gcp_check: list = field(default_factory=list)
    # dense
    dense: bool = True
    dense_scale: float = 0.5                         # matching size relative to the working size
    dense_step: int = 2
    neighbours: int = 2
    min_views: int = 2
    workers: int = 4

    def to_dict(self):
        d = asdict(self)
        d["images"] = len(self.images)
        return d


def _cancel_check(cancel):
    if cancel is not None and cancel():
        raise Cancelled()


def camera_models(paths, exifs, sizes, one_camera, focal_mm=0.0, sensor_mm=0.0, focal_px=0.0):
    """Intrinsics per camera model (make, model, focal, size). sizes: working (w, h); originals from EXIF."""
    from core.sfm.geometry import Intrinsics
    from core.sfm.images import focal_pixels
    keys, intr, img_intr = {}, [], []
    for i, p in enumerate(paths):
        ex = exifs[i]
        w, h = sizes[i]
        key = ("one", w, h) if one_camera else (ex.get("make"), ex.get("model"), round(ex.get("focal_mm", 0), 1), w, h)
        if key not in keys:
            ow = ex.get("width") or w
            s = w / max(ow, 1) if ex.get("width") else 1.0
            if focal_px > 0:
                f, src, prior = focal_px * s, "user (px)", (0.02)
            elif focal_mm > 0 and sensor_mm > 0:
                f, src, prior = focal_mm / sensor_mm * max(w, h), "user (mm)", 0.03
            else:
                f, src = focal_pixels(ex, w, h)
                prior = 0.05 if src.startswith("EXIF") else None
            it = Intrinsics(f, w / 2.0, h / 2.0, 0.0, 0.0, w, h, False,
                            {"focal_source": src, "camera": f"{ex.get('make', '')} {ex.get('model', '')}".strip()})
            if prior:
                it.meta["f_prior"] = (f, prior)
            keys[key] = len(intr)
            intr.append(it)
        img_intr.append(keys[key])
    return intr, img_intr


def sfm_on_images(paths, max_dim=2000, n_features=8000, feature="sift", pair_mode="auto", window=8,
                  intrinsics=None, exifs=None, one_camera=True, refine_intrinsics=True, max_error_px=4.0,
                  min_angle_deg=2.0, focal_mm=0.0, sensor_mm=0.0, focal_px=0.0, workers=4, log=print,
                  progress=None, cancel=None):
    from core.sfm import features as ft
    from core.sfm.incremental import Cancelled as IncCancelled
    from core.sfm.incremental import Reconstruction

    def prog(a, b):
        return (lambda f: progress(a + (b - a) * f)) if progress else None

    t0 = time.perf_counter()
    exifs = exifs if exifs is not None else [read_exif(p) for p in paths]
    log(f"Extracting {feature.upper()} features from {len(paths)} images (≤ {max_dim} px)…")
    feats = ft.extract_all(paths, max_dim, n_features, feature, workers, prog(0.0, 0.2), cancel)
    _cancel_check(cancel)
    nk = [len(f.kp) for f in feats]
    log(f"  {int(np.median(nk)):,} features per image (median), {time.perf_counter() - t0:.0f} s")
    gps = None
    if all("lat" in e for e in exifs):
        lat = np.array([e["lat"] for e in exifs])
        lon = np.array([e["lon"] for e in exifs])
        gps = np.column_stack([(lon - lon.mean()) * 111320 * np.cos(np.radians(lat.mean())),
                               (lat - lat.mean()) * 110540])
    pairs, mode = ft.select_pairs(len(paths), pair_mode, window, gps)
    log(f"Matching {len(pairs)} image pairs ({mode})…")
    ransac_px = max(1.0, 2.0 * max_dim / 2000)
    matches = ft.match_all(feats, pairs, feature, 0.8, ransac_px, 25, workers, prog(0.2, 0.45), cancel)
    _cancel_check(cancel)
    log(f"  {len(matches)} verified pairs, {time.perf_counter() - t0:.0f} s")
    if not matches:
        raise ValueError("No image pairs could be matched — the photos need more overlap (≥ 60 %) and texture")
    tracks, kp2t = ft.build_tracks(feats, matches)
    log(f"  {len(tracks):,} feature tracks")
    sizes = [f.size for f in feats]
    if intrinsics is not None:
        intr, img_intr = [intrinsics], [0] * len(paths)
    else:
        intr, img_intr = camera_models(paths, exifs, sizes, one_camera, focal_mm, sensor_mm, focal_px)
    for it in intr:
        log(f"  camera {it.meta.get('camera') or ''} {it.width}×{it.height}: f = {it.f:.0f} px "
            f"({it.meta.get('focal_source', 'calibrated' if it.fixed else '')})")
    rec = Reconstruction(paths, feats, intr, img_intr, tracks, kp2t)
    rec.exifs = exifs
    try:
        rec.run(matches, max_error_px * max_dim / 2000, min_angle_deg, refine_intrinsics, log=log,
                progress=prog(0.45, 1.0), cancel=cancel)
    except IncCancelled:
        raise Cancelled() from None
    n_reg = int(rec.registered.sum())
    log(f"SfM: {n_reg}/{len(paths)} images registered, {rec.n_points():,} tie points, "
        f"reprojection RMS {rec.mean_error():.2f} px, {time.perf_counter() - t0:.0f} s")
    for it in rec.intr:
        log(f"  refined camera: f = {it.f:.1f} px, k1 = {it.k1:+.4f}, k2 = {it.k2:+.4f}")
    if n_reg < 2:
        raise ValueError("Fewer than 2 images could be oriented")
    return rec


def write_las(path, xyz, rgb, extra=None, epsg=None, scale=None):
    import laspy
    hdr = laspy.LasHeader(point_format=2, version="1.2")
    if extra:
        hdr = laspy.LasHeader(point_format=2, version="1.2")
        hdr.add_extra_dims([laspy.ExtraBytesParams(name=k, type=np.float32) for k in extra])
    span = float(np.max(np.ptp(xyz, axis=0))) if len(xyz) else 1.0
    if scale is None:
        scale = 0.001 if epsg else max(span / 2e8, 1e-6)
    hdr.scales = np.array([scale] * 3)
    hdr.offsets = np.floor(xyz.min(axis=0)) if len(xyz) else np.zeros(3)
    if epsg:
        from pyproj import CRS
        hdr.add_crs(CRS.from_epsg(int(epsg)))
    las = laspy.LasData(hdr)
    las.x, las.y, las.z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    las.red, las.green, las.blue = (rgb[:, 0].astype(np.uint16) * 257, rgb[:, 1].astype(np.uint16) * 257,
                                    rgb[:, 2].astype(np.uint16) * 257)
    for k, v in (extra or {}).items():
        las[k] = np.asarray(v, np.float32)
    las.write(path)
    return path


def write_cameras(path, rec, names=None):
    import csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["image", "X", "Y", "Z", "omega_deg", "phi_deg", "kappa_deg", "f_px", "k1", "k2"])
        for i in np.flatnonzero(rec.registered):
            R = rec.R(i)
            C = rec.centre(i)
            # photogrammetric ω φ κ of the camera (image → world rotation with z pointing back)
            M = np.diag([1, -1, -1.0]) @ R
            phi = np.degrees(np.arcsin(np.clip(M[2, 0], -1, 1)))
            omega = np.degrees(np.arctan2(-M[2, 1], M[2, 2]))
            kappa = np.degrees(np.arctan2(-M[1, 0], M[0, 0]))
            it = rec.intr_of(i)
            w.writerow([os.path.basename(rec.paths[i]), f"{C[0]:.4f}", f"{C[1]:.4f}", f"{C[2]:.4f}",
                        f"{omega:.4f}", f"{phi:.4f}", f"{kappa:.4f}", f"{it.f:.2f}", f"{it.k1:.6f}", f"{it.k2:.6f}"])


def run_photogrammetry(p: PhotoParams, log=print, progress=None, cancel=None):
    """Full pipeline. Returns dict(dense_path, sparse_path, cameras_path, report_path, report, cameras)."""
    from core.sfm import georef as gr
    from core.sfm.dense import densify

    t0 = time.perf_counter()
    os.makedirs(p.out_dir, exist_ok=True)
    paths = list_images(p.images) if isinstance(p.images, (list, tuple)) else list_images(p.images)
    if len(paths) < 2:
        raise ValueError("At least 2 images are needed")
    if p.epsg:
        try:
            from pyproj import CRS
            if CRS.from_epsg(int(p.epsg)).is_geographic:
                raise ValueError(f"EPSG:{p.epsg} is a geographic (degree) system — choose a projected system in "
                                 "metres (e.g. TWD97 / TM2 EPSG:3826 or UTM)")
        except ImportError:
            pass
    exifs = [read_exif(x) for x in paths]
    n_gps = sum("lat" in e for e in exifs)
    log(f"{len(paths)} images · {n_gps} with GPS")

    def prog(a, b):
        return (lambda f: progress(a + (b - a) * f)) if progress else None

    rec = sfm_on_images(paths, p.max_dim, p.n_features, p.feature, p.pair_mode, p.window, None, exifs,
                        p.one_camera, p.refine_intrinsics, p.max_error_px, p.min_angle_deg, p.focal_mm,
                        p.sensor_mm, p.focal_px, p.workers, log, prog(0.0, 0.55), cancel)
    _cancel_check(cancel)
    # ---- georeferencing
    mode = p.georef
    if mode == "auto":
        mode = "gcp" if p.gcp_coords and p.gcp_obs else ("gps" if n_gps >= 3 and p.epsg else "level")
    report = {"program": "", "params": p.to_dict(), "images": len(paths), "registered": int(rec.registered.sum()),
              "tie_points": rec.n_points(), "reprojection_rms_px": rec.mean_error(),
              "cameras": [{"f_px": it.f, "k1": it.k1, "k2": it.k2, "width": it.width, "height": it.height,
                           **{k: v for k, v in it.meta.items() if k != "f_prior"}} for it in rec.intr],
              "unregistered": [os.path.basename(paths[i]) for i in np.flatnonzero(~rec.registered)]}
    epsg = None
    if mode == "gcp":
        coords = gr.read_gcp_coordinates(p.gcp_coords)
        # marks are in pixels of the image as decoded (EXIF orientation applied) — take that size from
        # the image itself: EXIF dimensions are often stale after editing / resizing
        from core.sfm.images import imread
        marked = {o[0] for o in p.gcp_obs}
        sizes = {}
        for i, path in enumerate(paths):
            if os.path.basename(path) in marked:
                im = imread(path, gray=True)
                sizes[i] = (im.shape[1], im.shape[0])
        report["georef"] = gr.georef_gcp(rec, coords, p.gcp_obs, sizes, set(p.gcp_check), log)
        epsg = p.epsg or None
    elif mode == "gps":
        if not p.epsg:
            raise ValueError("Choose the EPSG code of the output coordinate system for GPS georeferencing")
        report["georef"] = gr.georef_gps(rec, exifs, p.epsg, log=log)
        epsg = p.epsg
    elif mode == "level":
        report["georef"] = gr.level_model(rec, p.level, log=log)
    else:
        report["georef"] = {"method": "none"}
    if mode in ("gcp", "gps"):
        # a last adjustment in the final frame (poses and points; intrinsics kept)
        rec.bundle(False, max_points=200000, max_err=p.max_error_px * p.max_dim / 2000, gauge=None, max_nfev=30,
                   fix_poses=True)
    _cancel_check(cancel)
    stem = os.path.join(p.out_dir, p.name)
    sparse_path = write_las(stem + "_sparse.las", rec.X[rec.valid], rec.colors[rec.valid], epsg=epsg)
    cams_path = stem + "_cameras.csv"
    write_cameras(cams_path, rec)
    result = {"sparse_path": sparse_path, "cameras_path": cams_path, "epsg": epsg, "georef": mode,
              "cameras": np.array([rec.centre(i) for i in np.flatnonzero(rec.registered)]),
              "camera_names": [os.path.basename(paths[i]) for i in np.flatnonzero(rec.registered)],
              "rec": rec}
    if p.dense:
        xyz, rgb, views, voxel = densify(rec, p.dense_scale, p.dense_step, p.neighbours, p.min_views,
                                         log=log, progress=prog(0.55, 0.98), cancel=cancel)
        _cancel_check(cancel)
        result["dense_path"] = write_las(stem + "_dense.las", xyz, rgb, extra={"views": views}, epsg=epsg)
        report["dense_points"] = int(len(xyz))
        report["dense_spacing"] = float(voxel)
    report["seconds"] = time.perf_counter() - t0
    try:
        from core.version import APP_NAME, __version__
        report["program"] = f"{APP_NAME} {__version__}"
    except Exception:  # noqa: BLE001
        pass
    rp = stem + "_report.json"
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=float)
    result["report_path"] = rp
    result["report"] = report
    log(f"Done in {report['seconds'] / 60:.1f} min")
    if progress:
        progress(1.0)
    return result


# ---------------------------------------------------------------- stereo rig
@dataclass
class RigParams:
    left: list = field(default_factory=list)
    right: list = field(default_factory=list)
    out_dir: str = ""
    name: str = "stereo"
    calib_mode: str = "manual"                 # manual | file | chessboard
    calib_file: str = ""
    f_px: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    baseline_m: float = 0.12
    calib_size: tuple = (0, 0)
    board_left: list = field(default_factory=list)
    board_right: list = field(default_factory=list)
    board_cols: int = 9
    board_rows: int = 6
    board_square_m: float = 0.025
    max_dim: int = 1600
    step: int = 2
    orientation: str = "forward"
    min_views: int = 1
    epsg: int = 0


def run_stereo_rig(p: RigParams, log=print, progress=None, cancel=None):
    from core.sfm import stereo_rig as sr
    os.makedirs(p.out_dir, exist_ok=True)
    if p.calib_mode == "file":
        cal = sr.load_calibration(p.calib_file)
    elif p.calib_mode == "chessboard":
        cal = sr.calibrate_chessboard(p.board_left, p.board_right, p.board_cols, p.board_rows, p.board_square_m, log)
        cal.save(os.path.join(p.out_dir, p.name + "_calibration.json"))
    else:
        w, h = p.calib_size
        if not w:
            from core.sfm.images import imread
            im = imread(p.left[0], gray=True)
            h, w = im.shape
        cal = sr.manual_calibration(p.f_px or 1.2 * max(w, h), p.cx or w / 2, p.cy or h / 2, p.baseline_m, w, h)
    log(f"Rig: baseline {cal.baseline:.4f} m, f = {cal.K1[0, 0]:.1f} px at {cal.size[0]}×{cal.size[1]}")
    res = sr.run_rig(cal, p.left, p.right, p.max_dim, p.step, 5, p.orientation, 0.0, None, p.min_views,
                     log=log, progress=progress, cancel=cancel)
    stem = os.path.join(p.out_dir, p.name)
    res["dense_path"] = write_las(stem + "_dense.las", res["xyz"], res["rgb"], extra={"views": res["views"]})
    rep = {"pairs": res["pairs"], "points": int(len(res["xyz"])), "spacing": res["voxel"], "scale": res["scale"],
           "baseline_m": cal.baseline, "calibration": cal.to_dict(), "orientation": p.orientation}
    with open(stem + "_report.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=float)
    res["report"] = rep
    log(f"Stereo cloud: {len(res['xyz']):,} points (local metric coordinates)")
    return res
