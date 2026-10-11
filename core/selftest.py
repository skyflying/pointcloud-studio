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
    tmp = tempfile.mkdtemp(prefix="pcs_selftest_")
    ctx = {}

    def step(name, fn):
        nonlocal ok_all
        t = time.perf_counter()
        try:
            info = fn()
            lines.append(f"[PASS] {name:<34} {time.perf_counter() - t:6.2f}s  {info or ''}")
        except Exception as e:
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
        path = os.path.join(tmp, "t.laz")
        las.write(path)
        ctx["laz"] = path
        return f"{n:,} points"

    def read_laz():
        from fileio.las_reader import read_las
        ctx["pc"] = read_las(ctx["laz"])
        return f"{len(ctx['pc']):,} points (lazrs)"

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
            assert ds.crs.to_epsg() == 3826
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
        cmd = ClassifyCommand(pc, idx, 7)
        cmd.do()
        assert pc.n_noise == 600
        p = os.path.join(tmp, "c7.laz")
        export(pc, np.flatnonzero(pc.deleted_batch == 0), p, "laz", {})
        n7 = int((np.array(laspy.read(p).classification) == 7).sum())
        cmd.undo()
        assert n7 == 600 and pc.n_noise == 0
        return "600 points → Class 7 → LAZ → read back"

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

    for name, fn in (("generate LAZ (laspy + lazrs)", make_data), ("read LAZ", read_laz),
                     ("text export / import", text_roundtrip), ("TIN (Delaunay)", tin), ("DEM (TIN, shoal)", dem),
                     ("GeoTIFF with CRS", geotiff), ("LandXML", landxml), ("LAS export", las_with_crs),
                     ("cross-section", section), ("reject as Class 7", class7_reject), ("auto filter", auto_filter), ("routes (SHP / RPL)", routes), ("volumes", volumes), ("land classification", land_classification), ("earthworks design", earthworks), ("contours (SHP / DXF)", contours),
                     ("CRS search / reprojection", crs_and_reprojection),
                     ("project save / open", project_roundtrip), ("3D render (VisPy/OpenGL)", render)):
        step(name, fn)

    lines += ["", "RESULT: " + ("ALL PASSED" if ok_all else "FAILED")]
    text = "\n".join(lines)
    path = _report_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    print(f"\nReport: {path}")
    return ok_all, text, path
