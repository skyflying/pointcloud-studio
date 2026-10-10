"""Subset editor + reject / accept behaviour of the main window (mixed into MainWindow)."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDockWidget

from core import profile as prof
from core.subset import Subset, coverage
from core.trash import ClassifyCommand
from render.colormap import SCALAR_MODES, compute_colors, map_scalar, scalar_values
from ui.subset_panel import SubsetPanel


class SubsetMixin:
    def _show_dock(self, dock):
        dock.show()
        dock.raise_()               # bring its tab to the front

    def _init_subset_state(self):
        self.subset = None
        self.subset_done = []
        self.reject_mode = "class"        # "class" (LAS: flag with reject_class) or "trash"
        self.reject_class = 7
        self.subset_route = None
        self.subset_view_mode = "along"
        self._subset_cov = 0.0

    def _build_subset_dock(self):
        self.subset_panel = SubsetPanel(self)
        self.subset_dock = QDockWidget("Subset editor", self)
        self.subset_dock.setObjectName("subsetDock")
        self.subset_dock.setWidget(self.subset_panel)
        self.subset_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.subset_dock)
        # share the bottom area with the profile as TABS (side by side they would force a very wide window)
        self.tabifyDockWidget(self.profile_dock, self.subset_dock)
        self.resizeDocks([self.subset_dock], [340], Qt.Vertical)
        self.subset_dock.hide()
        self.subset_dock.visibilityChanged.connect(lambda vis: vis and self.refresh_subset())
        act = self.subset_dock.toggleViewAction()
        act.setText("Subset Editor Panel")
        self._m_view.addAction(act)

    # ---------------------------------------------------------------- drawing / overlay
    def _subset_overlay(self, draft=None):
        cur = self.subset.corners() if (self.subset is not None and getattr(self, "subset_visible", True)) else None
        done = [d.corners() for d in self.subset_done] if getattr(self, "reviewed_visible", True) else []
        self.viewer.set_subset_overlay(cur, draft, done)
        if hasattr(self, "_refresh_drawings"):
            self._refresh_drawings()

    def _on_subset_draft(self, p0, p1):
        draft = None
        if p0 is not None and p1 is not None:
            draft = Subset(p0, p1, self.subset_panel.sp_width.value()).corners()
        self._subset_overlay(draft)

    def _on_subset_finish(self, p0, p1):
        self.subset_route = None
        self.subset_panel.sp_length.blockSignals(True)
        self.subset_panel.sp_length.setValue(round(float(np.hypot(*(np.asarray(p1) - np.asarray(p0)))), 1))
        self.subset_panel.sp_length.blockSignals(False)
        self.subset = Subset(p0, p1, self.subset_panel.sp_width.value(), self.subset_panel.sp_overlap.value() / 100)
        self._show_dock(self.subset_dock)
        self._set_dirty(True)
        self.refresh_subset(fit=True)

    # ---------------------------------------------------------------- display
    def refresh_subset(self, fit=False):
        panel = self.subset_panel
        self._subset_overlay()
        if self.subset is None or not self.subset_dock.isVisible():
            return
        pc = self.pc
        if pc is None:
            panel.show_points(np.empty(0), np.empty(0), np.empty(0, np.int64), np.empty((0, 4), np.float32),
                              None, None, self.depth_mode, False, "")
            panel.set_status("Editing is locked — choose ✎ Edit on a point cloud in the Data panel to edit a subset")
            return
        sb = self.subset
        idx, a, c = sb.select(pc.xyz, pc.active_indices())
        along = self.subset_view_mode == "along"
        x = a if along else c
        z = pc.xyz[idx, 2]
        mode = self.viewer.color_mode
        if mode in SCALAR_MODES and self.viewer.scales[mode].vmin is not None:
            cols = map_scalar(scalar_values(pc, idx, mode), self.viewer.scales[mode])
        else:
            cols = compute_colors(pc, idx, mode)
        if pc.can_classify and not pc.hide_noise:
            cols[pc.noise_mask()[idx]] = self.viewer.NOISE_RGBA
        # reference surface: TIN / DEM cut along the centre line (side view) or across it (end view)
        if along:
            line = prof.Section("ref", np.array([sb.p0, sb.p1]))
            shift = 0.0
        else:
            h = sb.n * sb.width / 2
            line = prof.Section("ref", np.array([sb.center - h, sb.center + h]))
            shift = -sb.width / 2
        ref = {}
        if self.tin is not None:
            ch, zz = prof.tin_section(self.tin, line)
            ref["tin"] = (ch + shift, zz)
        if self.dem is not None:
            ch, zz = prof.dem_section(self.dem, line)
            ref["dem"] = (ch + shift, zz)
        sel = self.sel.mask[idx] if self.sel is not None else None
        panel.set_reject_options(pc.can_classify, self.reject_mode, self.reject_class)
        panel.show_points(x, z, idx, cols, sel, ref, self.depth_mode, fit,
                          "Along axis (m)" if along else "Across axis (m)  —  left of axis ← → right")
        if fit:
            QTimer.singleShot(0, lambda: (panel.plot.fit(), panel._sync_vex()))
        n_sel = int(sel.sum()) if sel is not None else 0
        noise_here = int(pc.noise_mask()[idx].sum()) if (pc.can_classify and not pc.hide_noise) else 0
        txt = (self._route_subset_label() + f"{len(idx):,} points in subset ({sb.length:,.1f} × {sb.width:g} m)  ·  {n_sel:,} selected"
               + (f"  ·  {noise_here:,} rejected shown" if noise_here else "")
               + f"  ·  reviewed {len(self.subset_done)} subset(s), {self._subset_cov:.1%} of points")
        panel.set_status(txt)

    # ---------------------------------------------------------------- panel callbacks
    def subset_page(self, direction):
        if self.subset is None:
            return
        if self.subset_route:
            step = self.subset_panel.sp_length.value() * (1 - self.subset_panel.sp_overlap.value() / 100)
            self.subset_route["ch"] += direction * step
            self._place_route_subset()
        else:
            self.subset = self.subset.paged(direction)
        self.refresh_subset(fit=True)

    def subset_length(self, L):
        if self.subset is None:
            return
        if self.subset_route:
            self._place_route_subset()
        else:
            self.subset.p1 = self.subset.p0 + self.subset.u * float(L)
        self.refresh_subset(fit=True)

    def subset_width(self, v):
        if self.subset is not None:
            self.subset.width = float(v)
            if self.subset_route:
                self._place_route_subset()
            self.refresh_subset(fit=True)

    def subset_overlap(self, frac):
        if self.subset is not None:
            self.subset.overlap = float(frac)

    def subset_view(self, view):
        self.subset_view_mode = view
        self.refresh_subset(fit=True)

    def subset_focus(self):
        if self.subset is None:
            return
        c = self.subset.corners()
        lo, hi = c.min(axis=0), c.max(axis=0)
        bb = self.project.bbox() if self.project.datasets else None
        zl, zh = (bb[0][2], bb[1][2]) if bb else (0.0, 0.0)
        keep = self.viewer.bbox
        self.viewer.bbox = (np.array([lo[0], lo[1], zl]), np.array([hi[0], hi[1], zh]))
        self.viewer.fit_view()
        self.viewer.bbox = keep

    def subset_mark_done(self):
        if self.subset is None:
            return
        self.subset_done.append(Subset(self.subset.p0.copy(), self.subset.p1.copy(), self.subset.width,
                                       self.subset.overlap))
        self._update_coverage()
        self._set_dirty(True)
        self.refresh_subset()
        self.statusBar().showMessage(f"Subset marked complete — {self._subset_cov:.1%} of points reviewed", 6000)

    def _update_coverage(self):
        pc = self.pc
        self._subset_cov = coverage(self.subset_done, pc.xyz, pc.active_indices()) if pc is not None else 0.0

    def subset_select(self, idx, op):
        if self.pc is None:
            return
        self.sel.apply(idx, op)
        self._track_method("Subset", op)
        self._selection_changed()
        self.refresh_subset()

    def subset_close(self):
        self.subset_dock.hide()
        self.subset = None
        self.subset_route = None
        self._subset_overlay()

    def set_reject_mode(self, mode, cls=None):
        """mode: 'trash' or 'class' (cls = class number; None asks for a custom class)."""
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        if mode == "class":
            if cls is None:
                top = self.pc.max_class if self.pc is not None else 31
                cls, ok = QInputDialog.getInt(self, "Reject as class", f"LAS class for rejected points (0–{top}):",
                                              self.reject_class, 0, top)
                if not ok:
                    self.subset_panel.set_reject_options(self.pc is not None and self.pc.can_classify,
                                                         self.reject_mode, self.reject_class)
                    return
            if self.pc is not None and cls > self.pc.max_class:
                QMessageBox.warning(self, "Class not possible",
                                    f"This LAS file (point format 0–5) can only store classes 0–31.\n"
                                    f"Class {cls} needs point format 6–10 (LAS 1.4).")
                self.subset_panel.set_reject_options(True, self.reject_mode, self.reject_class)
                return
            self.reject_class = int(cls)
            if self.pc is not None:
                self.pc.reject_classes.add(self.reject_class)
        self.reject_mode = mode
        self.subset_panel.set_reject_options(self.pc is not None and self.pc.can_classify, self.reject_mode,
                                             self.reject_class)
        self._update_edit_actions()

    def set_show_noise(self, on):
        pc = self.pc
        if pc is None or not pc.can_classify:
            return
        pc.hide_noise = not on
        self.sel.mask &= pc.active_mask()
        self.viewer.refresh()
        self._selection_changed()
        self._recompute_section()
        self.refresh_subset()
        self._refresh_data_card()

    # ---------------------------------------------------------------- reject / accept
    def _effective_reject_mode(self):
        return "class" if (self.pc is not None and self.pc.can_classify and self.reject_mode == "class") else "trash"

    def reject_selected(self):
        if self.selector.section_drawing:
            self.selector.remove_last()
            return
        if self.pc is None or self.sel is None or self.sel.count == 0 or self.selector.drawing:
            return
        if self._effective_reject_mode() == "trash":
            return self.delete_selected()
        idx = self.sel.indices()
        idx = idx[~self.pc.noise_mask()[idx]]
        if len(idx) == 0:
            return
        self.pc.reject_classes.add(self.reject_class)
        cmd = ClassifyCommand(self.pc, idx, self.reject_class)
        self.history.push(cmd)
        self.sel.clear()
        self._sel_methods = []
        self._after_edit(f"Rejected {len(idx):,} points → Class {self.reject_class} · Ctrl+Z to undo")

    def accept_selected(self):
        pc = self.pc
        if pc is None or not pc.can_classify or self.sel.count == 0:
            return
        idx = self.sel.indices()
        idx = idx[pc.noise_mask()[idx]]
        if len(idx) == 0:
            self.statusBar().showMessage("No rejected points in the selection", 5000)
            return
        self.history.push(ClassifyCommand(pc, idx, restore=True))
        self._after_edit(f"Accepted {len(idx):,} points (original class restored) · Ctrl+Z to undo")

    def _refresh_subset_if_open(self):
        if self.subset is not None and self.subset_dock.isVisible():
            if self.subset_done:
                self._update_coverage()
            self.refresh_subset()


class FilterMixin:
    """Auto filter: run in the background, result = selection; Cancel restores the previous selection."""

    def open_filter_dialog(self, scope=None):
        from core.surface import mean_spacing
        from ui.filter_dialog import FilterDialog
        if self.pc is None:
            self.statusBar().showMessage("Choose ✎ Edit on a point cloud first (Data panel)", 6000)
            return
        if getattr(self, "filter_dialog", None) is None:
            self.filter_dialog = FilterDialog(self, self)
        pc = self.pc
        act = pc.active_indices()
        n_sub = len(self.subset.select(pc.xyz, act)[0]) if self.subset is not None else 0
        surfaces = [(d.id, d.name) for d in self.project.datasets if d.kind in ("grid", "tin")]
        lo, hi = pc.bbox_min, pc.bbox_max
        self.filter_dialog.set_context(dict(all=len(act), selected=self.sel.count, subset=n_sub), surfaces,
                                       self.subset is not None, self.depth_mode, mean_spacing(pc.xyz, act),
                                       (float(lo[2]), float(hi[2])), scope)
        if self._filter_saved is None:
            self._filter_saved = self.sel.mask.copy()
        self.filter_dialog.show()
        self.filter_dialog.raise_()
        self.filter_dialog.activateWindow()

    def _filter_scope(self, scope):
        pc = self.pc
        if scope == "selected":
            base = self._filter_saved if self._filter_saved is not None else self.sel.mask
            return np.flatnonzero(base & pc.active_mask())
        if scope == "subset" and self.subset is not None:
            return self.subset.select(pc.xyz, pc.active_indices())[0]
        return pc.active_indices()

    def filter_run(self, method, params, scope, op):
        from core import filters
        if self.pc is None:
            return
        params = dict(params)
        surface = None
        if method == "surface":
            ds = self.project.get(params.pop("surface_id", None))
            surface = ds.obj if ds is not None else None
        idx = self._filter_scope(scope)
        self._filter_ctx = (method, op, len(idx))
        self._run_task("Running filter…", filters.run, (self.pc.xyz, idx, method, params),
                       dict(surface=surface), self._on_filter_done, report_progress=True)

    def _on_filter_done(self, result):
        from core.filters import METHODS
        self._close_progress()
        cand, info = result
        method, op, n_scope = self._filter_ctx
        base = self._filter_saved if (op == "add" and self._filter_saved is not None) else None
        mask = base.copy() if base is not None else np.zeros(len(self.pc), dtype=bool)
        mask[cand] = True
        self.sel.mask = mask & self.pc.active_mask()
        self._sel_methods = [f"Filter: {METHODS[method]}"]
        self._selection_changed()
        self._refresh_subset_if_open()
        self.filter_dialog.show_result(len(cand), n_scope, info.get("threshold", ""))

    def filter_finish(self, keep):
        if not keep and self._filter_saved is not None and self.pc is not None:
            self.sel.mask = self._filter_saved & self.pc.active_mask()
            self._sel_methods = []
            self._selection_changed()
            self._refresh_subset_if_open()
        elif keep and self.sel is not None and self.sel.count:
            self.statusBar().showMessage(f"{self.sel.count:,} filter candidates selected — review them, "
                                         "then Reject (Delete)", 10000)
        self._filter_saved = None


class RouteMixin:
    """Route import + route-based sections and subsets."""

    def import_route(self):
        from PySide6.QtWidgets import QDialog, QFileDialog
        from ui.route_dialog import RouteImportDialog
        path, _ = QFileDialog.getOpenFileName(self, "Import route", "",
                                              "Routes (*.shp *.csv *.txt *.rpl *.dat);;Shapefile (*.shp);;"
                                              "CSV / text (*.csv *.txt *.rpl *.dat);;All files (*)")
        if not path:
            return
        try:
            dlg = RouteImportDialog(path, self.project.crs, self)
        except Exception as e:  # noqa: BLE001
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Cannot read route", f"{type(e).__name__}: {e}")
            return
        if dlg.exec() != QDialog.Accepted:
            return
        self._add_routes(dlg.routes, dlg.crs, path)

    def _add_routes(self, routes, src_crs, path):
        from core import crs as crsmod
        from core.project import Dataset
        from fileio.route_reader import reproject_routes
        proj = self.project
        if not proj.datasets:
            self._new_project()
            proj = self.project
            proj.crs = src_crs
            if src_crs is not None and crsmod.is_geographic(src_crs):
                from ui.crs_dialog import CrsDialog
                ok, pcrs = CrsDialog.ask(self, "Project coordinate system",
                                         "The route is in latitude / longitude. Choose a projected system for the "
                                         "project (metres for KP, sections and volumes).", allow_unknown=False,
                                         warn_geographic=True)
                if ok and pcrs is not None:
                    proj.crs = pcrs
        note = ""
        if src_crs is not None and proj.crs is not None and crsmod.same(src_crs, proj.crs) is False:
            reproject_routes(routes, src_crs, proj.crs)
            note = f" · reprojected from {crsmod.label(src_crs)}"
        first = None
        for r in routes:
            ds = Dataset(proj.unique_name(r.name), "route", r, path, crs=proj.crs, source_crs=src_crs)
            proj.add(ds)
            first = first or ds
        self._project_changed(fit=len(proj.datasets) == len(routes))
        self._set_dirty(True)
        self.statusBar().showMessage(f"Imported {len(routes)} route(s){note}", 8000)
        return first

    def _show_routes(self):
        self.viewer.set_routes([d.obj for d in self.project.of_kind("route", visible_only=True)])

    def route_section(self, ds_id):
        from core import profile as prof
        ds = self.project.get(ds_id)
        r = ds.obj
        width = self.sections[self.active_section].width if self.active_section >= 0 else 2.0
        sec = prof.Section(r.name, r.xy.copy(), width=width, kp=r.kp_vertices.copy())
        self.sections.append(sec)
        self.active_section = len(self.sections) - 1
        self._set_dirty(True)
        self.profile_dock.show()
        self._recompute_section(fit=True)
        self._fit_profile_later()
        self.statusBar().showMessage(f"Section along {r.name}: KP {r.kp_range()[0]:.3f} – {r.kp_range()[1]:.3f}", 8000)

    def route_subset(self, ds_id, chainage=0.0):
        if self.pc is None:
            self.statusBar().showMessage("Choose ✎ Edit on a point cloud first (Data panel)", 6000)
            return
        self.subset_route = {"id": ds_id, "ch": float(chainage)}
        self._place_route_subset()
        self._show_dock(self.subset_dock)
        self._set_dirty(True)
        self.refresh_subset(fit=True)

    def _place_route_subset(self):
        from core.subset import Subset
        ds = self.project.get(self.subset_route["id"])
        if ds is None:
            self.subset_route = None
            return
        r = ds.obj
        L = self.subset_panel.sp_length.value()
        ch = min(max(self.subset_route["ch"], 0.0), max(r.length - 1e-6, 0.0))
        self.subset_route["ch"] = ch
        p0, p1 = r.xy_at(ch), r.xy_at(min(ch + L, r.length))
        if np.hypot(*(p1 - p0)) < 1e-6:
            p1 = p0 + (r.xy[-1] - r.xy[-2]) / max(np.hypot(*(r.xy[-1] - r.xy[-2])), 1e-9)
        self.subset = Subset(p0, p1, self.subset_panel.sp_width.value(), self.subset_panel.sp_overlap.value() / 100)

    def _route_subset_label(self):
        if not getattr(self, "subset_route", None):
            return ""
        ds = self.project.get(self.subset_route["id"])
        if ds is None:
            return ""
        r = ds.obj
        ch = self.subset_route["ch"]
        k0 = float(r.kp_at(ch))
        k1 = float(r.kp_at(min(ch + self.subset_panel.sp_length.value(), r.length)))
        from core.route import dist_label
        return f"{r.name}  {dist_label()} {k0:,.3f} – {k1:,.3f}  ·  "


class ClassifyMixin:
    def open_classify_dialog(self):
        from PySide6.QtWidgets import QDialog

        from core import classify as C
        from core.surface import mean_spacing
        from ui.classify_dialog import ClassifyDialog
        if self.pc is None:
            self.statusBar().showMessage("Choose ✎ Edit on a point cloud first (Data panel)", 6000)
            return
        pc = self.pc
        act = pc.active_indices()
        n_sub = len(self.subset.select(pc.xyz, act)[0]) if self.subset is not None else 0
        dlg = ClassifyDialog(dict(all=len(act), selected=self.sel.count, subset=n_sub), mean_spacing(pc.xyz, act),
                             self.subset is not None, self)
        if dlg.exec() != QDialog.Accepted:
            return
        sp = dlg.spec()
        idx = self._filter_scope(sp["scope"]) if sp["scope"] != "all" else act
        if len(idx) < 10:
            return
        pc.ensure_classification()
        existing = pc.attrs["Classification"][idx] == 2
        if sp["method"] is None and not existing.any():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Classify points", "There are no ground (class 2) points to start from.")
            return
        self._classify_ctx = (idx, sp)
        self._run_task("Classifying points…", C.classify, (pc.xyz, idx, sp["method"], sp["params"]),
                       dict(veg=sp["veg"], buildings=sp["buildings"], existing_ground=existing),
                       self._on_classified, report_progress=True)

    def _on_classified(self, result):
        from core.trash import ClassifyCommand
        from render.colormap import MODE_LABELS, available_modes
        self._close_progress()
        g, cls = result
        idx, sp = self._classify_ctx
        pc = self.pc
        cur = pc.attrs["Classification"][idx]
        change = cur != cls
        if sp["only_unclassified"]:
            change &= np.isin(cur, (0, 1))
        if not change.any():
            self.statusBar().showMessage("Classification unchanged", 6000)
            return
        self.history.push(ClassifyCommand(pc, idx[change], values=cls[change],
                                          label=f"Classify {int(change.sum()):,} points ({sp['label']})"))
        self.cmb_color.blockSignals(True)
        self.cmb_color.clear()
        for m in available_modes(pc):
            self.cmb_color.addItem(MODE_LABELS[m], m)
        self.cmb_color.setCurrentIndex(self.cmb_color.findData("classification"))
        self.cmb_color.blockSignals(False)
        self.viewer.set_color_mode("classification")
        names = {1: "unclassified", 2: "ground", 3: "low veg.", 4: "medium veg.", 5: "high veg.", 6: "building"}
        vals, cnt = np.unique(cls, return_counts=True)
        summary = " · ".join(f"{names.get(int(v), v)} {c:,}" for v, c in zip(vals, cnt))
        self._after_edit(f"{sp['label']}: {summary}  ({int(change.sum()):,} points changed · Ctrl+Z to undo)")
