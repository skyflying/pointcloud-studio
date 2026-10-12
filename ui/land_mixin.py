"""Land menu (terrain, change, stockpiles, road, hydrology, clearance) and vector reference layers."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

COLORS = {"streams": (0.30, 0.60, 1.00, 1.0), "watershed": (1.00, 0.85, 0.30, 1.0),
          "inundation": (0.30, 0.85, 0.95, 1.0), "infringements": (0.94, 0.28, 0.44, 1.0),
          "stockpiles": (0.55, 1.00, 0.55, 1.0), "areas": (0.78, 0.49, 1.00, 1.0), "line": (0.80, 0.80, 0.85, 0.9),
          "outlet": (1.00, 0.85, 0.30, 1.0)}


class LandMixin:
    def _init_land_state(self):
        self.land_dialog = None
        self._land_result = None

    # ================================================================ vector layers
    def import_vector_layer(self, path=None):
        from core import crs as crsmod
        from core.project import Dataset
        from core.vector_layer import VectorLayer
        from fileio import vector_io
        from ui.crs_dialog import CrsDialog
        from ui.vector_pick import pick_vector
        if path:
            from ui.vector_pick import pick_layer
            layer = pick_layer(self, path)
        else:
            path, layer = pick_vector(self, "Import vector layer")
        if not path:
            return
        try:
            feats, src, info = vector_io.read(path, layer)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Cannot read file", f"{type(e).__name__}: {e}")
            return
        if not feats:
            QMessageBox.warning(self, "Empty", "The file contains no features.")
            return
        proj = self.project
        if src is None:
            ok, src = CrsDialog.ask(self, "Coordinate system of the file",
                                    f"{os.path.basename(path)} does not state its coordinate system ({info}).",
                                    initial=proj.crs, allow_unknown=proj.crs is None)
            if not ok:
                return
        if not proj.datasets:
            self._new_project()
            proj = self.project
            proj.crs = src
            if src is not None and crsmod.is_geographic(src):
                ok, pcrs = CrsDialog.ask(self, "Project coordinate system",
                                         "The file is in latitude / longitude. Choose a projected system (metres) "
                                         "for the project.", allow_unknown=False, warn_geographic=True)
                if ok and pcrs is not None:
                    proj.crs = pcrs
        feats = vector_io.reproject(feats, src, proj.crs)
        name = os.path.splitext(os.path.basename(path))[0] + (f" · {layer}" if layer else "")
        proj.add(Dataset(proj.unique_name(name), "vector", VectorLayer(feats, meta={"info": info}), path,
                         crs=proj.crs, source_crs=src))
        self._project_changed(fit=len(proj.datasets) == 1)
        self._set_dirty(True)
        self.statusBar().showMessage(f"Imported {info}", 8000)

    def vector_use_polygon(self, ds_id):
        ds = self.project.get(ds_id)
        polys = ds.obj.polygons() if ds else []
        if not polys:
            return
        k = 0
        if len(polys) > 1:
            item, ok = QInputDialog.getItem(self, "Analysis area", "Polygon:",
                                            [f"{i + 1}: {n}" for i, (_k, n, _r) in enumerate(polys)], 0, False)
            if not ok:
                return
            k = int(item.split(":")[0]) - 1
        self._finish_polygon(np.asarray(polys[k][2], float))
        self.statusBar().showMessage(f"Analysis area: {polys[k][1]} (used by volumes, earthworks and filters)", 8000)

    def vector_to_routes(self, ds_id):
        from core.project import Dataset
        from core.route import Route
        ds = self.project.get(ds_id)
        lines = ds.obj.lines() if ds else []
        if not lines:
            return
        items = ["(all lines)"] + [f"{i + 1}: {n}" for i, (_k, n, _p) in enumerate(lines)]
        item, ok = QInputDialog.getItem(self, "Make routes", "Line(s) to use as routes / centre lines:", items, 0,
                                        False)
        if not ok:
            return
        pick = lines if item == "(all lines)" else [lines[int(item.split(":")[0]) - 1]]
        for _k, n, p in pick:
            z = p[:, 2] if p.shape[1] >= 3 and np.any(np.abs(p[:, 2]) > 1e-9) else None
            self.project.add(Dataset(self.project.unique_name(n), "route", Route(n, p[:, :2], z=z),
                                     crs=self.project.crs, source_crs=self.project.crs))
        self._project_changed()
        self._set_dirty(True)
        self.statusBar().showMessage(f"{len(pick)} route(s) created (chainage from the first vertex)", 8000)

    def vector_export(self, ds_id):
        from fileio import vector_io
        ds = self.project.get(ds_id)
        if ds is None:
            return
        path, flt = QFileDialog.getSaveFileName(self, "Export vector layer", ds.name + ".gpkg", vector_io.WRITE_FILTER)
        if not path:
            return
        path = vector_io.ext_from_filter(path, flt)
        self._write_feature_groups(path, {"features": ds.obj.features}, single=True)

    def _write_feature_groups(self, path, groups, single=False):
        """Write {name: features}; a Shapefile holds one geometry type, so mixed groups are split."""
        from fileio import vector_io
        base, ext = os.path.splitext(path)
        written = []
        for gname, feats in groups.items():
            if not feats:
                continue
            by = {}
            for f in feats:
                by.setdefault(f["type"], []).append(f)
            parts = list(by.items()) if ext == ".shp" and len(by) > 1 else [("", feats)]
            for t, fs in parts:
                suffix = ("" if single else f"_{gname}") + (f"_{t.lower()}" if t else "")
                p = f"{base}{suffix}{ext}"
                try:
                    vector_io.write(p, fs, self.project.crs, layer=gname)
                    written.append(p)
                except Exception as e:  # noqa: BLE001
                    QMessageBox.critical(self, "Export failed", f"{os.path.basename(p)}: {type(e).__name__}: {e}")
        if written:
            self.statusBar().showMessage("Saved " + ", ".join(os.path.basename(p) for p in written), 10000)

    # ================================================================ dialog
    def open_land_dialog(self, tab=0):
        from ui.land_dialog import LandDialog
        if self.land_dialog is None:
            self.land_dialog = LandDialog(self, self)
        self._land_context()
        self.land_dialog.tabs.setCurrentIndex(tab)
        self.land_dialog.show()
        self.land_dialog.raise_()
        self.land_dialog.activateWindow()

    def _land_context(self):
        if getattr(self, "land_dialog", None) is None:
            return
        P = self.project
        surfs = [(d.id, d.name) for d in P.datasets if d.kind in ("grid", "tin") and getattr(d.obj, "cvals", None) is None]
        vec = P.of_kind("vector")
        lines = [(("route", d.id), f"{d.name}" + (" (3D)" if d.obj.z is not None else "")) for d in P.of_kind("route")]
        for d in vec:
            for k, n, p in d.obj.lines():
                three = p.shape[1] >= 3 and np.any(np.abs(p[:, 2]) > 1e-9)
                lines.append((("vec", d.id, k), f"{d.name}: {n}" + (" (3D)" if three else "")))
        pts = [((d.id, k), f"{d.name}: {d.obj.label(k)}") for d in vec for k, f in enumerate(d.obj.features)
               if f["type"] == "Point"]
        ctx = {"surfaces": surfs, "clouds": [(d.id, d.name) for d in P.points],
               "routes": [(d.id, d.name) for d in P.of_kind("route")],
               "polys": [(d.id, f"{d.name} ({len(d.obj.polygons())} polygons)") for d in vec if d.obj.polygons()],
               "points": pts[:2000], "lines": lines,
               "epochs": [(d.id, d.name, d.survey_date) for d in P.datasets if d.kind in ("grid", "tin")
                          and getattr(d.obj, "cvals", None) is None]}
        self.land_dialog.set_context(ctx)

    # ================================================================ run
    def land_run(self, kind, sp):
        from core import land, terrain
        if self._busy(self.land_dialog):
            return
        P = self.project
        g = P.get
        used = [v for k, v in sp.items() if k in ("surface", "pc", "a", "b", "route", "polys")]
        used += [i for i, _d in sp.get("epochs", [])]
        if isinstance(sp.get("line"), tuple):
            used.append(sp["line"][1])
        if any(i is not None and g(i) is None for i in used):
            self._land_context()
            return self.land_dialog.show_error("A dataset was removed — check the choices and run again")
        try:
            if kind == "terrain":
                if sp["mode"] == "products":
                    fn, kw = terrain.terrain_products, dict(surface=g(sp["surface"]).obj, cell=sp["cell"],
                                                            products=tuple(sp["products"]))
                else:
                    fn, kw = terrain.surface_models, dict(pc=g(sp["pc"]).obj, cell=sp["cell"],
                                                          canopy_min=sp["canopy"])
            elif kind == "change":
                A, B = g(sp["a"]), g(sp["b"])
                if sp["mode"] == "dod":
                    from core.sandwave import years_between
                    polys = []
                    if sp["polys"] is not None:
                        polys = [(n, r) for _k, n, r in g(sp["polys"]).obj.polygons()]
                    fn, kw = terrain.dod, dict(A=A.obj, B=B.obj, cell=sp["cell"], sigma_a=sp["sa"], sigma_b=sp["sb"],
                                               t=sp["t"], polygons=polys,
                                               years=years_between(A.survey_date, B.survey_date))
                else:
                    fn, kw = terrain.m3c2, dict(pc1=A.obj, pc2=B.obj, spacing=sp["spacing"], normal_scale=sp["normal"],
                                                cyl_diameter=sp["diam"], max_depth=sp["depth"], reg_error=sp["reg"])
            elif kind == "stockpile":
                surfs = [(g(i).name, d, g(i).obj) for i, d in sp["epochs"]]
                polys = [(n, r) for _k, n, r in g(sp["polys"]).obj.polygons()]
                fn, kw = land.stockpile_volumes, dict(surfaces=surfs, polygons=polys, base=sp["base"],
                                                      level=sp["level"], cell=sp["cell"], density=sp["density"])
            elif kind == "road":
                kp = sp["kp"] or (None, None)
                fn, kw = land.road_design, dict(surface=g(sp["surface"]).obj, route=g(sp["route"]).obj,
                                                kp_from=kp[0], kp_to=kp[1], grade=sp["grade"], pvi=sp["pvi"],
                                                smooth=sp["smooth"], offset=sp["offset"], half_width=sp["w"],
                                                crossfall=sp["cf"], cut_n=sp["cut"], fill_n=sp["fill"],
                                                reach=sp["reach"], cell=sp["cell"], bin_m=sp["bin"],
                                                cut_to_fill=sp["c2f"], section_every=sp["sec"])
            elif kind == "hydro":
                out = sp["outlet"]
                if isinstance(out, tuple) and len(out) == 2 and out[0] == "point":
                    did, k = out[1]
                    out = tuple(g(did).obj.features[k]["parts"][0][0, :2])
                fn, kw = land.hydrology, dict(surface=g(sp["surface"]).obj, cell=sp["cell"], stream_area=sp["area"],
                                              outlet=out, snap=sp["snap"], level=sp["level"], seed=sp["seed"])
            else:
                fn, kw = land.clearance, dict(pc=g(sp["pc"]).obj, line=self._clearance_line(sp), limit=sp["limit"],
                                              classes=sp["classes"], bin_m=sp["bin"])
        except Exception as e:  # noqa: BLE001
            return self.land_dialog.show_error(f"{type(e).__name__}: {e}")
        self._run_task("Land analysis…", fn, (), kw, self._on_land_done, report_progress=True)

    def _clearance_line(self, sp):
        from core.filters import surface_z
        src = sp["line"]
        if src[0] == "route":
            r = self.project.get(src[1]).obj
            xy, z = r.xy, r.z
        else:
            p = self.project.get(src[1]).obj.features[src[2]]["parts"][0]
            xy = p[:, :2]
            z = p[:, 2] if p.shape[1] >= 3 and np.any(np.abs(p[:, 2]) > 1e-9) else None
        if sp["hag"] is not None:
            seg = np.diff(xy, axis=0)
            L = np.r_[0, np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))]
            c = np.r_[np.arange(0, L[-1], 1.0), L[-1]]
            dense = np.column_stack([np.interp(c, L, xy[:, 0]), np.interp(c, L, xy[:, 1])])
            gz = surface_z(self.project.get(sp["surface"]).obj, dense[:, 0], dense[:, 1])
            if not np.isfinite(gz).any():
                raise ValueError("The ground surface does not cover the line")
            gz = np.interp(c, c[np.isfinite(gz)], gz[np.isfinite(gz)])
            return np.column_stack([dense, gz + sp["hag"]])
        if z is None:
            raise ValueError("This line has no Z — tick 'height above ground' or use a 3D line")
        return np.column_stack([xy, z])

    @Slot(object)
    def _on_land_done(self, res):
        self._close_progress()
        self._land_result = res
        self.land_dialog.show_result(res)
        self._land_overlay()
        self.statusBar().showMessage(f"{res['title']}: done", 8000)

    # ================================================================ 3D
    def _land_overlay(self):
        res = self._land_result
        if res is None:
            self.viewer.set_analysis_overlay()
            return
        lines, labels = [], []
        for gname, feats in res["features"].items():
            col = COLORS.get(gname, (0.9, 0.9, 0.9, 1.0))
            for f in feats[:20000]:
                for p in f["parts"]:
                    p = np.asarray(p, float)
                    z = p[:, 2] if p.shape[1] >= 3 else None
                    if f["type"] == "Point":
                        x, y = p[0, :2]
                        for a, b in (((x - 3, y), (x + 3, y)), ((x, y - 3), (x, y + 3))):
                            lines.append({"xy": np.array([a, b]), "z": None, "color": col})
                        continue
                    q = p if f["type"] != "Polygon" else np.vstack([p, p[:1]])
                    lines.append({"xy": q[:, :2], "z": None if z is None or f["type"] == "Polygon" else
                                  (q[:, 2] if q.shape[1] >= 3 else None), "color": col})
                a = f["attrs"]
                txt = None
                if gname == "stockpiles":
                    txt = f"{a.get('NAME')}: {a.get('VOL_M3', 0):,.0f} m³"
                elif gname == "infringements":
                    txt = f"clearance {a.get('MIN_CLR', 0):.2f} m"
                elif gname in ("watershed", "inundation"):
                    txt = f"{gname} {a.get('AREA_KM2', 0) or a.get('AREA_M2', 0) / 1e6:.4f} km²"
                elif gname == "areas":
                    txt = f"{a.get('NAME')}: net {a.get('NET_M3', 0):+,.0f} m³"
                if txt:
                    p0 = np.asarray(f["parts"][0], float)
                    labels.append((float(p0[:, 0].mean()), float(p0[:, 1].mean()), None, txt))
        self.viewer.set_analysis_overlay(lines, labels[:100])

    def land_clear_overlay(self):
        self.viewer.set_analysis_overlay()

    # ================================================================ outputs
    def land_add_layers(self):
        from core.project import Dataset
        res = self._land_result
        if not res or not res["layers"]:
            return
        for name, dem in res["layers"]:
            self.project.add(Dataset(self.project.unique_name(name), "grid", dem, crs=self.project.crs,
                                     source_crs=self.project.crs))
        self._project_changed()
        self._set_dirty(True)
        self.statusBar().showMessage(f"Added {len(res['layers'])} layer(s)", 6000)

    def land_add_sections(self):
        from core import profile as prof
        res = self._land_result
        if not res or not res.get("sections"):
            return
        first = len(self.sections)
        for s in res["sections"]:
            self.sections.append(prof.Section(f"Road KP {s['kp']:.3f}", np.asarray(s["xy"], float), width=0.5))
        self.active_section = first
        self._set_dirty(True)
        self.profile_dock.show()
        self._recompute_section(fit=True)
        if hasattr(self, "_refresh_drawings"):
            self._refresh_drawings()

    def land_export_csv(self):
        res = self._land_result
        if not res:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export tables (CSV)", f"{res['kind']}.csv", "CSV (*.csv)")
        if not path:
            return
        base = os.path.splitext(path)[0]
        written = []
        for name, rows in res["tables"].items():
            if rows:
                safe = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
                self._write_rows(f"{base}_{safe}.csv", rows)
                written.append(f"{base}_{safe}.csv")
        with open(base + "_summary.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write("item,value\n")
            for a, b in res["summary"]:
                f.write(f'"{a}","{b}"\n')
        self.statusBar().showMessage("Saved " + ", ".join(os.path.basename(p) for p in written + [base + "_summary.csv"]),
                                     10000)

    def land_export_gis(self):
        from fileio import vector_io
        res = self._land_result
        if not res or not res["features"]:
            return
        path, flt = QFileDialog.getSaveFileName(self, "Export (GIS)", f"{res['kind']}.gpkg", vector_io.WRITE_FILTER)
        if path:
            self._write_feature_groups(vector_io.ext_from_filter(path, flt), res["features"])

    def land_export_tifs(self):
        from core.surface import Dem
        from fileio.surface_io import export_dem
        res = self._land_result
        if not res or not res["layers"]:
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder for GeoTIFFs")
        if not folder:
            return
        for name, dem in res["layers"]:
            vals = dem.cvals if dem.cvals is not None else dem.z
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")
            export_dem(Dem(np.asarray(vals, np.float32), dem.x0, dem.ytop, dem.cell, "export"),
                       os.path.join(folder, f"{safe}.tif"), crs=self.project.crs)
        self.statusBar().showMessage(f"Saved {len(res['layers'])} GeoTIFF(s) to {folder}", 8000)

    def land_save_chart(self):
        if self.land_dialog is None or not self._land_result or not self._land_result.get("chart"):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", f"{self._land_result['kind']}.png", "PNG (*.png)")
        if path:
            self.land_dialog.chart.grab().save(path)
