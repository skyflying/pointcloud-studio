"""Cable & Foundations menu: depth of burial, free spans, rock berm as-built (core/cable.py)."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QFileDialog

RGBA = {"ok": (0.02, 0.84, 0.63, 1.0), "fail": (0.94, 0.28, 0.44, 1.0), "nodata": (0.55, 0.57, 0.61, 0.8),
        "short": (1.0, 0.62, 0.26, 1.0), "cover": (0.97, 0.15, 0.52, 1.0)}


def _berm_job(asbuilt, route, kp_from, kp_to, pre, template, design, cell, crest_width, height, slope_n, end_n,
              half_width, tol, step, bin_m, min_cover, every, progress=None):
    from core import cable
    sub = (lambda a, b: (lambda f: progress(a + (b - a) * f))) if progress else (lambda a, b: None)
    design_dem = None
    if template:
        t = cable.berm_template(pre, route, kp_from, kp_to, crest_width, height, slope_n, end_n, cell,
                                progress=sub(0.0, 0.4))
        design_dem = t["design"]
        design = design_dem
    # grid half-width: the template's full reach, else the given footprint half-width
    hw = crest_width / 2.0 + height * slope_n + 2.0 if template else half_width
    res = cable.berm_check(asbuilt, design, route, kp_from, kp_to, pre=pre, half_width=hw, tol=tol,
                           cell=cell if template or not hasattr(design, "cell") else None, step=step, bin_m=bin_m,
                           min_cover=min_cover, check_every=every, check_len=2 * hw,
                           progress=sub(0.4 if template else 0.0, 1.0))
    res["design_dem"] = design_dem
    return res


class CableMixin:
    def _init_cable_state(self):
        self.cable_dialog = None
        self._cable_result = None
        self._cable_ctx = {}
        self._cable_batch = None

    # ---------------------------------------------------------------- dialog
    def open_cable_dialog(self, tab=0):
        from ui.cable_dialog import CableDialog
        if self.cable_dialog is None:
            self.cable_dialog = CableDialog(self, self)
        self._cable_context()
        self.cable_dialog.tabs.setCurrentIndex(tab)
        self.cable_dialog.show()
        self.cable_dialog.raise_()
        self.cable_dialog.activateWindow()

    def _cable_context(self):
        if getattr(self, "cable_dialog", None) is None:
            return
        P = self.project
        routes = [(d.id, d.name, d.obj.z is not None, d.obj.kp_range()) for d in P.of_kind("route")]
        surfs = [(d.id, d.name) for d in P.datasets if d.kind in ("grid", "tin")
                 and getattr(d.obj, "cvals", None) is None]
        clouds = [(d.id, d.name) for d in P.points]
        self.cable_dialog.set_context(routes, surfs, clouds, self.depth_mode)

    # ---------------------------------------------------------------- run
    def _busy(self, dialog):
        t = getattr(self, "_thread", None)
        if t is not None and t.isRunning():
            dialog.show_error("Another task is still running — try again when it has finished")
            return True
        return False

    def cable_run(self, kind, sp):
        from core import cable
        if self._busy(self.cable_dialog):
            return
        P = self.project
        ids = [sp.get(k) for k in ("cable", "seabed", "route", "pc", "asbuilt", "pre", "design")]
        if any(i is not None and P.get(i) is None for i in ids):
            self._cable_context()
            return self.cable_dialog.show_error("A dataset was removed — check the choices and run again")
        kw = {}
        if kind == "dob":
            r = P.get(sp["cable"])
            fn = cable.dob_analysis
            kw = dict(route=r.obj, seabed=P.get(sp["seabed"]).obj, step=sp["step"], target=sp["target"],
                      bin_m=sp["bin_m"], width=sp["width"], kp_range=sp["kp_range"])
            label = "Depth of burial…"
        elif kind == "span":
            if sp["method"] == "profile":
                r = P.get(sp["cable"])
                fn = cable.free_span_profile
                kw = dict(route=r.obj, seabed=P.get(sp["seabed"]).obj)
            else:
                r = P.get(sp["route"])
                fn = cable.free_span_points
                kw = dict(route=r.obj, pc=P.get(sp["pc"]).obj, search=sp["search"], side=sp["side"])
            kw.update(diameter=sp["diameter"], gap_min=sp["gap_min"], len_min=sp["len_min"], step=sp["step"],
                      bin_m=sp["bin_m"], kp_range=sp["kp_range"])
            label = "Free spans…"
        else:
            r = P.get(sp["route"])
            pre = P.get(sp["pre"]).obj if sp["pre"] is not None else None
            design = P.get(sp["design"]).obj if (not sp["template"] and sp["design"] is not None) else None
            fn = _berm_job
            kw = dict(asbuilt=P.get(sp["asbuilt"]).obj, route=r.obj, kp_from=sp["kp_from"], kp_to=sp["kp_to"],
                      pre=pre, template=sp["template"], design=design, cell=sp["cell"],
                      crest_width=sp["crest_width"], height=sp["height"], slope_n=sp["slope_n"], end_n=sp["end_n"],
                      half_width=sp["half_width"], tol=sp["tol"], step=sp["step"], bin_m=sp["bin_m"],
                      min_cover=sp["min_cover"], every=sp["every"])
            label = "Rock berm check…"
        self._cable_ctx = {"kind": kind, "spec": sp, "route_id": r.id, "route": r.name}
        self._cable_batch = None                       # a single run replaces an earlier batch
        self.cable_dialog.clear_batch()
        self._run_task(label, fn, (), kw, self._on_cable_done, report_progress=True)

    # ---------------------------------------------------------------- batch (several cables)
    def cable_run_batch(self, kind, sp):
        """Depth of burial or free span for every ticked cable; results kept per cable."""
        from core import cable
        if self._busy(self.cable_dialog):
            return
        P = self.project
        items = []
        for rid in sp["batch"]:
            d = P.get(rid)
            if d is not None:
                items.append((d.name, rid, d.obj))
        need_z = kind == "dob" or (kind == "span" and sp["method"] == "profile")
        skipped = [n for n, _i, r in items if need_z and r.z is None]
        items = [it for it in items if not (need_z and it[2].z is None)]
        if not items:
            return self.cable_dialog.show_error("None of the ticked cables has a cable-top column")
        seabed = P.get(sp["seabed"]).obj if sp.get("seabed") is not None else None
        pc = P.get(sp["pc"]).obj if sp.get("pc") is not None else None
        seen = {}

        def job(progress=None):
            out = {"kind": kind, "spec": sp, "items": [], "results": {}, "errors": [f"{n}: no cable-top Z"
                                                                                  for n in skipped]}
            for k, (name, rid, route) in enumerate(items):
                n_ = seen.get(name, 0)
                seen[name] = n_ + 1
                iid = name if n_ == 0 else f"{name} ({n_ + 1})"
                sub = (lambda f, k=k: progress((k + f) / len(items))) if progress else None
                try:
                    if kind == "dob":
                        res = cable.dob_analysis(route, seabed, step=sp["step"], target=sp["target"],
                                                 bin_m=sp["bin_m"], width=sp["width"], progress=sub)
                    elif sp["method"] == "profile":
                        res = cable.free_span_profile(route, seabed, diameter=sp["diameter"], gap_min=sp["gap_min"],
                                                      len_min=sp["len_min"], step=sp["step"], bin_m=sp["bin_m"],
                                                      progress=sub)
                    else:
                        res = cable.free_span_points(route, pc, search=sp["search"], side=sp["side"],
                                                     diameter=sp["diameter"], gap_min=sp["gap_min"],
                                                     len_min=sp["len_min"], step=sp["step"], bin_m=sp["bin_m"],
                                                     progress=sub)
                except Exception as e:  # noqa: BLE001 — one cable must not stop the batch
                    out["errors"].append(f"{iid}: {type(e).__name__}: {e}")
                    continue
                row = {"cable": iid}
                row.update({a: (round(b, 3) if isinstance(b, float) else b) for a, b in res["summary"].items()
                            if not isinstance(b, (list, dict, tuple))})
                out["items"].append({"item": iid, "route_id": rid, "row": row})
                out["results"][iid] = res
            return out

        self._cable_ctx = {"kind": kind, "spec": sp, "route_id": None, "route": "batch"}
        self._run_task(f"{'Depth of burial' if kind == 'dob' else 'Free spans'} — {len(items)} cables…", job, (), {},
                       self._on_cable_batch_done, report_progress=True)

    @Slot(object)
    def _on_cable_batch_done(self, out):
        self._close_progress()
        self._cable_batch = out
        self.cable_dialog.show_batch(out, self.depth_mode)
        if out["items"]:
            self.cable_open_batch_item(out["items"][0]["item"], switch_table=False)
        self.statusBar().showMessage(f"Batch: {len(out['items'])} cable(s), {len(out['errors'])} not analysed",
                                     10000)

    def cable_open_batch_item(self, iid, switch_table=True):
        b = getattr(self, "_cable_batch", None)
        if not b or iid not in b["results"]:
            return
        res = b["results"][iid]
        it = next(i for i in b["items"] if i["item"] == iid)
        self._cable_ctx = {"kind": b["kind"], "spec": b["spec"], "route_id": it["route_id"], "route": iid}
        self._cable_result = res
        d = self.cable_dialog
        d.show_result(res, self.depth_mode)
        if switch_table:
            j = d.cmb_table.findData("segments")
            if j >= 0:
                d.cmb_table.setCurrentIndex(j)
        d.btn_batch.setEnabled(True)
        self._cable_overlay()

    def cable_export_batch(self):
        """Folder: <kind>_batch.xlsx (Summary, Segments, KP bins with a cable column; CSV files without openpyxl)
        and one chart per cable."""
        import re
        from PySide6.QtWidgets import QApplication, QProgressDialog
        from ui.cable_dialog import cable_panels
        from ui.multiyear_ui import render_figure
        b = getattr(self, "_cable_batch", None)
        if not b or not b["items"]:
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder for the batch results")
        if not folder:
            return
        k = b["kind"]
        tag = {"dob": "depth_of_burial", "span": "free_span"}[k]
        hide = {"i0", "i1"}

        def clean(rows, iid):
            return [{"cable": iid, **{a: v for a, v in r.items() if a not in hide}} for r in rows]

        sheets = {"Summary": [i["row"] for i in b["items"]],
                  "Segments": [r for i in b["items"] for r in clean(b["results"][i["item"]]["segments"], i["item"])],
                  "KP bins": [r for i in b["items"] for r in clean(b["results"][i["item"]]["bins"], i["item"])]}
        if b["errors"]:
            sheets["Not analysed"] = [{"cable / reason": e} for e in b["errors"]]
        sheets["Settings"] = [{"item": a, "value": str(v)} for a, v in b["spec"].items() if a != "batch"]
        written = []
        try:
            import openpyxl
            from openpyxl.styles import Font
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            for name, rows in sheets.items():
                ws = wb.create_sheet(name[:31])
                cols = list(dict.fromkeys(c for r in rows for c in r)) or ["note"]
                ws.append(cols)
                for c in ws[1]:
                    c.font = Font(bold=True)
                for r in rows:
                    ws.append([(None if isinstance(r.get(c), float) and not np.isfinite(r.get(c)) else r.get(c))
                               if not isinstance(r.get(c), (list, tuple, dict)) else str(r.get(c)) for c in cols])
            path = os.path.join(folder, f"{tag}_batch.xlsx")
            wb.save(path)
            written.append(path)
        except ImportError:
            for name, rows in sheets.items():
                path = os.path.join(folder, f"{tag}_batch_{name.replace(' ', '_').lower()}.csv")
                cols = list(dict.fromkeys(c for r in rows for c in r)) or ["note"]
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    f.write(",".join(cols) + "\n")
                    for r in rows:
                        f.write(",".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + "\n")
                written.append(path)
        safe = lambda t: re.sub(r'[\\/:*?"<>|]+', "_", str(t)).strip()      # noqa: E731
        dlg = QProgressDialog("Writing charts…", "Cancel", 0, len(b["items"]), self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        title = {"dob": "Depth of burial", "span": "Free span"}[k]
        for n, it in enumerate(b["items"]):
            if dlg.wasCanceled():
                break
            dlg.setValue(n)
            QApplication.processEvents()
            res = b["results"][it["item"]]
            path = os.path.join(folder, f"{safe(it['item'])}_{tag}.png")
            render_figure(path, f"{it['item']} — {title}", res["st"]["kp"], cable_panels(res, self.depth_mode),
                          None, 3)
            written.append(path)
        dlg.setValue(len(b["items"]))
        self.statusBar().showMessage(f"Batch results: {len(written)} file(s) in {folder}", 10000)

    @Slot(object)
    def _on_cable_done(self, res):
        self._close_progress()
        self._cable_result = res
        self.cable_dialog.show_result(res, self.depth_mode)
        self._cable_overlay()
        s = res["summary"]
        msg = {"dob": lambda: f"DoB: {s['non_compliant']:,.1f} m below target in {s['segments']} segment(s)",
               "span": lambda: f"Free spans: {s['spans']} span(s) ≥ reporting length, {s['short_spans']} short",
               "berm": lambda: f"Rock berm: coverage {s['coverage_pct']:.1f}%, under-filled {s['under_volume']:,.2f} m³"}
        self.statusBar().showMessage(msg[res["kind"]](), 10000)

    # ---------------------------------------------------------------- 3D overlay
    def _cable_overlay(self):
        res = self._cable_result
        if res is None:
            self.viewer.set_analysis_overlay()
            return
        from core import cable
        st, se = res["st"], res["series"]
        xy = np.column_stack([st["x"], st["y"]])
        lines, labels = [], []

        def add_runs(mask, color, z, pts=xy):
            for i0, i1 in cable.runs(mask):
                j1 = min(i1 + 1, len(pts) - 1)
                lines.append({"xy": pts[i0:j1 + 1], "z": None if z is None else z[i0:j1 + 1], "color": color})

        if res["kind"] == "dob":
            dob, t = se["dob"], res["params"]["target"]
            top = se["cable_top"]
            add_runs(np.isfinite(dob) & (dob >= t), RGBA["ok"], top)
            add_runs(np.isfinite(dob) & (dob < t), RGBA["fail"], top)
            add_runs(~np.isfinite(dob), RGBA["nodata"], None)
            for g in res["segments"]:
                labels.append((g["x_from"], g["y_from"], top[g["i0"]],
                               f"DoB {g.get('dob_min', np.nan):.2f} m · {g['length']:.0f} m"))
        elif res["kind"] == "span":
            pts = res.get("crest_xy", xy)
            top = se["cable_top"]
            for g in res["segments"]:
                col = RGBA["fail"] if g["flag"] == "SPAN" else RGBA["short"]
                i0, i1 = g["i0"], min(g["i1"] + 1, len(pts) - 1)
                lines.append({"xy": pts[i0:i1 + 1], "z": top[i0:i1 + 1], "color": col})
                if g["flag"] == "SPAN":
                    labels.append((pts[i0][0], pts[i0][1], top[i0],
                                   f"Span {g['length']:.1f} m · gap {g.get('gap_max', np.nan):.2f} m"))
        else:
            az = se["asbuilt"]
            err = se["crest_error"]
            tol = res["params"]["tol"]
            add_runs(np.isfinite(err) & (err >= -tol), RGBA["ok"], az)
            add_runs(np.isfinite(err) & (err < -tol), RGBA["fail"], az)
            for g in res["segments"]:
                labels.append((g["x_from"], g["y_from"], az[g["i0"]],
                               f"under-filled {g.get('error_min', np.nan):+.2f} m · {g['length']:.0f} m"))
            for g in res.get("cover_segments", []):
                i0, i1 = g["i0"], min(g["i1"] + 1, len(xy) - 1)
                lines.append({"xy": xy[i0:i1 + 1], "z": az[i0:i1 + 1], "color": RGBA["cover"]})
                labels.append((g["x_from"], g["y_from"], az[g["i0"]], f"cover {g.get('cover_min', np.nan):.2f} m"))
        if len(labels) > 60:                                   # keep the view readable
            labels = labels[:60] + [(st["x"][-1], st["y"][-1], None, f"… {len(labels) - 60} more (see table)")]
        self.viewer.set_analysis_overlay(lines, labels)

    def cable_clear_overlay(self):
        self.viewer.set_analysis_overlay()

    def cable_goto(self, kp_from, kp_to):
        if kp_from is None:
            return
        rd = self.project.get(self._cable_ctx.get("route_id"))
        if rd is None:
            return
        r = rd.obj
        k = (kp_from + (kp_to if kp_to is not None else kp_from)) / 2.0
        c = r.chainage_at_kp(k)
        x, y = r.xy_at(np.array([c]))[0]
        z = np.interp(c, r.cum, r.z) if r.z is not None else None
        span = abs((kp_to or kp_from) - kp_from) * 1000.0
        self.viewer.look_at(x, y, z, extent=max(span * 2.5, 30.0))

    # ---------------------------------------------------------------- exports
    def _cable_base(self, caption, default, filt):
        res = self._cable_result
        if res is None:
            return None, None
        path, _ = QFileDialog.getSaveFileName(self, caption, default, filt)
        return res, path or None

    def _cable_write_csv(self, res, base):
        """summary / segments / KP bins / stations CSV of one result; returns the file names."""
        from core import crs as crsmod
        k = res["kind"]

        def cell(v):
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                return ""
            return f"{v:.4f}" if isinstance(v, float) else str(v)

        def write(fname, rows):
            cols = [c for c in dict.fromkeys(c for r in rows for c in r) if c not in ("i0", "i1")]
            with open(fname, "w", encoding="utf-8", newline="\n") as f:
                f.write(",".join(cols) + "\n")
                for r in rows:
                    f.write(",".join(cell(r.get(c)) for c in cols) + "\n")

        segs = list(res["segments"])
        if k == "berm":
            segs = [dict(s, type="under-filled crest") for s in segs] + \
                   [dict(s, type="cover below minimum") for s in res.get("cover_segments", [])]
        write(base + "_segments.csv", segs or [{"note": "no segments"}])
        write(base + "_kp_bins.csv", res["bins"] or [{"note": "no data"}])
        st = res["st"]
        cols = {"kp": st["kp"], "chainage_m": st["ch"], "x": st["x"], "y": st["y"]}
        cols.update({n: v for n, v in res["series"].items() if v is not None})
        names = list(cols)
        with open(base + "_stations.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write(",".join(names) + "\n")
            for i in range(len(st["ch"])):
                f.write(",".join("" if not np.isfinite(cols[n][i]) else f"{cols[n][i]:.4f}" for n in names) + "\n")
        meta = [("analysis", {"dob": "depth of burial", "span": "free span", "berm": "rock berm as-built"}[k]),
                ("route", res["route"]), ("coordinate_system", crsmod.label(self.project.crs)),
                ("z_convention", "elevation, positive up; DoB = seabed - cable top; gap = cable top - diameter - "
                                 "seabed; berm error = as-built - design")]
        meta += [(f"param_{a}", b) for a, b in res.get("params", {}).items()]
        meta += [(a, b) for a, b in res["summary"].items()]
        with open(base + "_summary.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write("item,value\n")
            for a, b in meta:
                v = f"{b:.4f}" if isinstance(b, float) else str(b)
                f.write(f"{a},\"{v}\"\n")
        return [base + s for s in ("_summary.csv", "_segments.csv", "_kp_bins.csv", "_stations.csv")]

    def _cable_batch_items(self):
        """[(cable, result)] of the batch shown in the dialog, else None."""
        b = getattr(self, "_cable_batch", None)
        if not b or not b.get("items"):
            return None
        return [(i["item"], b["results"][i["item"]]) for i in b["items"] if i["item"] in b["results"]]

    def cable_export_csv(self):
        """One cable: summary / segments / KP bins / stations CSV. Batch: the same for every cable, in a folder."""
        import re
        items = self._cable_batch_items()
        if items:
            folder = QFileDialog.getExistingDirectory(self, "Folder for the CSV files of every cable")
            if not folder:
                return
            safe = lambda t: re.sub(r'[\\/:*?"<>|]+', "_", str(t)).strip()      # noqa: E731
            for item, res in items:
                self._cable_write_csv(res, os.path.join(folder, f"{safe(item)}_{res['kind']}"))
            self.statusBar().showMessage(f"CSV files of {len(items)} cable(s) saved in {folder}", 10000)
            return
        if self._cable_result is None:
            return
        res, path = self._cable_base("Export analysis (CSV)", f"cable_{self._cable_result['kind']}.csv", "CSV (*.csv)")
        if not path:
            return
        files = self._cable_write_csv(res, os.path.splitext(path)[0])
        self.statusBar().showMessage("Saved " + ", ".join(os.path.basename(f) for f in files), 10000)

    @staticmethod
    def _cable_gis_feats(res, name=None):
        st, se = res["st"], res["series"]
        pts = res.get("crest_xy", np.column_stack([st["x"], st["y"]]))
        zser = se.get("cable_top") if res["kind"] != "berm" else se.get("asbuilt")
        if res["kind"] == "dob":
            segs = [("DoB below target", g, g.get("dob_min")) for g in res["segments"]]
        elif res["kind"] == "span":
            segs = [("free span" if g["flag"] == "SPAN" else "short span", g, g.get("gap_max")) for g in res["segments"]]
        else:
            segs = [("under-filled crest", g, g.get("error_min")) for g in res["segments"]]
            segs += [("cover below minimum", g, g.get("cover_min")) for g in res.get("cover_segments", [])]
        feats = []
        for typ, g, val in segs:
            i0 = g["i0"]
            i1 = min(max(g["i1"], i0 + 1), len(pts) - 1)
            if i1 <= i0:
                continue
            z = zser[i0:i1 + 1] if zser is not None else np.zeros(i1 - i0 + 1)
            feats.append({"type": "LineString",
                          "parts": [np.column_stack([pts[i0:i1 + 1], np.nan_to_num(z)])],
                          "attrs": {"TYPE": typ, "KP_FROM": g["kp_from"], "KP_TO": g["kp_to"], "LENGTH_M": g["length"],
                                    "VALUE_M": float(val) if val is not None else None,
                                    "ROUTE": str(name or res["route"])}})
        return feats

    def cable_export_shp(self):
        """Stretches as lines (TYPE, KP, length, value, ROUTE) — one cable, or every cable of a batch in one file."""
        from fileio import vector_io
        items = self._cable_batch_items()
        if not items:
            if self._cable_result is None:
                return
            items = [(self._cable_result["route"], self._cable_result)]
        k = items[0][1]["kind"]
        path, flt = QFileDialog.getSaveFileName(self, "Export segments (GIS)",
                                                f"cable_{k}{'_batch' if len(items) > 1 else ''}.gpkg",
                                                vector_io.WRITE_FILTER)
        if not path:
            return
        path = vector_io.ext_from_filter(path, flt)
        feats = [f for name, res in items for f in self._cable_gis_feats(res, name)]
        if not feats:
            self.statusBar().showMessage("No segments to export", 6000)
            return
        try:
            vector_io.write(path, feats, self.project.crs, layer=f"cable_{k}",
                            fields=[("TYPE", "C"), ("KP_FROM", "N"), ("KP_TO", "N"), ("LENGTH_M", "N"),
                                    ("VALUE_M", "N"), ("ROUTE", "C")])
        except Exception as e:  # noqa: BLE001
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Export failed", f"{type(e).__name__}: {e}")
            return
        self.statusBar().showMessage(f"Saved {os.path.basename(path)} ({len(feats)} segment(s) of {len(items)} "
                                     f"cable(s))", 8000)

    def cable_save_chart(self):
        items = self._cable_batch_items()
        if items:                                     # batch: one chart per cable, in a folder
            import re
            from ui.cable_dialog import cable_panels
            from ui.multiyear_ui import render_figure
            folder = QFileDialog.getExistingDirectory(self, "Folder for the chart of every cable")
            if not folder:
                return
            safe = lambda t: re.sub(r'[\\/:*?"<>|]+', "_", str(t)).strip()      # noqa: E731
            title = {"dob": "Depth of burial", "span": "Free span", "berm": "Rock berm"}
            for item, res in items:
                render_figure(os.path.join(folder, f"{safe(item)}_{res['kind']}.png"),
                              f"{item} — {title[res['kind']]}", res["st"]["kp"], cable_panels(res, self.depth_mode),
                              None, 3)
            self.statusBar().showMessage(f"Charts of {len(items)} cable(s) saved in {folder}", 10000)
            return
        if self.cable_dialog is None or self._cable_result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", f"cable_{self._cable_result['kind']}.png",
                                              "PNG (*.png)")
        if path:
            ch = self.cable_dialog.chart
            pm = ch.grab()
            pm = pm.scaled(pm.width() * 2, pm.height() * 2) if pm.width() < 900 else pm
            pm.save(path)
            self.statusBar().showMessage(f"Saved {os.path.basename(path)}", 6000)

    # ---------------------------------------------------------------- berm extras
    def cable_add_layers(self):
        from core.project import Dataset
        from core.surface import Dem
        res = self._cable_result
        if res is None or res["kind"] != "berm":
            return
        g = res["grid"]
        E = g["error"]
        d = E[np.isfinite(E)]
        m = max(abs(np.percentile(d, 2)), abs(np.percentile(d, 98)), 1e-3) if len(d) else 1.0
        name = self._cable_ctx.get("route", "berm")
        added = []
        if res.get("design_dem") is not None:
            ds = Dataset(self.project.unique_name(f"Berm design · {name}"), "grid", res["design_dem"],
                         crs=self.project.crs, source_crs=self.project.crs)
            self.project.add(ds)
            added.append("design surface")
        dem = Dem(np.where(np.isfinite(E), g["asbuilt"], np.nan).astype(np.float32), g["x0"], g["ytop"], g["cell"],
                  "difference", cvals=E, crange=(-m, m), meta={"title": "as-built − design"})
        self.project.add(Dataset(self.project.unique_name(f"Berm as-built − design · {name}"), "grid", dem,
                                 crs=self.project.crs, source_crs=self.project.crs))
        added.append("as-built − design")
        self._project_changed()
        self._set_dirty(True)
        self.statusBar().showMessage(f"Added: {', '.join(added)} (blue = under-filled, red = over-filled)", 8000)

    def cable_add_sections(self):
        from core import profile as prof
        from core.route import dist_label
        res = self._cable_result
        if res is None or not res.get("sections"):
            return
        first = len(self.sections)
        for s in res["sections"]:
            self.sections.append(prof.Section(f"Berm {dist_label()} {s['kp']:.3f}", np.asarray(s["xy"], float),
                                              width=0.5))
        self.active_section = first
        self._set_dirty(True)
        self.profile_dock.show()
        self._recompute_section(fit=True)
        if hasattr(self, "_refresh_drawings"):
            self._refresh_drawings()
        self.statusBar().showMessage(f"Added {len(res['sections'])} check section(s) — choose them in the profile "
                                     "panel", 8000)
