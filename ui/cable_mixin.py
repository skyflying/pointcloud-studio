"""Cable & Foundations menu: depth of burial, free spans, rock berm as-built (core/cable.py)."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Slot
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
        self._run_task(label, fn, (), kw, self._on_cable_done, report_progress=True)

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

    def cable_export_csv(self):
        from core import crs as crsmod
        if self._cable_result is None:
            return
        res, path = self._cable_base("Export analysis (CSV)", f"cable_{self._cable_result['kind']}.csv", "CSV (*.csv)")
        if not path:
            return
        base = os.path.splitext(path)[0]
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
        files = [base + s for s in ("_summary.csv", "_segments.csv", "_kp_bins.csv", "_stations.csv")]
        self.statusBar().showMessage("Saved " + ", ".join(os.path.basename(f) for f in files), 10000)

    def cable_export_shp(self):
        import shapefile
        if self._cable_result is None:
            return
        res, path = self._cable_base("Export segments (Shapefile)", f"cable_{self._cable_result['kind']}.shp",
                                     "Shapefile (*.shp)")
        if not path:
            return
        st, se = res["st"], res["series"]
        pts = res.get("crest_xy", np.column_stack([st["x"], st["y"]]))
        zser = se.get("cable_top") if res["kind"] != "berm" else se.get("asbuilt")
        base = os.path.splitext(path)[0]
        w = shapefile.Writer(base, shapeType=shapefile.POLYLINEZ)
        for name, typ, size, dec in (("TYPE", "C", 24, 0), ("KP_FROM", "N", 14, 4), ("KP_TO", "N", 14, 4),
                                     ("LENGTH_M", "N", 14, 2), ("VALUE_M", "N", 14, 3), ("ROUTE", "C", 60, 0)):
            w.field(name, typ, size, dec)
        segs = []
        if res["kind"] == "dob":
            segs = [("DoB below target", g, g.get("dob_min")) for g in res["segments"]]
        elif res["kind"] == "span":
            segs = [("free span" if g["flag"] == "SPAN" else "short span", g, g.get("gap_max")) for g in res["segments"]]
        else:
            segs = [("under-filled crest", g, g.get("error_min")) for g in res["segments"]]
            segs += [("cover below minimum", g, g.get("cover_min")) for g in res.get("cover_segments", [])]
        n = 0
        for typ, g, val in segs:
            i0, i1 = g["i0"], max(g["i1"], g["i0"] + 1)
            i1 = min(i1, len(pts) - 1)
            if i1 <= i0:
                continue
            z = zser[i0:i1 + 1] if zser is not None else np.zeros(i1 - i0 + 1)
            w.linez([[(float(x), float(y), float(zz) if np.isfinite(zz) else 0.0)
                      for (x, y), zz in zip(pts[i0:i1 + 1], z)]])
            w.record(typ, g["kp_from"], g["kp_to"], g["length"], float(val) if val is not None else None,
                     res["route"][:60])
            n += 1
        w.close()
        if self.project.crs is not None:
            with open(base + ".prj", "w", encoding="ascii", errors="ignore") as f:
                f.write(self.project.crs.to_wkt("WKT1_ESRI"))
        self.statusBar().showMessage(f"Saved {os.path.basename(base)}.shp ({n} segment(s))", 8000)

    def cable_save_chart(self):
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
