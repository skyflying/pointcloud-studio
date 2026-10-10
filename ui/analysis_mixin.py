"""Volume / difference, point-cloud-to-surface comparison, multi-epoch profile layers."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

LAYER_COLORS = ["#ffd166", "#4cc9f0", "#f72585", "#7bd389", "#ff9f1c", "#b8b8ff", "#ef476f", "#06d6a0"]


def layer_color(ds_id):
    return LAYER_COLORS[(ds_id - 1) % len(LAYER_COLORS)]


class AnalysisMixin:
    def _init_analysis_state(self):
        self.volume_dialog = None
        self.volume_polygon = None
        self._poly_mode = False
        self._vol_result = None

    # ---------------------------------------------------------------- volume dialog
    def open_volume_dialog(self):
        from ui.volume_dialog import VolumeDialog
        if self.volume_dialog is None:
            self.volume_dialog = VolumeDialog(self, self)
        self._volume_context()
        self.volume_dialog.show()
        self.volume_dialog.raise_()
        self.volume_dialog.activateWindow()

    def _volume_context(self):
        from core.surface import Dem
        if self.volume_dialog is None and getattr(self, "design_dialog", None) is None:
            return
        surfs = [(d.id, d.name, d.obj.cell if isinstance(d.obj, Dem) else None)
                 for d in self.project.datasets if d.kind in ("grid", "tin")]
        routes = [(d.id, d.name) for d in self.project.of_kind("route")]
        poly = (f"{len(self.volume_polygon)} vertices" if self.volume_polygon is not None else "none")
        if self.volume_dialog is not None:
            self.volume_dialog.set_context(surfs, routes, self.subset is not None, self.depth_mode, poly)
        if getattr(self, "design_dialog", None) is not None:
            base = [s for s in surfs if getattr(self.project.get(s[0]).obj, "cvals", None) is None]
            zr = {}
            for sid, _n, _c in base:
                lo, hi = self.project.get(sid).bbox()
                zr[sid] = (float(lo[2]), float(hi[2]))
            self.design_dialog.set_context(base, routes, self.subset is not None, self.depth_mode, poly, zr)

    def volume_draw_polygon(self):
        if not self.project.datasets:
            return
        self._poly_mode = True
        self.set_mode("section")
        self.statusBar().showMessage("Polygon: click the corners in the 3D view · double-click or Enter to finish "
                                     "· Esc cancels", 15000)

    def _finish_polygon(self, pts):
        self._poly_mode = False
        self.volume_polygon = np.asarray(pts, float)
        self.region_visible = True
        self.set_mode("navigate")
        self._show_region()
        self._set_dirty(True)
        self._refresh_drawings()
        self._volume_context()
        if self.volume_dialog is not None and self.volume_dialog.isVisible():
            self.volume_dialog.rb_r["polygon"].setChecked(True)
            self.volume_dialog.raise_()
        if getattr(self, "design_dialog", None) is not None and self.design_dialog.isVisible():
            self.design_dialog.rb["polygon"].setChecked(True)
            self.design_dialog.raise_()

    def volume_load_polygon(self):
        import shapefile

        from core import crs as crsmod
        from ui.crs_dialog import CrsDialog
        path, _ = QFileDialog.getOpenFileName(self, "Polygon shapefile", "", "Shapefile (*.shp)")
        if not path:
            return
        try:
            r = shapefile.Reader(path)
            shp = next(s for s in r.shapes() if s.shapeType in (shapefile.POLYGON, shapefile.POLYGONZ,
                                                                 shapefile.POLYGONM))
        except StopIteration:
            QMessageBox.warning(self, "No polygon", "The shapefile contains no polygon.")
            return
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Cannot read shapefile", str(e))
            return
        end = shp.parts[1] if len(shp.parts) > 1 else len(shp.points)
        xy = np.asarray(shp.points[:end], float)[:, :2]
        prj = os.path.splitext(path)[0] + ".prj"
        src = crsmod.guess_from_prj(prj) if os.path.isfile(prj) else None
        ok, src = CrsDialog.ask(self, "Coordinate system of the polygon", "Confirm the EPSG of the shapefile.",
                                initial=src or self.project.crs, allow_unknown=self.project.crs is None)
        if not ok:
            return
        if src is not None and self.project.crs is not None and crsmod.same(src, self.project.crs) is False:
            x, y = crsmod.transform_xy(xy[:, 0], xy[:, 1], src, self.project.crs)
            xy = np.column_stack([x, y])
        self._finish_polygon(xy)

    def volume_compute(self, sp):
        from core.volume import compare
        proj = self.project
        A = proj.get(sp["a"])
        B = proj.get(sp["b"]) if sp["b"] is not None else None
        region = None
        if sp["region"] == "route":
            rd = proj.get(sp["route"])
            region = ("corridor", rd.obj.xy, sp["half"]) if rd else None
        elif sp["region"] == "subset" and self.subset is not None:
            region = ("polygon", self.subset.corners())
        elif sp["region"] == "polygon":
            if self.volume_polygon is None:
                return self.volume_dialog.show_error("Draw or load a polygon first")
            region = ("polygon", self.volume_polygon)
        self._show_region()
        bname = B.name if B is not None else (f"depth {-sp['base']:g} m" if sp.get("base_ref") == "depth"
                                              else f"Z {sp['base']:g} m")
        self._vol_ctx = {"A": A.name, "B": bname, "spec": sp, "region": sp["region"]}
        self._run_task("Computing volumes…", compare, (A.obj,), dict(B=B.obj if B else None, base=sp["base"],
                       cell=sp["cell"], region=region), self._on_volume_done, report_progress=True)

    @Slot(object)
    def _on_volume_done(self, res):
        self._close_progress()
        self._vol_result = res
        self.volume_dialog.show_result(res, f"A: {self._vol_ctx['A']}   vs   B: {self._vol_ctx['B']}")

    def volume_add_layer(self):
        from core.project import Dataset
        from core.surface import Dem
        res = self._vol_result
        if res is None:
            return
        d = res["dz"][np.isfinite(res["dz"])]
        m = max(abs(np.percentile(d, 2)), abs(np.percentile(d, 98)), 1e-3) if len(d) else 1.0
        za = np.where(np.isfinite(res["dz"]), res["za"], np.nan).astype(np.float32)
        dem = Dem(za, res["x0"], res["ytop"], res["cell"], "difference", cvals=res["dz"], crange=(-m, m),
                  meta={"A": self._vol_ctx["A"], "B": self._vol_ctx["B"]})
        ds = Dataset(self.project.unique_name(f"Δ {self._vol_ctx['A']} − {self._vol_ctx['B']}"), "grid", dem,
                     crs=self.project.crs, source_crs=self.project.crs)
        self.project.add(ds)
        self._project_changed()
        self._set_dirty(True)
        self.statusBar().showMessage(f"Δ layer added (blue ≤ {-m:.2f} m … red ≥ +{m:.2f} m)", 8000)

    def volume_export_csv(self):
        res = self._vol_result
        if res is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Volume report", "volume_report.csv", "CSV (*.csv)")
        if not path:
            return
        s, c = res["stats"], self._vol_ctx
        from core import crs as crsmod
        rows = [("surface_A", c["A"]), ("compare_B", c["B"]), ("region", c["region"]),
                ("coordinate_system", crsmod.label(self.project.crs)), ("cell_m", s["cell"]),
                ("compared_area_m2", f"{s['area']:.3f}"), ("region_area_m2", f"{s.get('region_area', s['area']):.3f}"),
                ("coverage", f"{s.get('coverage', 1.0):.4f}"), ("fill_volume_m3", f"{s['fill_volume']:.3f}"),
                ("fill_area_m2", f"{s['fill_area']:.3f}"), ("cut_volume_m3", f"{s['cut_volume']:.3f}"),
                ("cut_area_m2", f"{s['cut_area']:.3f}"), ("net_volume_m3", f"{s['net_volume']:.3f}"),
                ("dz_mean_m", f"{s['mean']:.4f}"), ("dz_std_m", f"{s['std']:.4f}"), ("dz_min_m", f"{s['min']:.4f}"),
                ("dz_max_m", f"{s['max']:.4f}"), ("dz_p05_m", f"{s['p05']:.4f}"), ("dz_p95_m", f"{s['p95']:.4f}"),
                ("sign_convention", "dz = A - B; positive = A above B (fill / accretion)")]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("item,value\n")
            for k, val in rows:
                f.write(f"{k},{val}\n")
        self.statusBar().showMessage(f"Saved {os.path.basename(path)}", 6000)

    def volume_export_tif(self):
        from core.surface import Dem
        from fileio.surface_io import export_dem
        res = self._vol_result
        if res is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Δ GeoTIFF", "difference.tif", "GeoTIFF (*.tif)")
        if path:
            export_dem(Dem(res["dz"], res["x0"], res["ytop"], res["cell"], "difference"), path, crs=self.project.crs)
            self.statusBar().showMessage(f"Saved {os.path.basename(path)} (Δ = A − B)", 6000)

    # ---------------------------------------------------------------- point cloud to surface
    def compare_points_to_surface(self):
        from core.filters import surface_z
        from render.colormap import DEVIATION_ATTR, MODE_LABELS, available_modes
        if self.pc is None:
            self.statusBar().showMessage("Choose ✎ Edit on the point cloud to compare (Data panel)", 6000)
            return
        surfs = [d for d in self.project.datasets if d.kind in ("grid", "tin")]
        if not surfs:
            QMessageBox.information(self, "Point cloud to surface", "Create or load a DEM / TIN first.")
            return
        name, ok = QInputDialog.getItem(self, "Point cloud to surface", "Reference surface:",
                                        [d.name for d in surfs], 0, False)
        if not ok:
            return
        ds = next(d for d in surfs if d.name == name)
        pc = self.pc
        dz = (pc.xyz[:, 2] - surface_z(ds.obj, pc.xyz[:, 0], pc.xyz[:, 1])).astype(np.float32)
        pc.attrs[DEVIATION_ATTR] = dz
        act = pc.active_indices()
        d = dz[act]
        d = d[np.isfinite(d)]
        self.viewer.scales["deviation"].vmin = None
        self.cmb_color.blockSignals(True)
        self.cmb_color.clear()
        for m in available_modes(pc):
            self.cmb_color.addItem(MODE_LABELS[m], m)
        self.cmb_color.setCurrentIndex(self.cmb_color.findData("deviation"))
        self.cmb_color.blockSignals(False)
        self.viewer.set_color_mode("deviation")
        if len(d):
            ad = np.abs(d)
            msg = (f"{len(d):,} points vs {ds.name}: Δ mean {d.mean():+.3f} m, std {d.std():.3f} m, "
                   f"95 % within ±{np.percentile(ad, 95):.3f} m · {np.mean(ad <= 0.1):.1%} within ±0.10 m")
        else:
            msg = "No points over the surface"
        self.statusBar().showMessage(msg, 20000)

    # ---------------------------------------------------------------- multi-epoch profile layers
    def _profile_layers(self, sec, profile_pc):
        from core import profile as prof
        from core.surface import Tin
        layers = []
        for d in self.project.datasets:
            if not d.visible:
                continue
            if d.kind in ("grid", "tin"):
                ch, z = prof.tin_section(d.obj, sec) if isinstance(d.obj, Tin) else prof.dem_section(d.obj, sec)
                if len(ch):
                    layers.append({"name": d.name, "color": layer_color(d.id), "kind": "line",
                                   "ch": sec.disp(ch), "z": z})
            elif d.kind == "points" and d.obj is not profile_pc:
                r = prof.corridor_points(d.obj.xyz, d.obj.active_indices(), sec)
                if len(r["idx"]):
                    layers.append({"name": d.name, "color": layer_color(d.id), "kind": "points",
                                   "ch": sec.disp(r["chainage"]), "z": r["z"]})
        return layers


class DesignMixin:
    """Earthworks design (dredge / fill, cable trench) and slope maps."""

    def open_design_dialog(self):
        from ui.design_dialog import DesignDialog
        if getattr(self, "design_dialog", None) is None:
            self.design_dialog = DesignDialog(self, self)
        self._volume_context()
        self.design_dialog.show()
        self.design_dialog.raise_()
        self.design_dialog.activateWindow()

    def design_compute(self, kind, sp):
        from core.design import dredge_design, trench_design
        proj = self.project
        S = proj.get(sp["surface"])
        if kind == "dredge":
            if sp["region"] == "route":
                rd = proj.get(sp["route"])
                region = ("corridor", rd.obj.xy, sp["half"])
            elif sp["region"] == "subset":
                if self.subset is None:
                    return self.design_dialog.show_error("Draw a subset first")
                region = ("polygon", self.subset.corners())
            else:
                if self.volume_polygon is None:
                    return self.design_dialog.show_error("Draw or load a polygon first")
                region = ("polygon", self.volume_polygon)
            self._show_region()
            args = (S.obj, region, sp["level"])
            kw = dict(mode=sp["mode"], overdredge=sp["overdredge"], cut_n=sp["cut_n"], fill_n=sp["fill_n"],
                      cell=sp["cell"])
            fn = dredge_design
            lvl = f"depth {-sp['level']:g} m" if sp.get("level_ref") == "depth" else f"elevation {sp['level']:g} m"
            title = f"{S.name}: {dict(level='cut + fill', cut='cut', fill='fill')[sp['mode']]} to {lvl}"
        else:
            rd = proj.get(sp["route"])
            r = rd.obj
            args = (S.obj, r.xy, r.kp_vertices)
            kw = dict(bottom_width=sp["bottom_width"], slope_n=sp["slope_n"], depth=sp["depth"], ref=sp["ref"],
                      level=sp["level"], smooth=sp["smooth"], kp_step=sp["kp_step"], cell=sp["cell"])
            fn = trench_design
            how = (f"{sp['depth']:g} m below the existing surface (smoothed {sp['smooth']:g} m)" if sp["ref"] == "seabed"
                   else (f"bottom at depth {-sp['level']:g} m" if sp.get("level_ref") == "depth"
                         else f"bottom at elevation {sp['level']:g} m"))
            title = (f"Trench along {rd.name}: bottom {sp['bottom_width']:g} m, slopes 1:{sp['slope_n']:g}, {how}")
        self._design_ctx = {"kind": kind, "spec": sp, "title": title, "surface": S.name}
        self._run_task("Computing design…", fn, args, kw, self._on_design_done, report_progress=True)

    @Slot(object)
    def _on_design_done(self, res):
        self._close_progress()
        self._design_result = res
        c = self._design_ctx
        self.design_dialog.show_result(c["kind"], res, c["title"])

    def design_add_layers(self):
        from core.project import Dataset
        from core.surface import Dem
        res = getattr(self, "_design_result", None)
        if res is None:
            return
        c = self._design_ctx
        label = "Trench" if c["kind"] == "trench" else "Cut-fill design"
        G = res["G"]
        changed = np.isfinite(res["dz"]) & (np.abs(res["dz"]) > 1e-6)
        design = Dem(np.where(np.isfinite(res["S"]), G, np.nan).astype(np.float32), res["x0"], res["ytop"], res["cell"],
                     "design", meta={"title": c["title"]})
        d = res["dz"][changed]
        m = max(abs(np.percentile(d, 2)), abs(np.percentile(d, 98)), 1e-3) if len(d) else 1.0
        delta = Dem(np.where(changed, G, np.nan).astype(np.float32), res["x0"], res["ytop"], res["cell"], "difference",
                    cvals=np.where(changed, res["dz"], np.nan).astype(np.float32), crange=(-m, m),
                    meta={"title": c["title"]})
        for name, obj in ((f"{label} surface · {c['surface']}", design), (f"{label} cut/fill · {c['surface']}", delta)):
            self.project.add(Dataset(self.project.unique_name(name), "grid", obj, crs=self.project.crs,
                                     source_crs=self.project.crs))
        self._project_changed()
        self._set_dirty(True)
        self.statusBar().showMessage(f"{label} surface and cut/fill layer added (blue = cut, red = fill)", 8000)

    def design_export_csv(self):
        from core import crs as crsmod
        res = getattr(self, "_design_result", None)
        if res is None:
            return
        c = self._design_ctx
        path, _ = QFileDialog.getSaveFileName(self, "Design report", f"{c['kind']}_report.csv", "CSV (*.csv)")
        if not path:
            return
        s = res["stats"]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("item,value\n")
            f.write(f"design,\"{c['title']}\"\nexisting_surface,\"{c['surface']}\"\n")
            f.write(f"coordinate_system,\"{crsmod.label(self.project.crs)}\"\n")
            for k, val in c["spec"].items():
                f.write(f"param_{k},{val}\n")
            for k, val in s.items():
                if isinstance(val, (int, float)):
                    f.write(f"{k},{val:.4f}\n")
            from ui.design_dialog import earthwork_quantities
            for k, val in earthwork_quantities(s, c["spec"]).items():
                if isinstance(val, (int, float)):
                    f.write(f"{k},{val:.4f}\n")
            f.write("sign_convention,\"dz = design - existing; negative = cut (dredge / excavation), positive = fill\"\n")
            f.write("levels,\"all levels and Z values are elevations (Z positive up); depth = -Z\"\n")
            if c["kind"] == "trench":
                f.write("\nkp_from_km,kp_to_km,excavation_m3\n")
                for a, b, vol in res["table"]:
                    f.write(f"{a:.3f},{b:.3f},{vol:.3f}\n")
        self.statusBar().showMessage(f"Saved {os.path.basename(path)}", 6000)

    def design_export_tif(self, which):
        from core.surface import Dem
        from fileio.surface_io import export_dem
        res = getattr(self, "_design_result", None)
        if res is None:
            return
        name = "design_surface.tif" if which == "design" else "design_cut_fill.tif"
        path, _ = QFileDialog.getSaveFileName(self, "GeoTIFF", name, "GeoTIFF (*.tif)")
        if path:
            z = res["G"] if which == "design" else res["dz"]
            export_dem(Dem(z, res["x0"], res["ytop"], res["cell"], which), path, crs=self.project.crs)
            self.statusBar().showMessage(f"Saved {os.path.basename(path)}", 6000)

    # ---------------------------------------------------------------- slope map
    def open_slope_map(self):
        from core.design import slope_map
        surfs = [d for d in self.project.datasets if d.kind in ("grid", "tin") and
                 getattr(d.obj, "cvals", None) is None]
        if not surfs:
            QMessageBox.information(self, "Slope map", "Create or load a DEM / TIN first.")
            return
        name, ok = QInputDialog.getItem(self, "Slope map", "Surface:", [d.name for d in surfs], 0, False)
        if not ok:
            return
        thr, ok = QInputDialog.getText(self, "Slope map", "Report the area steeper than (degrees, comma separated):",
                                       text="5, 10, 15")
        if not ok:
            return
        try:
            thresholds = tuple(float(t) for t in thr.replace(";", ",").split(",") if t.strip())
        except ValueError:
            thresholds = (5.0, 10.0, 15.0)
        ds = next(d for d in surfs if d.name == name)
        self._slope_ctx = (ds, thresholds)
        self._run_task("Computing slopes…", slope_map, (ds.obj,), dict(thresholds=thresholds),
                       self._on_slope_done, report_progress=True)

    @Slot(object)
    def _on_slope_done(self, res):
        from core.project import Dataset
        from core.surface import Dem
        self._close_progress()
        ds, thresholds = self._slope_ctx
        s = res["stats"]
        top = max(float(np.nanpercentile(res["slope"], 98)), max(thresholds) if thresholds else 10.0)
        dem = Dem(res["z"], res["x0"], res["ytop"], res["cell"], "slope", cvals=res["slope"], crange=(0.0, top),
                  meta={"source": ds.name})
        dem.cpalette = "Turbo"
        self.project.add(Dataset(self.project.unique_name(f"Slope · {ds.name}"), "grid", dem, crs=self.project.crs,
                                 source_crs=self.project.crs))
        self._project_changed()
        self._set_dirty(True)
        above = "\n".join(f"   steeper than {t:g}°:  {frac:.1%} of the area" for t, frac in s["above"].items())
        QMessageBox.information(self, "Slope map",
                                f"{ds.name}  (cell {s['cell']:g} m)\n\n"
                                f"Mean slope {s['mean']:.2f}°   ·   95 % below {s['p95']:.2f}°   ·   max {s['max']:.2f}°\n"
                                f"{above}\n\nLayer colours (Turbo): 0° blue … {top:.1f}° red.")


class ContourMixin:
    def open_contour_dialog(self):
        from PySide6.QtWidgets import QDialog

        from core.contours import make_contours
        from core.surface import Tin
        from ui.contour_dialog import ContourDialog
        surfs = [d for d in self.project.datasets if d.kind in ("grid", "tin") and
                 getattr(d.obj, "cvals", None) is None]
        if not surfs:
            QMessageBox.information(self, "Contours", "Create or load a DEM / TIN first.")
            return
        lo, hi = surfs[0].bbox()
        dlg = ContourDialog([(d.id, d.name, isinstance(d.obj, Tin)) for d in surfs], (float(lo[2]), float(hi[2])),
                            self.depth_mode, self)
        if dlg.exec() != QDialog.Accepted:
            return
        sid, prm = dlg.params()
        self._contour_src = self.project.get(sid)
        self._run_task("Creating contours…", make_contours, (self._contour_src.obj,), prm, self._on_contours_done,
                       report_progress=True)

    @Slot(object)
    def _on_contours_done(self, cs):
        from core.project import Dataset
        self._close_progress()
        src = self._contour_src
        if not cs.lines:
            QMessageBox.information(self, "Contours", "No contour lines in this range — check interval / base.")
            return
        ds = Dataset(self.project.unique_name(f"Contours {cs.interval:g} m · {src.name}"), "contour", cs,
                     crs=self.project.crs, source_crs=self.project.crs, derived_from=src.id,
                     edit_version=src.edit_version)
        self.project.add(ds)
        self._project_changed()
        self._set_dirty(True)
        self.statusBar().showMessage(f"{len(cs.lines):,} contour lines on {cs.n_levels} levels "
                                     f"(major every {cs.index_every})", 8000)

    def _show_contours(self, rebuild=False):
        v = self.viewer
        ids = set()
        for d in self.project.of_kind("contour"):
            ids.add(d.id)
            if rebuild or d.id not in v.contours:
                v.set_contours(d.id, d.obj, depth=self.depth_mode)
            v.set_contours_visible(d.id, d.visible)
        for cid in list(v.contours):
            if cid not in ids:
                v.set_contours(cid, None)

    def export_contours(self, ds):
        from fileio.contour_io import export_contours
        stem = os.path.splitext(self._project_path_stem("contours"))[0] + "_contours"
        path, flt = QFileDialog.getSaveFileName(self, "Export contours", stem + ".shp",
                                                "Shapefile 3D (*.shp);;DXF 3D polylines (*.dxf)")
        if not path:
            return
        if not path.lower().endswith((".shp", ".dxf")):
            path += ".dxf" if "DXF" in flt else ".shp"
        labels = None
        if path.lower().endswith(".dxf"):
            from PySide6.QtWidgets import QDialog

            from ui.contour_dialog import LabelDialog
            dlg = LabelDialog(ds.obj, self.depth_mode, self)
            if dlg.exec() != QDialog.Accepted:
                return
            labels = dlg.options()
        self._exporting = True
        self._run_task(f"Exporting {os.path.basename(path)}…", export_contours, (ds.obj, path),
                       dict(crs=self.project.crs, labels=labels), lambda _: self._on_surface_exported(path),
                       report_progress=True)
