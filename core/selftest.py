"""Self-test for packaged builds:  PointCloudStudio --selftest

Exercises every heavy dependency (LAZ codec, SciPy, rasterio/GDAL, pyproj/PROJ,
VisPy shaders + OpenGL, Qt) and writes selftest_report.txt next to the executable
(macOS app: ~/Library/Application Support/PointCloudStudio).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback


def _report_path():
    from core.platform_util import report_dir
    base = report_dir()
    try:
        test = os.path.join(base, ".write_test")
        open(test, "w").close()
        os.remove(test)
    except OSError:
        base = tempfile.gettempdir()
    return os.path.join(base, "selftest_report.txt")


def run(app=None):
    import numpy as np

    from core.version import APP_NAME, __version__
    lines = [f"{APP_NAME} {__version__} self-test", f"frozen: {getattr(sys, 'frozen', False)}",
             f"python: {sys.version.split()[0]}", ""]
    ok_all = True
    skipped = []
    tmp = tempfile.mkdtemp(prefix="pcs_selftest_")
    ctx = {}

    def step(name, fn):
        nonlocal ok_all
        t = time.perf_counter()
        try:
            info = fn()
            lines.append(f"[PASS] {name:<34} {time.perf_counter() - t:6.2f}s  {info or ''}")
        except Exception as e:
            # an optional module that is not installed (e.g. rasterio, lazrs) → that feature is
            # unavailable in this build: SKIP, not FAIL
            from core.deps import module_from_error, package_for_module
            mod = module_from_error(e)
            pkg = package_for_module(mod) if mod else None
            if pkg is not None and not pkg[2]:
                skipped.append(pkg[1])
                lines.append(f"[SKIP] {name:<34} optional module '{pkg[1]}' not installed ({pkg[3]})")
                return
            ok_all = False
            lines.append(f"[FAIL] {name:<34} {type(e).__name__}: {e}")
            lines.append("       " + traceback.format_exc().replace("\n", "\n       "))

    def make_data():
        import laspy
        rng = np.random.default_rng(0)
        n = 60_000
        x = 160_000 + rng.uniform(0, 300, n)
        y = 2_620_000 + rng.uniform(0, 150, n)
        z = -20 - 0.004 * (x - 160_000) + 0.6 * np.sin((x - 160_000) / 15)
        h = laspy.LasHeader(point_format=3, version="1.2")
        h.scales, h.offsets = [0.001] * 3, [160_000, 2_620_000, 0]
        las = laspy.LasData(h)
        las.x, las.y, las.z = x, y, z
        las.intensity = (rng.random(n) * 1000).astype(np.uint16)
        import importlib.util
        has_lazrs = importlib.util.find_spec("lazrs") is not None
        path = os.path.join(tmp, "t.laz" if has_lazrs else "t.las")   # later steps still run without lazrs
        las.write(path)
        ctx["laz"] = path
        return f"{n:,} points" + ("" if has_lazrs else " (LAS — lazrs not installed, LAZ not tested)")

    def read_laz():
        from fileio.las_reader import read_las
        ctx["pc"] = read_las(ctx["laz"])
        return f"{len(ctx['pc']):,} points" + (" (lazrs)" if ctx["laz"].endswith(".laz") else " (LAS)")

    def text_roundtrip():
        from fileio.writers import export
        from fileio.xyz_reader import read_xyz
        p = os.path.join(tmp, "t.xyz")
        idx = np.arange(0, len(ctx["pc"]), 7)
        export(ctx["pc"], idx, p, "xyz", dict(columns=["X", "Y", "Z"], delimiter=" ", decimals=3, header=True))
        back = read_xyz(p, 1, "whitespace", ["X", "Y", "Z"], "utf-8")
        assert len(back) == len(idx)
        return f"{len(back):,} rows"

    def tin():
        from core.surface import build_tin
        ctx["tin"] = build_tin(ctx["pc"].xyz, ctx["pc"].active_indices(), 8.0)
        return f"{len(ctx['tin'].triangles):,} triangles (SciPy)"

    def dem():
        from core.surface import build_dem
        pc = ctx["pc"]
        ctx["dem"] = build_dem(pc.xyz, pc.active_indices(), 1.0, "tin", tin=ctx["tin"])
        d2 = build_dem(pc.xyz, pc.active_indices(), 2.0, "shoal")
        assert d2.has_true_position
        return f"{ctx['dem'].shape} + shoal true position"

    def geotiff():
        import rasterio
        from pyproj import CRS

        from fileio.surface_io import export_dem
        p = os.path.join(tmp, "t.tif")
        export_dem(ctx["dem"], p, crs=CRS.from_epsg(3826))
        with rasterio.open(p) as ds:
            epsg = ds.crs.to_epsg() if ds.crs else None
            wkt = ds.crs.to_wkt()[:60] if ds.crs else None
        if epsg != 3826:
            from rasterio.env import PROJDataFinder
            env = {k: os.environ[k] for k in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA") if k in os.environ}
            raise AssertionError(f"read back EPSG={epsg} (expected 3826), CRS={wkt!r}; "
                                 f"GDAL {rasterio.__gdal_version__}; rasterio proj_data="
                                 f"{PROJDataFinder().search_wheel()}; env={env or 'none'}")
        return "EPSG:3826 (rasterio/GDAL + pyproj/PROJ)"

    def landxml():
        from fileio.surface_io import export_tin
        p = os.path.join(tmp, "t.xml")
        export_tin(ctx["tin"], p)
        return f"{os.path.getsize(p) / 1e6:.1f} MB"

    def las_with_crs():
        from fileio.writers import export
        p = os.path.join(tmp, "t2.las")
        export(ctx["pc"], ctx["pc"].active_indices()[:1000], p, "las", {})
        return "ok"

    def section():
        from core import profile
        sec = profile.Section("T", np.array([[160_020.0, 2_620_070.0], [160_280.0, 2_620_080.0]]), width=1.0)
        r = profile.compute(sec, ctx["pc"], ctx["tin"], ctx["dem"])
        assert len(r["idx"]) > 0 and len(r["tin"][0]) > 0
        return f"{len(r['idx']):,} corridor points"

    def class7_reject():
        import laspy

        from core.trash import ClassifyCommand
        from fileio.writers import export
        pc = ctx["pc"]
        idx = np.arange(0, 600)
        import importlib.util
        fmt = "laz" if importlib.util.find_spec("lazrs") is not None else "las"
        cmd = ClassifyCommand(pc, idx, 7)
        cmd.do()
        try:                                   # always undo, so later steps see the original cloud
            assert pc.n_noise == 600
            p = os.path.join(tmp, "c7." + fmt)
            export(pc, np.flatnonzero(pc.deleted_batch == 0), p, fmt, {})
            n7 = int((np.array(laspy.read(p).classification) == 7).sum())
        finally:
            cmd.undo()
        assert n7 == 600 and pc.n_noise == 0
        return f"600 points → Class 7 → {fmt.upper()} → read back"

    def auto_filter():
        from core import filters
        pc = ctx["pc"]
        z = pc.xyz[:, 2].copy()
        spikes = np.arange(0, len(pc), 997)[:40]
        pc.xyz[spikes, 2] += 1.5
        try:
            cand, _ = filters.run(pc.xyz, pc.active_indices(), "grid", dict(cell=2.0, mode="abs", value=0.3))
            cand2, _ = filters.run(pc.xyz, pc.active_indices(), "sor", dict(k=8, n_std=3.0))
        finally:
            pc.xyz[:, 2] = z
        found = len(set(cand.tolist()) & set(spikes.tolist()))
        assert found == len(spikes) and len(cand) == len(spikes), f"grid filter found {found}/{len(spikes)} (+{len(cand) - found})"
        return f"grid median: {found}/{len(spikes)} spikes, 0 false · SOR ran ({len(cand2)} cand.)"

    def routes():
        import shapefile

        from core import crs as crsmod
        from fileio.route_reader import read_route_table, read_shp, reproject_routes
        xy = np.array([[160020.0, 2620040.0], [160250.0, 2620120.0], [160420.0, 2620200.0]])
        x2, y2 = crsmod.transform_xy(xy[:, 0], xy[:, 1], crsmod.from_epsg(3826), crsmod.from_epsg(32651))
        base = os.path.join(tmp, "r")
        w = shapefile.Writer(base, shapeType=shapefile.POLYLINE)
        w.field("NAME", "C")
        w.line([list(map(list, zip(x2, y2)))])
        w.record("T-01")
        w.close()
        with open(base + ".prj", "w") as f:
            f.write(crsmod.from_epsg(32651).to_wkt("WKT1_ESRI"))
        rs, c, _ = read_shp(base + ".shp")
        reproject_routes(rs, c, crsmod.from_epsg(3826))
        err = float(np.abs(rs[0].xy - xy).max())
        p = os.path.join(tmp, "r.csv")
        kp = 5 + np.r_[0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))] / 1000
        with open(p, "w") as f:
            f.write("KP,E,N\n" + "".join(f"{k:.4f},{a:.3f},{b:.3f}\n" for k, (a, b) in zip(kp, xy)))
        rc, _ = read_route_table(p, 1, "comma", ["KP", "Easting (X)", "Northing (Y)"])
        assert err < 0.01 and abs(rc[0].kp_at(100.0) - 5.1) < 1e-3, f"route error {err}"
        return f"SHP + .prj (UTM51N → TWD97, {err * 1000:.1f} mm) · RPL CSV with KP"

    def volumes():
        from core.surface import Dem
        from core.volume import compare
        cell, nr, nc = 0.5, 160, 160
        xs = (np.arange(nc) + 0.5) * cell
        X, Y = np.meshgrid(xs, 80 - (np.arange(nr) + 0.5) * cell)
        base = -20 + 0.01 * X
        mound = 1.5 * np.exp(-((X - 40) ** 2 + (Y - 40) ** 2) / (2 * 6.0 ** 2))
        r = compare(Dem((base + mound).astype(np.float32), 0, 80, cell, "a"), Dem(base.astype(np.float32), 0, 80, cell, "b"))
        exact = 1.5 * 2 * np.pi * 36
        err = abs(r["stats"]["fill_volume"] - exact) / exact
        assert err < 0.005, f"volume error {err:.2%}"
        return f"berm {r['stats']['fill_volume']:.2f} m³ (exact {exact:.2f})"

    def earthworks():
        from core.design import dredge_design, slope_map, trench_design
        from core.surface import Dem
        cell, n = 0.25, 800
        X, _ = np.meshgrid((np.arange(n) + 0.5) * cell, 200 - (np.arange(n) + 0.5) * cell)
        flat = Dem(np.full((n, n), -10.0, np.float32), 0, 200, cell, "f")
        r = dredge_design(flat, ("polygon", [(80, 90), (120, 90), (120, 110), (80, 110)]), -14.0, "cut", cut_n=3.0)
        exact = 40 * 20 * 4 + 60 * 3 * 16 + np.pi * 9 * 64 / 3
        e1 = abs(r["stats"]["cut_volume"] - exact) / exact
        t = trench_design(flat, np.array([(20.0, 100.0), (180.0, 100.0)]), np.array([1.0, 1.16]), bottom_width=1.0,
                          slope_n=2.0, depth=2.0, smooth=10, kp_step=0.05)
        e2 = abs(t["stats"]["cut_volume"] - 10 * 160) / 1600
        sm = slope_map(Dem((-10 + 0.1 * X).astype(np.float32), 0, 200, cell, "s"))
        e3 = abs(sm["stats"]["mean"] - np.degrees(np.arctan(0.1)))
        assert e1 < 0.005 and e2 < 0.005 and e3 < 0.01, f"dredge {e1:.2%} trench {e2:.2%} slope {e3:.3f}°"
        return f"dredge pit {e1:.2%}, trench {e2:.2%}, slope {e3:.3f}° error"

    def contours():
        import shapefile

        from core.contours import make_contours
        from core.surface import Dem
        from fileio.contour_io import export_contours
        cell, n = 0.5, 300
        X, Y = np.meshgrid((np.arange(n) + 0.5) * cell, 150 - (np.arange(n) + 0.5) * cell)
        cs = make_contours(Dem((-np.hypot(X - 75, Y - 75)).astype(np.float32), 0, 150, cell, "c"), 10)
        err = max(float(np.abs(np.hypot(l[:, 0] - 75, l[:, 1] - 75) + lev).max()) for l, lev in zip(cs.lines, cs.levels))
        export_contours(cs, os.path.join(tmp, "c.shp"))
        export_contours(cs, os.path.join(tmp, "c.dxf"))
        n_shp = len(shapefile.Reader(os.path.join(tmp, "c.shp")))
        dxf = open(os.path.join(tmp, "c.dxf")).read()
        n_dxf, n_txt = dxf.count("\nPOLYLINE\n"), dxf.count("\nTEXT\n")
        assert err < 0.01 and n_shp == n_dxf == len(cs.lines) and n_txt > 0, \
            f"contour err {err}, shp {n_shp}, dxf {n_dxf}, labels {n_txt}"
        return f"{len(cs.lines)} rings (radius err {err * 1000:.1f} mm) → SHP + DXF 3D with {n_txt} depth labels"

    def land_classification():
        from core import classify as C
        rng = np.random.default_rng(3)
        gx, gy = rng.uniform(0, 120, 40000), rng.uniform(0, 120, 40000)
        terr = lambda x, y: 15 + 0.05 * x + 2 * np.sin(y / 20)                      # noqa: E731
        bx, by = rng.uniform(40, 70, 2500), rng.uniform(40, 60, 2500)                # flat roof +8 m
        tx, ty = rng.normal(95, 3, 3000), rng.normal(95, 3, 3000)                    # tree canopy
        out = ~((gx > 40) & (gx < 70) & (gy > 40) & (gy < 60))
        xyz = np.vstack([np.column_stack([gx[out], gy[out], terr(gx[out], gy[out])]),
                         np.column_stack([bx, by, terr(bx, by) + 8]),
                         np.column_stack([tx, ty, terr(tx, ty) + rng.uniform(3, 12, 3000)])])
        truth = np.r_[np.full(out.sum(), 2), np.full(2500, 6), np.full(3000, 5)]
        g, cls = C.classify(xyz, np.arange(len(xyz)), "smrf", dict(cell=1.0), veg=(0.5, 2, 5),
                            buildings=dict(min_height=2.5, roughness=0.02, min_area=25, cell=1.0))
        err = float(((truth == 2) != g).mean())
        b_ok = float(((truth == 6) & (cls == 6)).sum() / 2500)
        assert err < 0.02 and b_ok > 0.8, f"ground error {err:.2%}, building recall {b_ok:.0%}"
        return f"SMRF ground error {err:.2%}, roof recall {b_ok:.0%} (land data)"

    def crs_and_reprojection():
        from core import crs as crsmod
        from fileio.grid_reader import reproject_dem
        hits = crsmod.search("TWD97 121")
        assert any(c == 3826 for c, _, _ in hits), "EPSG database search failed"
        x, y = crsmod.transform_xy([160500.0], [2620150.0], crsmod.from_epsg(3826), crsmod.from_epsg(4326))
        assert abs(x[0] - 120.12) < 0.01 and abs(y[0] - 23.68) < 0.01, "TWD97 → WGS84 wrong"
        r = reproject_dem(ctx["dem"], crsmod.from_epsg(3826), crsmod.from_epsg(32651))
        assert np.isfinite(r.z).mean() > 0.5
        return f"EPSG search, TWD97→WGS84, DEM → UTM 51N {r.z.shape}"

    def project_roundtrip():
        from core import project_io
        from core.project import Dataset, Project
        pr = Project()
        pr.crs = None
        ds = pr.add(Dataset("t", "points", ctx["pc"]))
        pr.add(Dataset("d", "grid", ctx["dem"], derived_from=ds.id))
        path = os.path.join(tmp, "t.pcsproj")
        project_io.save_project(path, pr)
        doc = project_io.read_project(path)
        g = project_io.load_generated(doc["datasets"][1], path)
        assert g.z.shape == ctx["dem"].z.shape
        return f"{len(doc['datasets'])} datasets saved / read"

    def render():
        from PySide6.QtWidgets import QApplication

        from ui.main_window import MainWindow
        app_ = QApplication.instance()
        w = MainWindow()
        w.resize(900, 600)
        w.show()
        app_.processEvents()
        from core.project import Dataset
        w._new_project()
        w.project.add(Dataset("selftest cloud", "points", ctx["pc"]))
        w.project.add(Dataset("selftest DEM", "grid", ctx["dem"]))
        w._project_changed(fit=True)
        import time as _t
        t_end = _t.perf_counter() + 1.0          # let Qt lay out / resize the newly shown 3D view
        while _t.perf_counter() < t_end:
            app_.processEvents()
            _t.sleep(0.02)
        w.viewer.fit_view()
        app_.processEvents()
        img = w.viewer.canvas.render()
        cover = float((img[..., :3].astype(int).sum(-1) > 80).mean())
        w._dirty = False
        w.close()
        v = w.viewer
        assert cover > 0.01, (f"nothing rendered (canvas {v.canvas.size}, stack {w.stack.currentIndex()}, "
                              f"bound={w.pc is not None}, markers={v.markers.visible}, shown={len(v.display_idx)}, "
                              f"camera={tuple(round(c, 1) for c in v.camera.center)}/{v.camera.scale_factor:.1f}, "
                              f"surfaces={[(k, l.V is not None) for k, l in v.surfaces.items()]})")
        return f"OpenGL render ok, coverage {cover:.2f}"

    def cable_analyses():
        """Synthetic cable with known answers: DoB, free span (both methods), rock berm."""
        from core import cable
        from core.pointcloud import PointCloud
        from core.route import Route
        from core.surface import Dem
        X0, Y0 = 200_000.0, 2_600_000.0
        flat = Dem(np.full((60, 1100), -20.0, np.float32), X0 - 50, Y0 + 30, 1.0, "t")
        ch = np.arange(0, 1001.0)
        xy = np.column_stack([X0 + ch, np.full_like(ch, Y0)])
        # DoB: cable top −21.5 (DoB 1.5 = target), −20.8 between 300 and 400 m (DoB 0.8)
        r = Route("C", xy, kp=10 + ch / 1000, z=np.where((ch >= 300) & (ch <= 400), -20.8, -21.5))
        d = cable.dob_analysis(r, flat, step=1.0, target=1.5)
        g = d["segments"]
        assert len(g) == 1 and abs(g[0]["length"] - 101) <= 2 and abs(g[0]["dob_min"] - 0.8) < 1e-6, g
        # free span A: 20 m trough (−21) under a cable lying on the seabed (top −19.8, Ø 0.2) → gap 1.0
        z = np.full((60, 1100), -20.0, np.float32)
        xs = X0 - 50 + np.arange(1100) + 0.5
        z[:, (xs >= X0 + 500) & (xs <= X0 + 520)] = -21.0
        r2 = Route("C2", xy, kp=10 + ch / 1000, z=np.full_like(ch, -19.8))
        a = cable.free_span_profile(r2, Dem(z, X0 - 50, Y0 + 30, 1.0, "t"), diameter=0.2, gap_min=0.1, len_min=10)
        sp = [s for s in a["segments"] if s["flag"] == "SPAN"]
        assert len(sp) == 1 and abs(sp[0]["length"] - 20) <= 2 and abs(sp[0]["gap_max"] - 1.0) < 0.01, a["segments"]
        # free span B (points only): 15 m trough (−20.8) across the corridor, cable crest at −19.8, 0.3 m off route
        rng = np.random.default_rng(3)
        n = 300_000
        px, py = rng.uniform(X0, X0 + 1000, n), rng.uniform(Y0 - 5, Y0 + 5, n)
        pz = np.where((px >= X0 + 600) & (px <= X0 + 615), -20.8, -20.0) + rng.normal(0, 0.02, n)
        cx = rng.uniform(X0, X0 + 1000, 30_000)
        cloud = PointCloud(np.column_stack([np.r_[px, cx], np.r_[py, Y0 + 0.3 + rng.normal(0, 0.03, 30_000)],
                                            np.r_[pz, -19.8 + rng.normal(0, 0.01, 30_000)]]))
        rb = Route("R", np.array([[X0, Y0], [X0 + 1000, Y0]]), kp=np.array([10.0, 11.0]))
        b = cable.free_span_points(rb, cloud, diameter=0.2, gap_min=0.1, len_min=10, search=1.5, side=3.0)
        sp = [s for s in b["segments"] if s["flag"] == "SPAN"]
        assert len(sp) == 1 and abs(sp[0]["length"] - 15) <= 2 and abs(sp[0]["gap_max"] - 0.8) < 0.1, b["segments"]
        # rock berm: template crest 2 m, height 1 m, 1:3, KP 10.2–10.4 on a flat seabed → 500 m³ per 100 m body;
        # as-built 0.4 m low between 300 and 320 m → one under-filled centre-line segment of ~20 m
        t = cable.berm_template(flat, rb, 10.2, 10.4, crest_width=2, height=1, slope_n=3, cell=0.25, smooth=0)
        D = t["design"]
        A = D.z.copy()
        xs = D.x0 + (np.arange(A.shape[1]) + 0.5) * D.cell
        cols = (xs >= X0 + 300) & (xs <= X0 + 320)
        A[:, cols] = np.where(A[:, cols] > -19.55, A[:, cols] - 0.4, A[:, cols])
        c = cable.berm_check(Dem(A, D.x0, D.ytop, D.cell, "a"), D, rb, 10.2, 10.4, pre=flat, half_width=6, tol=0.1,
                             step=0.5, bin_m=100)
        body = [x for x in c["bins"] if abs(x["kp_from"] - 10.2) < 1e-9][0]["design_m3"]
        assert abs(body - 500) / 500 < 0.03, body
        assert len(c["segments"]) == 1 and abs(c["segments"][0]["length"] - 20) <= 2, c["segments"]
        return (f"DoB 0.8 m over {g[0]['length']:.0f} m · span A {a['segments'][0]['length']:.0f} m / gap "
                f"{a['segments'][0]['gap_max']:.2f} m · span B {sp[0]['length']:.0f} m / gap {sp[0]['gap_max']:.2f} m"
                f" · berm {body:.0f} m³ per 100 m")

    def scour_and_sandwaves():
        """Cone-shaped scour hole (2.0 → 2.5 m in two years) and sand waves (λ 200 m, 10 m in 2 years)."""
        from core import sandwave, scour
        from core.route import Route
        from core.structures import Structures
        from core.surface import Dem
        X0, Y0 = 200_000.0, 2_600_000.0

        def hole(maxd):
            n, c = 400, 0.5
            x0, ytop = X0, Y0 + 200
            xs = x0 + (np.arange(n) + 0.5) * c
            ys = ytop - (np.arange(n) + 0.5) * c
            X, Y = np.meshgrid(xs, ys)
            r = np.hypot(X - (X0 + 100), Y - (Y0 + 100))
            d = np.where((r >= 4) & (r <= 14), maxd * (1 - (r - 4) / 10), 0.0)
            return Dem((-20 - d).astype(np.float32), x0, ytop, c, "s")
        S = Structures(["WTG01"], np.array([[X0 + 100, Y0 + 100]]), [8.0])
        res = scour.scour_analysis(S, [0], [{"name": "a", "date": "2023-01-01", "obj": hole(2.0)},
                                            {"name": "b", "date": "2025-01-01", "obj": hole(2.5)}],
                                   {"mode": "farfield", "ff_in": 8, "ff_out": 10}, r_max=5, threshold=0.5)
        r1, r2 = res["table"]
        assert abs(r1["max_depth"] - 2.0) < 0.1 and abs(r1["extent"] - 11.5) < 0.6, r1
        assert abs(r2["max_depth_rate"] - 0.25) < 0.05, r2
        # sand waves moving 10 m towards 70° in two years (oblique to an east-west route)
        c, nx, ny = 1.0, 1600, 300
        xs = X0 + (np.arange(nx) + 0.5) * c
        ys = Y0 + 150 - (np.arange(ny) + 0.5) * c
        X, Y = np.meshgrid(xs, ys)
        k = np.array([np.sin(np.radians(70)), np.cos(np.radians(70))])

        def waves(shift):
            ph = ((X - X0) * k[0] + (Y - Y0) * k[1] - shift) / 200.0
            return Dem((-25 + 0.002 * (X - X0) + np.cos(2 * np.pi * ph)).astype(np.float32), X0, Y0 + 150, c, "w")
        rt = Route("R", np.array([[X0 + 100, Y0], [X0 + 1500, Y0]]), kp=np.array([0.1, 1.5]))
        eps = [{"name": "a", "date": "2022-01-01", "obj": waves(0.0)}, {"name": "b", "date": "2024-01-01",
                                                                         "obj": waves(10.0)}]
        m = sandwave.migration(rt, eps, window=150, spacing=150, max_shift=40, hp_len=400, method="2d")["summary"]
        assert abs(m["rate_median"] - 5.0) < 0.3 and abs(m["azimuth_median"] - 70) < 3, m
        p = sandwave.migration(rt, eps, window=150, spacing=150, max_shift=40, hp_len=400, method="profile")["summary"]
        assert abs(p["rate_along_median"] - 5.0 / np.sin(np.radians(70))) < 0.3, p
        return (f"scour {r1['max_depth']:.2f} → {r2['max_depth']:.2f} m, extent {r1['extent']:.1f} m · sand waves "
                f"{m['rate_median']:.2f} m/yr towards {m['azimuth_median']:.0f}° (true 5.00 / 70°)")

    for name, fn in (("generate LAZ (laspy + lazrs)", make_data), ("read LAZ", read_laz),
                     ("text export / import", text_roundtrip), ("TIN (Delaunay)", tin), ("DEM (TIN, shoal)", dem),
                     ("GeoTIFF with CRS", geotiff), ("LandXML", landxml), ("LAS export", las_with_crs),
                     ("cross-section", section), ("reject as Class 7", class7_reject), ("auto filter", auto_filter), ("routes (SHP / RPL)", routes), ("volumes", volumes), ("land classification", land_classification), ("earthworks design", earthworks), ("contours (SHP / DXF)", contours),
                     ("CRS search / reprojection", crs_and_reprojection),
                     ("cable DoB / free span / rock berm", cable_analyses),
                     ("scour / sand-wave migration", scour_and_sandwaves),
                     ("project save / open", project_roundtrip), ("3D render (VisPy/OpenGL)", render)):
        step(name, fn)

    result = "FAILED" if not ok_all else ("PASSED" if skipped else "ALL PASSED")
    if ok_all and skipped:
        result += f" — {len(skipped)} step(s) skipped, optional modules not installed: {', '.join(sorted(set(skipped)))}"
    lines += ["", "RESULT: " + result]
    text = "\n".join(lines)
    path = _report_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    print(f"\nReport: {path}")
    return ok_all, text, path
