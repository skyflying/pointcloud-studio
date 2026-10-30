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

    def vector_formats():
        """Points, 3D lines and polygons with a hole → every vector format → read back (mm accuracy)."""
        from core import crs as crsmod
        from fileio import vector_io as V
        c = crsmod.from_epsg(3826)
        X0, Y0 = 200_000.0, 2_600_000.0
        ring = np.array([[X0, Y0], [X0 + 100, Y0], [X0 + 100, Y0 + 80], [X0, Y0 + 80]])
        hole = np.array([[X0 + 40, Y0 + 30], [X0 + 40, Y0 + 50], [X0 + 60, Y0 + 50], [X0 + 60, Y0 + 30]])
        groups = {"poly": [{"type": "Polygon", "parts": [ring, hole], "attrs": {"name": "pad", "v": 1.5}}],
                  "line": [{"type": "LineString", "parts": [np.array([[X0, Y0, -20.0], [X0 + 500, Y0 + 10, -21.5]])],
                            "attrs": {"name": "C1"}}],
                  "pt": [{"type": "Point", "parts": [np.array([[X0 + 10, Y0 + 20]])], "attrs": {"name": "W1"}}]}
        done = []
        for ext in (".shp", ".gpkg", ".geojson", ".kml", ".dxf"):
            for g, fs in groups.items():
                p_ = os.path.join(tmp, f"v_{g}{ext}")
                V.write(p_, fs, crs=c)
                back, crs, _i = V.read(p_)
                back = V.reproject(back, crs, c) if crs is not None else back
                a_ = np.vstack([q[:, :2] for f in fs for q in f["parts"]])
                b_ = np.vstack([q[:, :2] for f in back for q in f["parts"]])
                err = max(np.min(np.hypot(b_[:, 0] - x, b_[:, 1] - y)) for x, y in a_)
                assert err < 0.01, (ext, g, err)
            done.append(ext)
        # regression: a small polygon whose last vertex is 5 m from the first (was treated as closed)
        from core.volume import polygon_mask
        P_ = np.array([[X0, Y0], [X0 + 10, Y0], [X0 + 10, Y0 + 10], [X0, Y0 + 5]])
        xs_ = X0 + np.arange(0, 10, 0.5) + 0.25
        ys_ = Y0 + 10 - np.arange(0, 10, 0.5) - 0.25
        area = polygon_mask(xs_, ys_, P_).sum() * 0.25
        assert abs(area - 75.0) < 1e-6, area
        # regression: DXF HATCH polygons (how GDAL / many CAD programs store areas)
        hp = os.path.join(tmp, "hatch.dxf")
        with open(hp, "w") as fh:
            fh.write("0\nSECTION\n2\nENTITIES\n0\nHATCH\n8\nAREAS\n10\n0\n20\n0\n30\n0\n2\nSOLID\n70\n1\n71\n0\n"
                     "91\n1\n92\n2\n72\n0\n73\n1\n93\n4\n10\n0\n20\n0\n10\n10\n20\n0\n10\n10\n20\n10\n10\n0\n"
                     "20\n10\n97\n0\n75\n0\n76\n1\n98\n0\n0\nENDSEC\n0\nEOF\n")
        hf, _c, _i = V.read(hp)
        assert len(hf) == 1 and hf[0]["type"] == "Polygon" and len(hf[0]["parts"][0]) == 4, hf
        return "read/write " + " ".join(done) + " (< 1 cm) · small-polygon mask 75.0 m² · DXF HATCH"

    def land_analyses():
        """Analytic answers: slope / aspect, DoD volumes, stockpile cone, road fill, inundation, clearance."""
        from core import land, terrain
        from core.pointcloud import PointCloud
        from core.route import Route
        from core.surface import Dem
        X0, Y0 = 300_000.0, 2_700_000.0

        def grid(f, n=200, c=1.0):
            xs = X0 + (np.arange(n) + 0.5) * c
            ys = Y0 + n * c - (np.arange(n) + 0.5) * c
            X, Y = np.meshgrid(xs, ys)
            return Dem(f(X - X0, Y - Y0).astype(np.float32), X0, Y0 + n * c, c, "t")
        L = dict(terrain.terrain_products(grid(lambda x, y: 100 - np.tan(np.radians(10)) * x))["layers"])
        sl, asp = float(np.nanmedian(L["Slope (°)"].cvals)), float(np.nanmedian(L["Aspect (° from N)"].cvals))
        assert abs(sl - 10) < 0.05 and abs(asp - 90) < 0.5, (sl, asp)
        d = terrain.dod(grid(lambda x, y: 0 * x), grid(lambda x, y: np.where(np.hypot(x - 60, y - 100) < 10, 1.0, 0)))
        dep = d["tables"]["Volumes"][0]["deposition_m3"]
        assert abs(dep - np.pi * 100) / (np.pi * 100) < 0.03, dep
        cone = grid(lambda x, y: 0.02 * x + np.clip(5 * (1 - np.hypot(x - 100, y - 100) / 10), 0, None), n=800, c=0.25)
        t = np.linspace(0, 2 * np.pi, 60, endpoint=False)
        ring = np.column_stack([X0 + 100 + 12 * np.cos(t), Y0 + 100 + 12 * np.sin(t)])
        v = land.stockpile_volumes([("s", None, cone)], [("A", ring)])["tables"]["Volumes"][0]["volume_m3"]
        assert abs(v - np.pi * 100 * 5 / 3) < 3, v
        rt = Route("CL", np.array([[X0 + 100, Y0 + 200], [X0 + 300, Y0 + 200]]), kp=np.array([0.0, 0.2]))
        rd = land.road_design(grid(lambda x, y: 0 * x, n=400), rt, grade="pvi", pvi=[(0.0, 1.0), (0.2, 1.0)],
                              half_width=4, crossfall=0.0, fill_n=2.0, cell=0.25, bin_m=50)
        fill = sum(r["fill_m3"] for r in rd["tables"]["Earthworks per interval"])
        assert abs(fill - 2000) < 20, fill
        h = land.hydrology(grid(lambda x, y: 0.1 * np.abs(x - 100) + 0.05 * y), level=3.0, seed=(X0 + 100, Y0 + 2))
        ia = h["tables"]["Inundation"][0]["area_m2"]
        assert abs(ia - 1800) < 60, ia
        rng = np.random.default_rng(1)
        P = np.column_stack([X0 + rng.uniform(0, 100, 20000), Y0 + 50 + rng.uniform(-10, 10, 20000), np.zeros(20000)])
        cls = np.full(20000, 2)
        tr = (P[:, 0] > X0 + 40) & (P[:, 0] < X0 + 50) & (np.abs(P[:, 1] - Y0 - 50) < 3)
        P[tr, 2], cls[tr] = 7.0, 5
        pc = PointCloud(P)
        pc.attrs["Classification"] = cls.astype(np.uint8)
        segs = land.clearance(pc, np.array([[X0, Y0 + 50, 10.0], [X0 + 100, Y0 + 50, 10.0]]), limit=5.0)["tables"]["Segments"]
        assert len(segs) == 1 and abs(segs[0]["min_clearance"] - 3.0) < 0.05, segs
        return (f"slope {sl:.2f}° / aspect {asp:.1f}° · DoD {dep:.0f} m³ · stockpile {v:.1f} m³ (523.6) · road fill "
                f"{fill:.0f} m³ (2000) · inundation {ia:.0f} m² (1800) · clearance {segs[0]['min_clearance']:.2f} m")

    def _batch_check(r, sea):
        """Three shuffled / reversed segments of one string → one 1000 m cable; batch + workbook sheets."""
        from core import multiyear_batch as mb
        from core.route import Route
        segs = []
        for a_, b_, nm in ((0, 300, "A"), (300, 650, "B"), (650, 1000, "C")):
            xy = r.xy[(r.cum >= a_ - 1e-9) & (r.cum <= b_ + 1e-9)]
            segs.append(Route(nm, xy[::-1] if nm == "B" else xy, z=np.full(len(xy), -21.5),
                              meta={"attributes": {"string": "S1"}}))
        st_ = mb.stitch([segs[2], segs[1], segs[0]], "string")
        assert len(st_) == 1 and abs(st_[0].length - 1000.0) < 1e-6 and len(st_[0].meta["members"]) == 3, st_
        items = [(x.name, "cable", x) for x in segs] + [(st_[0].name, "string", st_[0])]
        b = mb.run_batch(items, [{"name": str(2025 + t), "year": 2025 + t, "obj": sea(t)} for t in range(5)],
                         dict(required=1.0, reburial_to=1.5, mode="cumulative"))
        sh = mb.workbook_sheets(b, {"mode": "cumulative"})
        assert len(b["items"]) == 4 and len(sh["Summary"]) == 4 and len(sh["Points"]) > 2000, sh.keys()
        # members 50 m apart: the gap is joined without a cable level and not assessed
        a2 = Route("A", segs[0].xy, z=np.full(len(segs[0].xy), -21.5), meta={"attributes": {"string": "S2"}})
        c2 = Route("C", segs[2].xy, z=np.full(len(segs[2].xy), -21.5), meta={"attributes": {"string": "S2"}})
        g = mb.stitch([a2, c2], "string")[0]
        assert g.meta["gaps"] and abs(g.meta["gaps"][0] - 350.0) < 1.0, g.meta
        gb = mb.run_batch([(g.name, "string", g)], [{"name": "2025", "year": 2025, "obj": sea(0)}],
                          dict(required=1.0))["items"][0]["row"]
        assert abs(gb["length_m"] - 650.0) < 3.0 and gb["gaps_not_assessed_m"] > 340, gb
        # reference rules (original burial-check script): peak shaving = converged iterative sweeps
        from core import multiyear
        from core.multiyear import peak_shave
        chx = np.arange(0.0, 400.0, 1.0)
        zz = -40 + 3 * np.sin(chx / 15.0) + np.where((chx > 200) & (chx < 204), 6.0, 0.0)
        it = zz.copy()
        k_ = np.tan(np.radians(15.0))
        for _ in range(60):
            for i in range(1, len(it)):
                it[i] = min(it[i], it[i - 1] + k_)
            for i in range(len(it) - 2, -1, -1):
                it[i] = min(it[i], it[i + 1] + k_)
        assert np.abs(peak_shave(zz, chx, 15.0) - it).max() < 1e-9
        rx = Route("R", np.c_[np.linspace(0, 1000, 11), np.zeros(11)], z=np.full(11, -21.5))
        yrs = [{"name": "a", "year": 2025, "obj": sea(0)}, {"name": "b", "year": 2026, "obj": sea(0)}]
        rr = multiyear.assess(rx, yrs, mode="cumulative", trigger="exposed", min_run=7.0, settle_short=True,
                              action="lower_by", lower_by=1.5, cost_per_m=10.0, discount_rate=0.1)
        ss = rr["summary"]
        assert ss["total_cost"] is not None and abs(ss["total_cost_pv"] - sum(
            r_.get("cost", 0) / 1.1 ** (r_["year"] - 2025) for r_ in rr["table"])) < 1e-6, ss
        return (" · batch: 3 segments stitched into 1000 m, 4 items, workbook sheets, 350 m gap not assessed · "
                "reference rules (peak shaving, exposure ≥ 7 m, settle, lower-by, discounted cost)")

    def multiyear_check():
        """Seabed dropping 0.3 m/yr over 300 m, cable 1.5 m below the original seabed, required cover 1.0 m:
        re-burial first needed 2027, exposure 2031, free span 2032, 300 m → 3.0 h at 100 m/h."""
        from core import multiyear
        from core.route import Route
        from core.surface import Dem
        X0, Y0 = 200_000.0, 2_600_000.0
        ch = np.arange(0, 1001.0)
        r = Route("C", np.column_stack([X0 + ch, np.full_like(ch, Y0)]), kp=ch / 1000, z=np.full_like(ch, -21.5))

        def sea(t):
            z = np.full((40, 1100), -20.0, np.float32)
            xs = X0 - 50 + np.arange(1100) + 0.5
            z[:, (xs >= X0 + 300) & (xs <= X0 + 600)] = -20.0 - 0.3 * t
            return Dem(z, X0 - 50, Y0 + 20, 1.0, "s")
        res = multiyear.assess(r, [{"name": str(2025 + t), "year": 2025 + t, "simulated": t > 0, "obj": sea(t)}
                                   for t in range(9)], required=1.0, rate=100)
        s_ = res["summary"]
        assert (s_["first_reburial_year"], s_["first_exposed_year"], s_["first_span_year"]) == (2027, 2031, 2032), s_
        h = [r_["trenching_h"] for r_ in res["table"] if r_["year"] == 2027][0]
        assert abs(h - 3.0) < 0.05, h
        # cumulative: re-buried to 1.5 m whenever below 1.0 m → every second year, never exposed
        cu = multiyear.assess(r, [{"name": str(2025 + t), "year": 2025 + t, "obj": sea(t)} for t in range(9)],
                              required=1.0, reburial_to=1.5, rate=100, mode="cumulative")["summary"]
        assert cu["campaign_years"] == [2027, 2029, 2031, 2033] and cu["first_exposed_year"] is None, cu
        # trencher limited to 2.0 m cover → campaigns 2027 and 2031
        cap = multiyear.assess(r, [{"name": str(2025 + t), "year": 2025 + t, "obj": sea(t)} for t in range(9)],
                               required=1.0, reburial_to=2.5, max_cover=2.0, mode="cumulative")["summary"]
        assert cap["campaign_years"] == [2027, 2031], cap
        # cable import: semicolons, decimal commas, DMS latitude / longitude, depths in cm to the cable centre
        from fileio import route_reader as rr
        p_ = os.path.join(tmp, "sbi.csv")
        with open(p_, "w", encoding="utf-8") as fh:
            fh.write("# test\nKP;Lat;Lon;TOC_cm\n0,000;N23°30'00.0\";E120°00'00.0\";2150\n"
                     "0,010;N23°30'00.3\";E120°00'00.1\";2160\n")
        rt, geo = rr.read_route_table(p_, 2, "semicolon", ["KP", "Latitude", "Longitude", "Cable top (depth, + down)"],
                                      None, opts={"decimal_comma": True, "comment": "#", "z_unit": "cm",
                                                  "z_ref": "centre", "diameter": 0.2})
        assert geo and np.allclose(rt[0].z, [-21.4, -21.5]) and abs(rt[0].xy[0, 1] - 23.5) < 1e-9, (rt[0].z, rt[0].xy)
        return (f"independent: re-burial {s_['first_reburial_year']}, exposed {s_['first_exposed_year']}, span "
                f"{s_['first_span_year']} · cumulative: campaigns {', '.join(map(str, cu['campaign_years']))}, "
                f"never exposed · 2 m trencher: {', '.join(map(str, cap['campaign_years']))} · {h:.1f} h · "
                "EU / DMS / cm cable file read" + _batch_check(r, sea))

    def meshes_and_models():
        from core import mesh as M
        from core import modeling as MD
        rng = np.random.default_rng(1)
        pc = ctx["pc"]
        m = M.build_mesh(pc.xyz, pc.active_indices()[::5], "delaunay25d", max_edge=8, smooth=2)
        q = rng.normal(size=(8000, 3))
        q = q / np.linalg.norm(q, axis=1)[:, None] * (5 + rng.normal(0, 0.02, (8000, 1)))
        a = M.build_mesh(q, np.arange(len(q)), "alpha3d")
        assert a.is_closed and abs(a.volume / (4 / 3 * np.pi * 125) - 1) < 0.1, (a.volume, a.meta)
        p_ = os.path.join(tmp, "m.ply")
        for ext in (".obj", ".ply", ".stl", ".dxf"):
            M.export_mesh(a, p_[:-4] + ext)
        g = np.c_[rng.uniform(0, 40, (15000, 2)), rng.normal(0, 0.02, 15000)]
        x, y = rng.uniform(10, 20, 5000), rng.uniform(10, 18, 5000)
        b = np.c_[x, y, 6 + 3 * (1 - np.abs(y - 14) / 4)]
        xyz = np.vstack([g, b])
        cls = np.r_[np.full(len(g), 2), np.full(len(b), 6)]
        bd = MD.building_models(xyz, cls, np.arange(len(xyz)), lod=2)
        v = bd["buildings"][0]["volume_m3"]
        assert len(bd["buildings"]) == 1 and abs(v - 600) < 30, bd["buildings"]
        t = rng.uniform(0, 2 * np.pi, 3000)
        pile = np.c_[3 + 1.2 * np.cos(t), 4 + 1.2 * np.sin(t), rng.uniform(-8, 2, 3000)]
        cy = MD.detect_cylinders(pile, np.arange(len(pile)), thr=0.02)["cylinders"]
        assert cy and abs(cy[0]["radius"] - 1.2) < 0.02, cy
        pl = MD.detect_planes(xyz, np.arange(len(xyz)), thr=0.08, min_points=300)["segments"]
        assert len(pl) >= 3, len(pl)
        return (f"TIN mesh {len(m.faces):,} tri · alpha shape {a.volume:.0f} m³ (sphere 524) · LOD2 {v:.0f} m³ "
                f"(600) · pile r {cy[0]['radius']:.3f} m · {len(pl)} planes")

    def cube_s44_filters():
        from core import cube, noise_filters as nf, s44, uncertainty as un
        from core.pointcloud import PointCloud
        rng = np.random.default_rng(7)
        n = 120_000
        x, y = rng.uniform(0, 120, n), rng.uniform(0, 90, n)
        true = -25 + 2 * np.sin(x / 15) + 0.02 * y
        z = true + rng.normal(0, 0.07, n)
        sp = rng.choice(n, 400, replace=False)
        z[sp] += rng.choice([-1, 1], 400) * rng.uniform(0.8, 3, 400)
        cl = rng.choice(np.setdiff1d(np.arange(n), sp), 500, replace=False)
        x[cl], y[cl] = rng.normal(60, 1.5, 500), rng.normal(45, 1.5, 500)
        true = -25 + 2 * np.sin(x / 15) + 0.02 * y
        z[cl] = true[cl] + rng.uniform(4, 7, 500)
        xyz = np.c_[x, y, z]
        bad = np.zeros(n, bool)
        bad[np.r_[sp, cl]] = True
        bad &= np.abs(z - true) > 0.5
        pc = PointCloud(xyz, {"TPU": np.full(n, 0.2, np.float32)}, {"type": "test"}, name="mbes")
        tab = os.path.join(tmp, "tvu.csv")
        with open(tab, "w") as f:
            f.write("x,y,tvu\n")
            for gx in range(0, 121, 10):
                for gy in range(0, 91, 10):
                    f.write(f"{gx},{gy},0.1\n")
        u = un.resolve({"tvu": {"source": "attribute", "attribute": "TPU", "confidence": "95"},
                        "thu": {"source": "table", "path": tab, "column": 2, "max_dist": 0, "confidence": "1s"}},
                       pc, np.arange(n))
        assert abs(np.median(u["tvu"]) - 0.2 / 1.96) < 1e-6 and abs(np.median(u["thu"]) - 0.1) < 1e-9
        r = cube.estimate(xyz, np.arange(n), u["tvu"], u["thu"], resolution=1.0)
        f = np.zeros(n, bool)
        f[r["candidates"]] = True
        det, fal = f[bad].mean(), f[~bad].mean()
        assert det > 0.95 and fal < 0.002, (det, fal)
        rep, d = s44.assess(xyz, np.arange(n)[~bad], "1a", tvu95=np.full((~bad).sum(), 0.2), ref_cell=2.0)
        assert rep["aposteriori"]["1a"]["tvu_pct"] > 99 and rep["apriori"]["special"]["tvu_pct"] == 100.0
        assert abs(float(s44.allowed_tvu("1a", 40.0)) - np.hypot(0.5, 0.013 * 40)) < 1e-12
        c, _ = nf.clusters(xyz, np.arange(n), radius=1.0, min_points=50)
        lm, _ = nf.local_mad(xyz, np.arange(n), k=24, n_sigma=4)
        g = np.zeros(n, bool)
        g[lm] = True
        return (f"CUBE detect {det:.1%} / false {fal:.2%} · S-44 1a a-posteriori "
                f"{rep['aposteriori']['1a']['tvu_pct']:.1f} %, best {rep['best_order_aposteriori']} · "
                f"local MAD spikes {g[sp].mean():.0%} · clusters {len(c)} pts")

    def photogrammetry():
        import cv2  # noqa: F401  (optional: SKIP when OpenCV is not installed)
        from core.sfm import synthetic
        from core.sfm.geometry import umeyama
        from core.sfm.pipeline import PhotoParams, run_photogrammetry
        sc = synthetic.make_scene(os.path.join(tmp, "sfm"), n_x=3, n_y=2, size=(640, 480), f=560.0,
                                  altitude=40.0)
        p_ = PhotoParams(images=sc["paths"], out_dir=os.path.join(tmp, "sfm_out"), max_dim=640, n_features=4000,
                         focal_px=560.0, georef="level", dense=True, dense_scale=1.0, dense_step=2, workers=2)
        logs = []
        try:
            r = run_photogrammetry(p_, log=logs.append)
        except Exception as e:                       # keep the pipeline log in the report for diagnosis
            raise RuntimeError(f"{type(e).__name__}: {e}\n  log:\n    " + "\n    ".join(logs[-25:])) from e
        rec = r["rec"]
        assert rec.registered.all(), f"{rec.registered.sum()}/6 images oriented"
        C = rec.centres()
        s_, R_, t_ = umeyama(C, sc["centres"])
        cam_err = float(np.max(np.linalg.norm(s_ * C @ R_.T + t_ - sc["centres"], axis=1)))
        from fileio.las_reader import read_las
        d = read_las(r["dense_path"])
        X = s_ * d.xyz @ R_.T + t_
        dz = np.abs(X[:, 2] - sc["truth"](X[:, 0], X[:, 1]))
        assert cam_err < 0.2 and np.median(dz) < 0.15, (cam_err, float(np.median(dz)))
        rig = synthetic.make_stereo_scene(os.path.join(tmp, "rig"), n=1, size=(640, 480), f=560.0)
        from core.sfm.pipeline import RigParams, run_stereo_rig
        rr = run_stereo_rig(RigParams(left=rig["left"], right=rig["right"], out_dir=os.path.join(tmp, "rig_out"),
                                      f_px=560.0, baseline_m=rig["baseline"], orientation="camera", max_dim=640),
                            log=lambda s: None)
        dy = np.abs(rr["xyz"][:, 2] - rig["surface"](rr["xyz"][:, 0], 1.5 - rr["xyz"][:, 1]))
        assert np.median(dy) < 0.15, float(np.median(dy))
        return (f"6 photos: cameras ±{cam_err * 100:.1f} cm, dense {len(d):,} pts, median |Δz| "
                f"{np.median(dz) * 100:.1f} cm · stereo rig median {np.median(dy) * 100:.1f} cm")

    for name, fn in (("generate LAZ (laspy + lazrs)", make_data), ("read LAZ", read_laz),
                     ("text export / import", text_roundtrip), ("TIN (Delaunay)", tin), ("DEM (TIN, shoal)", dem),
                     ("GeoTIFF with CRS", geotiff), ("LandXML", landxml), ("LAS export", las_with_crs),
                     ("cross-section", section), ("reject as Class 7", class7_reject), ("auto filter", auto_filter), ("routes (SHP / RPL)", routes), ("volumes", volumes), ("land classification", land_classification), ("earthworks design", earthworks), ("contours (SHP / DXF)", contours),
                     ("CRS search / reprojection", crs_and_reprojection),
                     ("cable DoB / free span / rock berm", cable_analyses),
                     ("scour / sand-wave migration", scour_and_sandwaves),
                     ("vector formats (SHP/GPKG/GeoJSON/KML/DXF)", vector_formats),
                     ("land: terrain / change / stockpile / road / hydro / clearance", land_analyses),
                     ("multi-year cable assessment", multiyear_check),
                     ("meshes / models (planes, piles, LOD2)", meshes_and_models),
                     ("CUBE / IHO S-44 / noise filters", cube_s44_filters),
                     ("photogrammetry SfM-MVS / stereo rig (OpenCV)", photogrammetry),
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
