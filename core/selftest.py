"""Self-test for packaged builds:  PointCloudStudio --selftest

Exercises every heavy dependency (LAZ codec, SciPy, rasterio/GDAL, pyproj/PROJ,
VisPy shaders + OpenGL, Qt) and writes selftest_report.txt next to the executable.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback


def _report_path():
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()
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

    def render():
        from PySide6.QtWidgets import QApplication

        from ui.main_window import MainWindow
        app_ = QApplication.instance()
        w = MainWindow()
        w.resize(900, 600)
        w.show()
        app_.processEvents()
        pc = ctx["pc"]
        w.pc = pc
        from core.selection import Selection
        from core.trash import Trash
        w.sel, w.trash = Selection(len(pc)), Trash(pc)
        w.viewer.set_pointcloud(pc)
        w.stack.setCurrentIndex(1)
        w.viewer.set_surface("dem", ctx["dem"])
        app_.processEvents()
        img = w.viewer.canvas.render()
        cover = float((img[..., :3].astype(int).sum(-1) > 80).mean())
        w._dirty = False
        w.close()
        assert cover > 0.01, "nothing rendered"
        return f"OpenGL render ok, coverage {cover:.2f}"

    for name, fn in (("generate LAZ (laspy + lazrs)", make_data), ("read LAZ", read_laz),
                     ("text export / import", text_roundtrip), ("TIN (Delaunay)", tin), ("DEM (TIN, shoal)", dem),
                     ("GeoTIFF with CRS", geotiff), ("LandXML", landxml), ("LAS export", las_with_crs),
                     ("cross-section", section), ("3D render (VisPy/OpenGL)", render)):
        step(name, fn)

    lines += ["", "RESULT: " + ("ALL PASSED" if ok_all else "FAILED")]
    text = "\n".join(lines)
    path = _report_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    print(f"\nReport: {path}")
    return ok_all, text, path
