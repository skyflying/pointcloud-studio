"""Cable & Foundations: structures, survey dates, scour monitoring, sand-wave migration."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox

from ui.cable_mixin import RGBA


class SeabedMixin:
    def _init_seabed_state(self):
        self.seabed_dialog = None
        self._seabed_result = None
        self._seabed_ctx = {}

    # ---------------------------------------------------------------- structures
    def import_structures(self):
        from core import crs as crsmod
        from core.project import Dataset
        from core.structures import reproject
        from ui.structure_dialog import StructureImportDialog
        from ui.vector_pick import pick_vector
        path, layer = pick_vector(self, "Import structures", "CSV / text (*.csv *.txt *.dat)")
        if not path:
            return
        try:
            dlg = StructureImportDialog(path, self.project.crs, self, layer=layer)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Cannot read file", f"{type(e).__name__}: {e}")
            return
        if dlg.exec() != QDialog.Accepted or dlg.result is None:
            return
        st, src = dlg.result, dlg.crs
        proj = self.project
        if not proj.datasets:
            self._new_project()
            proj = self.project
            proj.crs = src
            if src is not None and crsmod.is_geographic(src):
                from ui.crs_dialog import CrsDialog
                ok, pcrs = CrsDialog.ask(self, "Project coordinate system",
                                         "The structures are in latitude / longitude. Choose a projected system for "
                                         "the project (metres for diameters and scour radii).", allow_unknown=False,
                                         warn_geographic=True)
                if ok and pcrs is not None:
                    proj.crs = pcrs
        if proj.crs is not None and crsmod.is_geographic(proj.crs):
            QMessageBox.warning(self, "Geographic coordinates", "The project is in latitude / longitude — scour radii "
                                "and distances need a projected system (metres).")
        note = ""
        if src is not None and proj.crs is not None and crsmod.same(src, proj.crs) is False:
            reproject(st, src, proj.crs)
            note = f" · reprojected from {crsmod.label(src)}"
        name = os.path.splitext(os.path.basename(path))[0]
        proj.add(Dataset(proj.unique_name(name), "structure", st, path, crs=proj.crs, source_crs=src))
        self._project_changed(fit=len(proj.datasets) == 1)
        self._set_dirty(True)
        self.statusBar().showMessage(f"Imported {len(st)} structure(s){note}", 8000)

    # ---------------------------------------------------------------- survey dates
    def ds_set_date(self, ds_id):
        import re
        ds = self.project.get(ds_id)
        if ds is None:
            return
        t, ok = QInputDialog.getText(self, "Survey date", f"Survey date of {ds.name} (YYYY-MM-DD, empty = none):",
                                     text=ds.survey_date or "")
        if not ok:
            return
        t = t.strip()
        if t and not re.match(r"^\d{4}-\d{2}-\d{2}$", t):
            QMessageBox.warning(self, "Survey date", "Use the form YYYY-MM-DD, e.g. 2025-06-30")
            return
        self.seabed_set_date(ds_id, t or None)
        self._project_changed()

    def seabed_set_date(self, ds_id, text):
        ds = self.project.get(ds_id)
        if ds is not None and ds.survey_date != text:
            ds.survey_date = text
            self._set_dirty(True)
            self._refresh_data_card()

    # ---------------------------------------------------------------- dialog
    def open_seabed_dialog(self, tab=0):
        from ui.seabed_dialog import SeabedDialog
        if self.seabed_dialog is None:
            self.seabed_dialog = SeabedDialog(self, self)
        self._seabed_context()
        self.seabed_dialog.tabs.setCurrentIndex(tab)
        self.seabed_dialog.show()
        self.seabed_dialog.raise_()
        self.seabed_dialog.activateWindow()

    def _seabed_context(self):
        if getattr(self, "seabed_dialog", None) is None:
            return
        P = self.project
        structs = [(d.id, d.name, list(d.obj.names)) for d in P.of_kind("structure")]
        surfs = [(d.id, d.name, d.survey_date) for d in P.datasets if d.kind in ("grid", "tin")
                 and getattr(d.obj, "cvals", None) is None]
        clouds = [(d.id, f"{d.name}  (points)", d.survey_date) for d in P.points]
        routes = [(d.id, d.name) for d in P.of_kind("route")]
        cables = [(d.id, d.name) for d in P.of_kind("route") if d.obj.z is not None]
        self.seabed_dialog.set_context(structs, surfs, clouds, routes, cables, self.depth_mode)

    # ---------------------------------------------------------------- run
    def seabed_run(self, kind, sp):
        if self._busy(self.seabed_dialog):
            return
        P = self.project
        notes = []
        epochs = []
        ids = [i for i, _d in sp["epochs"]] + [sp.get(k) for k in ("structures", "route", "cable")]
        if sp.get("baseline", {}).get("id") is not None:
            ids.append(sp["baseline"]["id"])
        if any(i is not None and P.get(i) is None for i in ids):
            self._seabed_context()
            return self.seabed_dialog.show_error("A dataset was removed — check the choices and run again")
        for i, d in sp["epochs"]:
            ds = P.get(i)
            epochs.append({"name": ds.name, "date": d, "obj": ds.obj, "id": i})
        undated = [e["name"] for e in epochs if not e["date"]]
        if undated and len(epochs) > 1:
            notes.append("No survey date: " + ", ".join(undated) + " — order as listed, no rates")
        if kind == "scour":
            from core import scour
            S = P.get(sp["structures"])
            idx = list(range(len(S.obj))) if sp["which"] in (None, -1) else [sp["which"]]
            base = dict(sp["baseline"])
            if base["mode"] == "surface":
                bd = P.get(base.pop("id"))
                base.update(obj=bd.obj, name=bd.name)
            kw = dict(structures=S.obj, indices=idx, epochs=epochs, baseline=base, r_max=sp["r_max"],
                      n_rays=sp["n_rays"], threshold=sp["threshold"], margin=sp["margin"], cell=sp["cell"])
            fn, label = scour.scour_analysis, "Scour analysis…"
            self._seabed_ctx = {"kind": kind, "structures_id": S.id, "notes": notes, "title": f"Scour — {S.name}"}
        else:
            from core import sandwave
            r = P.get(sp["route"])
            cab = P.get(sp["cable"]).obj if sp["cable"] is not None else None
            kw = dict(route=r.obj, epochs=epochs, step=sp["step"], window=sp["window"], spacing=sp["spacing"],
                      max_shift=sp["max_shift"], hp_len=sp["hp_len"], method=sp["method"], cell=sp["cell"],
                      min_corr=sp["min_corr"], min_height=sp["min_height"], cable_route=cab, target=sp["target"],
                      horizons=sp["horizons"])
            if sp["method"] == "2d" and any(type(e["obj"]).__name__ == "PointCloud" for e in epochs):
                notes.append("A point cloud is among the surveys — the profile method was used")
            fn, label = sandwave.migration, "Sand wave migration…"
            self._seabed_ctx = {"kind": kind, "route_id": r.id, "notes": notes, "title": f"Sand waves — {r.name}"}
        self._run_task(label, fn, (), kw, self._on_seabed_done, report_progress=True)

    @Slot(object)
    def _on_seabed_done(self, res):
        self._close_progress()
        self._seabed_result = res
        notes = list(self._seabed_ctx.get("notes", ()))
        if res.get("forecast_note"):
            notes.append(res["forecast_note"])
        self.seabed_dialog.show_result(res, self.depth_mode, notes, self._seabed_ctx.get("title"))
        self._seabed_overlay()
        if res["kind"] == "scour":
            last = {}
            for r in res["table"]:
                last[r["structure"]] = r
            mx = np.nanmax([r["max_depth"] for r in last.values()]) if last else np.nan
            self.statusBar().showMessage(f"Scour: {len(last)} structure(s), deepest {mx:.2f} m", 10000)
        else:
            s = res["summary"]
            self.statusBar().showMessage(f"Sand waves: median {s['rate_median']:.2f} m/yr "
                                         f"({s['reliable_windows']} reliable windows)", 10000)

    # ---------------------------------------------------------------- 3D
    def _seabed_overlay(self):
        res = self._seabed_result
        if res is None:
            self.viewer.set_analysis_overlay()
            return
        lines, labels = [], []
        t = np.linspace(0, 2 * np.pi, 49)
        if res["kind"] == "scour":
            for s in res["structures"]:
                e = s["epochs"][-1]["row"]
                cx, cy = s["x"], s["y"]
                lines.append({"xy": np.column_stack([cx + s["r_max"] * np.cos(t), cy + s["r_max"] * np.sin(t)]),
                              "z": None, "color": RGBA["nodata"]})
                ext = e.get("extent")
                if ext and np.isfinite(ext) and ext > 0:
                    lines.append({"xy": np.column_stack([cx + ext * np.cos(t), cy + ext * np.sin(t)]), "z": None,
                                  "color": RGBA["fail"]})
                if np.isfinite(e.get("max_depth", np.nan)):
                    labels.append((cx + s["r_max"], cy, None, f"{s['name']} scour {e['max_depth']:.2f} m"))
        else:
            ne = len(res["epoch_names"])
            main = [w for w in res["windows"] if w["a"] == 0 and w["b"] == ne - 1 and np.isfinite(w.get("dx", np.nan))]
            spacing = res["params"]["spacing"]
            sh = np.array([w["shift"] for w in main]) if main else np.array([1.0])
            k = 0.6 * spacing / max(np.nanmax(sh), 1e-6)            # longest arrow ≈ 0.6 × window spacing
            for w in main:
                p0 = np.array([w["x"], w["y"]])
                d = np.array([w["dx"], w["dy"]]) * k
                L = np.hypot(*d)
                col = RGBA["ok"] if w.get("reliable") else RGBA["nodata"]
                if L > 1e-6:
                    u = d / L
                    head = 0.25 * L
                    left = p0 + d - head * (u + np.array([-u[1], u[0]]) * 0.6)
                    right = p0 + d - head * (u - np.array([-u[1], u[0]]) * 0.6)
                    lines.append({"xy": np.array([p0, p0 + d]), "z": None, "color": col})
                    lines.append({"xy": np.array([left, p0 + d, right]), "z": None, "color": col})
                if w.get("reliable") and np.isfinite(w.get("rate", np.nan)):
                    labels.append((w["x"], w["y"], None, f"{w['rate']:.1f} m/yr"))
            st = res["st"]
            xy = np.column_stack([st["x"], st["y"]])
            for g in res.get("segments", []):
                i0, i1 = g["i0"], min(g["i1"] + 1, len(xy) - 1)
                lines.append({"xy": xy[i0:i1 + 1], "z": None, "color": RGBA["fail"]})
                labels.append((g["x_from"], g["y_from"], None, f"exposed after ~{g.get('years_min', np.nan):g} yr"))
        if len(labels) > 80:
            labels = labels[:80]
        self.viewer.set_analysis_overlay(lines, labels)

    def seabed_clear_overlay(self):
        self.viewer.set_analysis_overlay()

    def seabed_goto(self, row):
        res = self._seabed_result
        if res is None:
            return
        if res["kind"] == "scour":
            s = next((s for s in res["structures"] if s["name"] == row.get("structure")), None)
            if s is not None:
                self.viewer.look_at(s["x"], s["y"], None, extent=s["r_max"] * 4)
        else:
            if "x" in row:
                self.viewer.look_at(row["x"], row["y"], None, extent=res["params"]["window"] * 2)
            elif "kp" in row or "kp_from" in row:
                rd = self.project.get(self._seabed_ctx.get("route_id"))
                if rd is not None:
                    k = row.get("kp", row.get("kp_from"))
                    c = rd.obj.chainage_at_kp(k)
                    x, y = rd.obj.xy_at(np.array([c]))[0]
                    self.viewer.look_at(x, y, None, extent=res["params"]["window"] * 2)

    # ---------------------------------------------------------------- exports
    def _seabed_path(self, caption, default, filt):
        if self._seabed_result is None:
            return None
        path, _ = QFileDialog.getSaveFileName(self, caption, default, filt)
        return path or None

    @staticmethod
    def _write_rows(fname, rows):
        cols = [c for c in dict.fromkeys(c for r in rows for c in r) if c not in ("i0", "i1", "a", "b")]

        def cell(v):
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                return ""
            if isinstance(v, (bool, np.bool_)):
                return "yes" if v else "no"
            s = f"{v:.4f}" if isinstance(v, float) else str(v)
            return f'"{s}"' if "," in s else s

        with open(fname, "w", encoding="utf-8", newline="\n") as f:
            f.write(",".join(cols) + "\n")
            for r in rows:
                f.write(",".join(cell(r.get(c)) for c in cols) + "\n")

    def seabed_export_csv(self):
        res = self._seabed_result
        path = self._seabed_path("Export analysis (CSV)", f"{res['kind'] if res else 'seabed'}.csv", "CSV (*.csv)")
        if not path:
            return
        base = os.path.splitext(path)[0]
        files = []
        if res["kind"] == "scour":
            self._write_rows(base + "_scour.csv", res["table"])
            files.append(base + "_scour.csv")
            rays = []
            for s in res["structures"]:
                for e in s["epochs"]:
                    for k, b in enumerate(s["bearings"]):
                        for r, z, d in zip(s["r"], e["ray_z"][k], e["ray_depth"][k]):
                            rays.append({"structure": s["name"], "epoch": e["name"], "bearing": float(b),
                                         "r": float(r), "z": float(z), "scour_depth": float(d)})
            self._write_rows(base + "_radial_profiles.csv", rays)
            files.append(base + "_radial_profiles.csv")
        else:
            for key in ("windows", "crests", "forecast", "segments"):
                if res.get(key):
                    self._write_rows(f"{base}_{key}.csv", res[key])
                    files.append(f"{base}_{key}.csv")
            st = res["st"]
            cols = {"kp": st["kp"], "chainage_m": st["ch"], "x": st["x"], "y": st["y"]}
            cols.update({n: v for n, v in res["series"].items() if v is not None})
            n = len(st["ch"])
            self._write_rows(base + "_stations.csv",
                             [{k: float(v[i]) for k, v in cols.items()} for i in range(n)])
            files.append(base + "_stations.csv")
        self.statusBar().showMessage("Saved " + ", ".join(os.path.basename(f) for f in files), 10000)

    def seabed_export_shp(self):
        from fileio import vector_io
        res = self._seabed_result
        if res is None:
            return
        path, flt = QFileDialog.getSaveFileName(self, "Export (GIS)", f"{res['kind']}.gpkg", vector_io.WRITE_FILTER)
        if not path:
            return
        path = vector_io.ext_from_filter(path, flt)
        base, ext = os.path.splitext(path)

        def f(v):
            return float(v) if v is not None and np.isfinite(v) else None
        jobs = []
        if res["kind"] == "scour":
            feats = []
            for s in res["structures"]:
                e = s["epochs"][-1]["row"]
                feats.append({"type": "Point", "parts": [np.array([[s["x"], s["y"]]])],
                              "attrs": {"NAME": s["name"], "EPOCH": e["epoch"], "DATE": e["date"], "DIAM_M": s["diameter"],
                                        "MAXDEP_M": f(e["max_depth"]), "R_MAX_M": f(e["r_at_max"]),
                                        "BRG_MAX": f(e["bearing_at_max"]), "EXTENT_M": f(e["extent"]),
                                        "AREA_M2": f(e["scour_area"]), "VOL_M3": f(e["scour_volume"]),
                                        "REF_Z": f(e["reference"])}})
            jobs.append((path, feats, "scour"))
        else:
            vec = [{"type": "LineString", "parts": [np.array([[r["x"], r["y"]], [r["x"] + r["dx"], r["y"] + r["dy"]]])],
                    "attrs": {"KP": r["kp"], "SHIFT_M": f(r["shift"]), "RATE_MYR": f(r["rate"]),
                              "AZIMUTH": f(r["azimuth"]), "ALONG_M": f(r["along"]), "CORR": f(r["corr"]),
                              "RELIABLE": "yes" if r.get("reliable") else "no", "PAIR": r["pair"]}}
                   for r in res["windows"] if np.isfinite(r.get("dx", np.nan))]
            jobs.append((f"{base}_vectors{ext}", vec, "migration_vectors"))
            if res.get("segments"):
                st = res["st"]
                xy = np.column_stack([st["x"], st["y"]])
                exp = []
                for g in res["segments"]:
                    i0, i1 = g["i0"], max(min(g["i1"] + 1, len(xy) - 1), g["i0"] + 1)
                    exp.append({"type": "LineString", "parts": [xy[i0:i1 + 1]],
                                "attrs": {"KP_FROM": g["kp_from"], "KP_TO": g["kp_to"], "LENGTH_M": g["length"],
                                          "YEARS_MIN": f(g.get("years_min"))}})
                jobs.append((f"{base}_exposure{ext}", exp, "exposure_risk"))
        written = []
        for p, feats, layer in jobs:
            if feats:
                vector_io.write(p, feats, self.project.crs, layer=layer)
                written.append(p)
        self.statusBar().showMessage("Saved " + ", ".join(os.path.basename(x) for x in written), 8000)

    def seabed_save_chart(self):
        if self.seabed_dialog is None or self._seabed_result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", f"{self._seabed_result['kind']}.png", "PNG (*.png)")
        if path:
            pm = self.seabed_dialog.chart.grab()
            if self._seabed_result["kind"] == "scour":
                from PySide6.QtGui import QPainter, QPixmap
                m = self.seabed_dialog.map.grab()
                out = QPixmap(m.width() + pm.width(), max(m.height(), pm.height()))
                out.fill()
                p = QPainter(out)
                p.drawPixmap(0, 0, m)
                p.drawPixmap(m.width(), 0, pm)
                p.end()
                pm = out
            pm.save(path)
            self.statusBar().showMessage(f"Saved {os.path.basename(path)}", 6000)

    # ---------------------------------------------------------------- scour layers
    def _scour_dem(self, si, ei):
        from core.surface import Dem
        s = self._seabed_result["structures"][si]
        e = s["epochs"][min(ei, len(s["epochs"]) - 1)]
        d = e["depth"]
        m = float(np.nanmax(np.abs(d))) if np.isfinite(d).any() else 1.0
        z = e["z"] if e.get("z") is not None else (e["ref"] - d)       # seabed = reference − depth
        z = np.where(np.isfinite(d), z, np.nan).astype(np.float32)
        dem = Dem(z, s["x0"], s["ytop"], s["cell"], "difference", cvals=(-d).astype(np.float32), crange=(-m, m),
                  meta={"title": f"scour depth {s['name']} {e['name']}"})
        return dem, s, e

    def seabed_add_layer(self, si, ei):
        from core.project import Dataset
        res = self._seabed_result
        if res is None or res["kind"] != "scour" or si < 0:
            return
        dem, s, e = self._scour_dem(si, ei)
        self.project.add(Dataset(self.project.unique_name(f"Scour {s['name']} · {e['name']}"), "grid", dem,
                                 crs=self.project.crs, source_crs=self.project.crs))
        self._project_changed()
        self._set_dirty(True)
        self.statusBar().showMessage("Scour layer added (blue = below the reference)", 8000)

    def seabed_export_tifs(self, ei):
        from core.surface import Dem
        from fileio.surface_io import export_dem
        res = self._seabed_result
        if res is None or res["kind"] != "scour":
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder for scour-depth GeoTIFFs")
        if not folder:
            return
        n = 0
        for si, s in enumerate(res["structures"]):
            e = s["epochs"][min(max(ei, 0), len(s["epochs"]) - 1)]
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in f"{s['name']}_{e['name']}")
            export_dem(Dem(e["depth"], s["x0"], s["ytop"], s["cell"], "scour depth"),
                       os.path.join(folder, f"scour_{safe}.tif"), crs=self.project.crs)
            n += 1
        self.statusBar().showMessage(f"Saved {n} GeoTIFF(s) — values: scour depth below the reference (+ = deeper)",
                                     10000)
