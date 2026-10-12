"""Project / multi-dataset behaviour of the main window (mixed into MainWindow)."""
from __future__ import annotations

import os
import time

import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox

from core import crs as crsmod
from core.history import History
from core.pointcloud import PointCloud
from core.project import Dataset, Project
from core.surface import DEM_METHODS, build_dem, build_tin, mean_spacing
from fileio.grid_reader import is_esri_ascii, read_raster, read_xyz_grid, reproject_dem
from fileio.las_reader import read_las
from fileio.surface_io import DEM_FORMATS, TIN_FORMATS, export_dem, export_tin
from fileio.xyz_reader import read_xyz
from render.colormap import MODE_LABELS, available_modes
from ui.crs_dialog import CrsDialog
from ui.import_dialog import XyzImportDialog
from ui.surface_dialogs import DemDialog, TinDialog

LAS_EXT = (".las", ".laz")
RASTER_EXT = (".tif", ".tiff", ".grd")
TEXT_EXT = (".xyz", ".asc", ".txt", ".csv", ".pts")
ALL_FILTER = ("All supported (*.pcsproj *.las *.laz *.xyz *.asc *.txt *.csv *.pts *.tif *.tiff *.grd "
              "*.shp *.gpkg *.geojson *.kml *.kmz *.gpx *.dxf);;"
              "PointCloud Studio project (*.pcsproj);;"
              "Point clouds (*.las *.laz *.xyz *.asc *.txt *.csv *.pts);;"
              "DEM grids (*.tif *.tiff *.asc *.grd *.xyz);;"
              "Vector layers (*.shp *.gpkg *.geojson *.json *.kml *.kmz *.gpx *.dxf);;All files (*)")


class ProjectMixin:
    # ================================================================ state helpers
    def _init_project_state(self):
        self.project = Project()
        self._bound_id = None
        self._n_points_seen = 0
        self._load_queue = []
        self._pending = None
        self._surface_src = None
        self.project_path = None

    @property
    def tin(self):
        """TIN used by sections / DEM creation: visible TIN of the edited cloud, else any visible TIN."""
        return self._pick_surface("tin")

    @property
    def dem(self):
        return self._pick_surface("grid")

    def _pick_surface(self, kind):
        cands = self.project.of_kind(kind, visible_only=True)
        if not cands:
            return None
        pref = [d for d in cands if d.derived_from == self._bound_id]
        return (pref or cands)[-1].obj

    def _bound_ds(self):
        return self.project.get(self._bound_id) if self._bound_id is not None else None

    def _outdated_ids(self):
        out = []
        for d in self.project.datasets:
            if d.derived_from is not None:
                src = self.project.get(d.derived_from)
                if src is not None and src.edit_version != d.edit_version:
                    out.append(d.id)
        return out

    def _refresh_data_card(self):
        self.data_card.refresh(self.project, self._outdated_ids())
        self._refresh_drawings()

    # ---------- drawings (sections, polygon, subsets) ----------
    def _drawings(self):
        out = []
        for i, sec in enumerate(self.sections):
            kp = f" · KP {sec.kp.min():.3f}–{sec.kp.max():.3f}" if sec.kp is not None else ""
            out.append(dict(key=("section", i), name=sec.name, visible=sec.visible, color="#ff9f43",
                            info=f"section · {sec.length:,.1f} m · ±{sec.width:g} m{kp}"
                                 + ("  ·  active" if i == self.active_section else ""),
                            actions=[("Show profile", lambda i=i: self.drawing_activate(("section", i))),
                                     ("Delete section", lambda i=i: self.drawing_remove(("section", i)))]))
        if self.volume_polygon is not None:
            P = np.asarray(self.volume_polygon, float)
            area = 0.5 * abs(np.dot(P[:, 0], np.roll(P[:, 1], 1)) - np.dot(P[:, 1], np.roll(P[:, 0], 1)))
            out.append(dict(key=("polygon",), name="Polygon (volume / dredge area)", visible=self.region_visible,
                            color="#ffd166", info=f"{len(P)} vertices · {area:,.1f} m²",
                            actions=[("Remove polygon", lambda: self.drawing_remove(("polygon",)))]))
        if self.subset is not None:
            out.append(dict(key=("subset",), name="Subset box", visible=self.subset_visible, color="#ff9f43",
                            info=f"{self.subset.length:,.1f} × {self.subset.width:g} m",
                            actions=[("Close subset", self.subset_close)]))
        if self.subset_done:
            out.append(dict(key=("reviewed",), name="Reviewed subsets", visible=self.reviewed_visible,
                            color="#3ecf8e", info=f"{len(self.subset_done)} area(s)",
                            actions=[("Clear reviewed areas", lambda: self.drawing_remove(("reviewed",)))]))
        return out

    def _refresh_drawings(self):
        if hasattr(self, "data_card"):
            self.data_card.refresh_drawings(self._drawings())

    def drawing_visible(self, key, on):
        on = bool(on)
        if key[0] == "section" and key[1] < len(self.sections):
            self.sections[key[1]].visible = on
            self._overlay_sections()
        elif key[0] == "polygon":
            self.region_visible = on
            self._show_region()
        elif key[0] == "subset":
            self.subset_visible = on
            self._subset_overlay()
        elif key[0] == "reviewed":
            self.reviewed_visible = on
            self._subset_overlay()
        self._set_dirty(True)

    def drawing_activate(self, key):
        if key[0] == "section" and key[1] < len(self.sections):
            self.active_section = key[1]
            self.sections[key[1]].visible = True
            self._show_dock(self.profile_dock)
            self._recompute_section(fit=True)
            self._fit_profile_later()
            self._refresh_drawings()

    def drawing_remove(self, key):
        if key[0] == "section" and key[1] < len(self.sections):
            self.active_section = key[1]
            self.delete_section()
        elif key[0] == "polygon":
            self.volume_polygon = None
            self._show_region()
            self._volume_context()
        elif key[0] == "reviewed":
            self.subset_done = []
            self._subset_cov = 0.0
            self._subset_overlay()
            self.refresh_subset()
        self._set_dirty(True)
        self._refresh_drawings()

    def _show_region(self):
        self.viewer.set_region(self.volume_polygon if (self.volume_polygon is not None and self.region_visible) else None)

    # ================================================================ open / add
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open (new project)", "", ALL_FILTER)
        if path:
            self.load_path(path, add=False)

    def add_data(self):
        if not self.project.datasets:
            return self.open_file()
        paths, _ = QFileDialog.getOpenFileNames(self, "Add data to project", "", ALL_FILTER)
        self._load_queue = list(paths)
        self._next_in_queue()

    def _next_in_queue(self):
        t = getattr(self, "_thread", None)
        if self._load_queue and t is not None and t.isRunning():
            # the previous load's thread is still shutting down (its result arrives before it has
            # finished) — starting now would be refused and the file silently dropped: retry shortly
            from PySide6.QtCore import QTimer
            QTimer.singleShot(30, self._next_in_queue)
            return
        if self._load_queue:
            self.load_path(self._load_queue.pop(0), add=True)
        elif getattr(self, "_batch_xyz", None) is not None or getattr(self, "_batch_crs", None) is not None:
            self._batch_xyz = self._batch_crs = None      # batch import finished
            if hasattr(self, "_multiyear_context"):
                self._multiyear_context()

    def load_path(self, path, add=False):
        from fileio.vector_io import is_vector
        if is_vector(path):                       # GIS / CAD layers are added to the current project
            self.import_vector_layer(path)
            return self._next_in_queue()
        if not add and not self._confirm_discard("start a new project"):
            return
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pcsproj":
            return self.open_project(path)
        name = os.path.basename(path)
        opts = {}
        if ext in LAS_EXT:
            fn, args, kw = read_las, (path,), {}
        elif ext in RASTER_EXT or (ext == ".asc" and is_esri_ascii(path)):
            fn, args, kw = read_raster, (path,), {}
        elif ext in TEXT_EXT or ext == "":
            batch = getattr(self, "_batch_xyz", None)
            if batch is not None and batch.get("opts") is not None:
                opts = dict(batch["opts"])           # batch import: same column mapping as the first file
            else:
                dlg = XyzImportDialog(path, self)
                if dlg.exec() != QDialog.Accepted:
                    return self._next_in_queue()
                opts = dlg.settings()
                if batch is not None:
                    batch["opts"] = dict(opts)
            as_grid = opts.pop("as_grid", False)
            fn, args, kw = (read_xyz_grid if as_grid else read_xyz), (path,), dict(opts)
            opts["as_grid"] = as_grid
        else:
            QMessageBox.warning(self, "Unsupported format", f"Unrecognized file extension: {ext}")
            return self._next_in_queue()
        self._pending = dict(path=path, add=add, opts=opts)
        self._run_task(f"Loading {name}…", fn, args, kw, self._on_loaded, report_progress=False)

    @Slot(object)
    def _on_loaded(self, obj):
        self._close_progress()
        try:
            self._ingest(obj)
        finally:
            self._next_in_queue()

    def _ingest(self, obj):
        p = self._pending
        load_time = time.perf_counter() - self._t0
        add = p["add"] and bool(self.project.datasets)
        if not add:
            self._new_project()
        proj = self.project
        kind = "points" if isinstance(obj, PointCloud) else "grid"
        name = proj.unique_name(os.path.basename(p["path"]))
        if kind == "points":
            src = crsmod.las_crs(obj.source["las"]) if obj.source.get("las") is not None else None
        else:
            src = obj.meta.get("crs")

        if src is None and getattr(self, "_batch_crs", None) is not None:
            src = self._batch_crs                    # batch import: one coordinate system for all files
        if src is None:
            ok, src = CrsDialog.ask(
                self, f"Coordinate system — {name}",
                "This file does not state its coordinate system. Choose the EPSG code of the data"
                + (" (the project uses " + crsmod.label(proj.crs) + ")." if proj.crs is not None else "."),
                initial=proj.crs, allow_unknown=True, warn_geographic=not proj.datasets)
            if not ok:
                self.statusBar().showMessage(f"{name} not added", 6000)
                self._project_changed(fit=False)
                return
        if not proj.datasets:
            proj.crs = src
            if src is not None and crsmod.is_geographic(src):
                ok, pcrs = CrsDialog.ask(
                    self, "Project coordinate system",
                    "The data is in latitude / longitude. Choose a projected system for the project "
                    "(metres are needed for distances, grids and volumes); the data will be reprojected.",
                    allow_unknown=False, warn_geographic=True)
                if ok and pcrs is not None:
                    proj.crs = pcrs

        target = proj.crs
        note = ""
        if src is not None and target is not None and crsmod.same(src, target) is False:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                if kind == "points":
                    x, y = crsmod.transform_xy(obj.xyz[:, 0], obj.xyz[:, 1], src, target)
                    obj.xyz[:, 0], obj.xyz[:, 1] = x, y
                    obj.recompute_bbox()
                    obj.source["reprojected_from"] = crsmod.label(src)
                else:
                    obj = reproject_dem(obj, src, target)
            finally:
                QApplication.restoreOverrideCursor()
            note = f" · reprojected {crsmod.label(src)} → EPSG:{crsmod.epsg_of(target)}"
        elif src is not None and target is None and proj.datasets:
            note = " · project has no coordinate system: no reprojection applied"

        ds = Dataset(name, kind, obj, p["path"], crs=target if target is not None else src, source_crs=src,
                     import_opts=p["opts"])
        first = not proj.datasets
        tag = getattr(self, "_year_tags", {}).pop(p["path"], None)
        if tag is not None:                          # batch import of yearly surfaces
            ds.survey_date, ds.simulated = tag
        proj.add(ds)
        if first:
            self.depth_mode = bool(ds.bbox()[1][2] <= 0)
        self._project_changed(fit=first)
        if not first:
            self._set_dirty(True)
        if kind == "points" and first:
            self.file_card.set_rows(self._info_rows(obj, load_time))
        msg = f"Loaded {name} in {load_time:.2f} s{note}"
        if getattr(self, "_lock_msg", ""):
            msg += "  ·  " + self._lock_msg
        self.statusBar().showMessage(msg, 12000)

    def _new_project(self):
        for key in list(self.viewer.surfaces):
            self.viewer.set_surface(key, None)
        self.project = Project()
        self._bound_id = None
        self._n_points_seen = 0
        self.pc = None
        self.sel = None
        self.trash = None
        self.history = History()
        self.sections = []
        self.active_section = -1
        self.profile_dock.hide()
        self._cable_result = None
        self._seabed_result = None
        self._land_result = None
        self._multiyear_result = None
        self.viewer.set_analysis_overlay()
        self.subset = None
        self.subset_route = None
        self.subset_done = []
        self.volume_polygon = None
        self.region_visible = self.subset_visible = self.reviewed_visible = True
        self._subset_cov = 0.0
        self.subset_dock.hide()
        self.viewer.set_subset_overlay()
        self.viewer.set_region(None)
        self.viewer.set_routes([])
        for cid in list(getattr(self.viewer, "contours", {})):
            self.viewer.set_contours(cid, None)
        self.viewer.set_origin(None)
        self.viewer.set_pointcloud(None, reset=True)
        self._sel_methods = []
        self.project_path = None
        self._set_dirty(False)

    # ================================================================ binding / lock
    def _project_changed(self, fit=False):
        proj = self.project
        n_pts = len(proj.points)
        lock_msg = ""
        if n_pts != self._n_points_seen:            # apply the automatic rule only when the count changes
            before = proj.edit_id
            proj.apply_lock_rule()
            if n_pts >= 2 and before is not None and proj.edit_id is None:
                lock_msg = "Editing locked: the project now has several point clouds — click ✎ Edit on one to edit it"
            self._n_points_seen = n_pts
        self.viewer.set_origin(proj.origin, proj.bbox())
        target = proj.edit_target
        if (target.id if target else None) != self._bound_id:
            self._bind(target)
        v = self.viewer
        for d in proj.points:
            if target is not None and d.id == target.id:
                v.remove_reference(d.id)
                continue
            if d.id not in v.refs:
                v.add_reference(d.id, d.obj)
            v.set_reference_visible(d.id, d.visible)
        for rid in list(v.refs):
            if proj.get(rid) is None or (target is not None and rid == target.id):
                v.remove_reference(rid)
        surf_ids = {d.id for d in proj.datasets if d.kind in ("grid", "tin")}
        for d in proj.datasets:
            if d.kind in ("grid", "tin"):
                if d.id not in v.surfaces or v.surfaces[d.id].V is None:
                    v.set_surface(d.id, d.obj)
                    if d.opacity < 1:
                        v.set_layer_opacity(d.id, d.opacity)
                v.set_layer_visible(d.id, d.visible)
        for key in list(v.surfaces):
            if isinstance(key, int) and key not in surf_ids:
                v.set_surface(key, None)
        if target is not None:
            v.set_layer_visible("points", target.visible)
        self._show_routes()
        self._show_contours()
        if fit:
            v.fit_view()
        v.recolor_surfaces()
        self.stack.setCurrentIndex(1 if proj.datasets else 0)
        self._set_enabled(bool(proj.datasets))
        self._refresh_data_card()
        self.data_card.setVisible(bool(proj.datasets))
        self._apply_edit_lock()
        self._update_chips()
        self._update_surface_actions()
        self._set_dirty(self._dirty)
        self._recompute_section()
        self._refresh_subset_if_open()
        self._volume_context()
        v._notify()
        self._lock_msg = lock_msg
        if lock_msg:
            self.statusBar().showMessage(lock_msg, 10000)

    def _bind(self, ds):
        """Make `ds` the edited (primary) cloud, or unbind when ds is None."""
        if self.selector.drawing:
            self.selector.cancel()
        self._bound_id = ds.id if ds is not None else None
        if ds is not None:
            self.pc, self.sel, self.trash, self.history = ds.obj, ds.sel, ds.trash, ds.history
            self.viewer.remove_reference(ds.id)
        else:
            self.pc, self.sel, self.trash, self.history = None, None, None, History()
        self.viewer.set_pointcloud(self.pc, reset=False)
        if hasattr(self, "subset_panel"):
            self.subset_panel.chk_noise.blockSignals(True)
            self.subset_panel.chk_noise.setChecked(self.pc is not None and not self.pc.hide_noise)
            self.subset_panel.chk_noise.blockSignals(False)
        self.cmb_color.blockSignals(True)
        cur = self.cmb_color.currentData()
        self.cmb_color.clear()
        modes = available_modes(self.pc) if self.pc is not None else ["elevation"]
        for m in modes:
            self.cmb_color.addItem(MODE_LABELS[m], m)
        mode = cur if cur in modes else ("rgb" if "rgb" in modes else "elevation")
        self.cmb_color.setCurrentIndex(self.cmb_color.findData(mode))
        self.cmb_color.blockSignals(False)
        self.viewer.set_color_mode(mode)
        self._sel_methods = []
        if self.pc is not None:
            self.trash_card.refresh(self.trash)
            self._update_trash_view()
            self._selection_changed()
            self.file_card.set_rows(self._info_rows(self.pc, 0.0)[:-1])
        else:
            self.sel_card.update_info(0, [])
            self.file_card.set_rows(None)

    def _apply_edit_lock(self):
        editable = self.pc is not None
        for k in ("pick", "box", "lasso", "subset"):
            self.mode_actions[k].setEnabled(editable)
        if not editable and self.selector.mode in ("pick", "box", "lasso", "subset"):
            self.set_mode("navigate")
        for a in (self.act_sel_all, self.act_invert, self.act_clear, self.act_filter, self.act_classify):
            a.setEnabled(editable)
        self.trash_card.setVisible(editable)
        if not editable:
            self.sel_card.setVisible(False)
        has_pts = bool(self.project.points)
        self.act_export.setEnabled(has_pts)
        self.act_tin.setEnabled(has_pts)
        self.act_dem.setEnabled(has_pts)
        self.act_add.setEnabled(bool(self.project.datasets))
        self._update_edit_actions()

    # ================================================================ data panel callbacks
    def ds_visible(self, ds_id, on):
        ds = self.project.get(ds_id)
        if ds is None:
            return
        ds.visible = bool(on)
        if ds.kind in ("route", "structure", "vector"):
            self._show_routes()
            return
        if ds.kind == "contour":
            self.viewer.set_contours_visible(ds_id, on)
            return
        self.viewer.set_layer_visible("points" if ds_id == self._bound_id else ds_id, on)
        self.viewer.recolor_surfaces()
        self._recompute_section()

    def ds_opacity(self, ds_id, a):
        ds = self.project.get(ds_id)
        if ds is not None:
            ds.opacity = float(a)
            self.viewer.set_layer_opacity(ds_id, a)

    def ds_edit(self, ds_id, on):
        proj = self.project
        if on:
            proj.edit_id = ds_id
        elif proj.edit_id == ds_id:
            proj.edit_id = None
        self._project_changed()
        name = proj.get(ds_id).name if proj.get(ds_id) else ""
        self.statusBar().showMessage(f"Editing {name}" if on else "Editing locked", 6000)

    def ds_flip_z(self, ds_id):
        """The file stored depths (positive down): flip the sign so everything is elevation (Z up)."""
        from core.pointcloud import PointCloud
        ds = self.project.get(ds_id)
        if ds is None or ds.kind not in ("points", "grid"):
            return
        o = ds.obj
        if isinstance(o, PointCloud):
            o.xyz[:, 2] *= -1.0
            o.recompute_bbox()
            if ds_id == self._bound_id:
                self.viewer.refresh()
            else:
                self.viewer.remove_reference(ds_id)
        else:
            o.z = -o.z
            self.viewer.set_surface(ds_id, None)
        ds.z_flipped = not ds.z_flipped
        if isinstance(o, PointCloud):
            o.source["z_flipped"] = ds.z_flipped           # export default: write Z back as depth
        ds.edit_version += 1                               # surfaces / contours built from it are now out of date
        for sc in (self.viewer.scales.get("elevation"),):
            if sc is not None:
                sc.vmin = sc.vmax = sc.amin = sc.amax = None
                sc.auto = True
        lo, hi = ds.bbox()                                  # follow the flipped data: depth display if all ≤ 0
        self.scale_card.cmb_axis.setCurrentIndex(1 if hi[2] <= 0 else 0)
        self._project_changed()
        self._set_dirty(True)
        self.statusBar().showMessage(f"{ds.name}: Z sign flipped — values are now "
                                     f"{'elevations from depths' if ds.z_flipped else 'as in the file'}", 8000)

    def ds_zoom(self, ds_id):
        ds = self.project.get(ds_id)
        if ds is None:
            return
        keep = self.viewer.bbox
        lo, hi = ds.bbox()
        if not (np.isfinite(lo[2]) and np.isfinite(hi[2])):          # routes, structures: no Z
            lo, hi = lo.copy(), hi.copy()
            lo[2], hi[2] = (keep[0][2], keep[1][2]) if keep is not None else (0.0, 0.0)
        self.viewer.bbox = (lo, hi)
        self.viewer.fit_view()
        self.viewer.bbox = keep

    def ds_wire(self, ds_id, on):
        self.viewer.set_wireframe(ds_id, on)

    def ds_export(self, ds_id):
        ds = self.project.get(ds_id)
        if ds is None:
            return
        if ds.kind == "points":
            self.export_file(ds)
        elif ds.kind == "grid":
            self.export_dem(ds)
        elif ds.kind == "contour":
            self.export_contours(ds)
        else:
            self.export_tin(ds)

    def ds_remove(self, ds_id):
        ds = self.project.get(ds_id)
        if ds is None:
            return
        if ds.kind == "points" and ds.trash is not None and ds.trash.total:
            r = QMessageBox.question(self, "Remove from project",
                                     f"{ds.name} has {ds.trash.total:,} deleted points that were not exported. "
                                     "Remove it anyway?")
            if r != QMessageBox.Yes:
                return
        if ds_id == self._bound_id:
            self.project.edit_id = None
        self.project.remove(ds_id)
        self._set_dirty(True)
        if not self.project.datasets:
            self._new_project()
            self._project_changed(fit=True)
            return
        self._project_changed()

    def set_hillshade(self, on):
        self.viewer.set_hillshade(on)

    # ================================================================ TIN / DEM as project datasets
    def _pick_source_points(self, title):
        if self._bound_ds() is not None:
            return self._bound_ds()
        pts = self.project.points
        if not pts:
            QMessageBox.information(self, title, "The project has no point cloud.")
            return None
        if len(pts) == 1:
            return pts[0]
        name, ok = QInputDialog.getItem(self, title, "Source point cloud:", [d.name for d in pts], 0, False)
        return next((d for d in pts if d.name == name), None) if ok else None

    def _source_idx(self, src, source):
        db = src.obj.deleted_batch
        return np.flatnonzero(src.sel.mask if source == "selected" else db == 0)

    def _counts_of(self, src):
        sel = src.sel.count if src.id == self._bound_id else 0
        out = dict(active=src.obj.n_active, selected=sel)
        pc = src.obj
        if pc.has("Classification"):
            vals, cnt = np.unique(pc.attrs["Classification"][pc.active_indices()], return_counts=True)
            if len(vals) > 1:
                out["classes"] = {int(v): int(c) for v, c in zip(vals, cnt)}
        return out

    def _class_filter(self, src, sidx, dlg):
        """Apply the dialog's class filter; returns (indices, label suffix) or (None, msg) on error."""
        try:
            cls = dlg.classes()
        except ValueError:
            return None, "Classes must be numbers separated by commas"
        if not cls:
            return sidx, ""
        keep = np.isin(src.obj.attrs["Classification"][sidx], cls)
        sidx = sidx[keep]
        if len(sidx) < 3:
            return None, f"No points of class {', '.join(map(str, cls))} in the source"
        return sidx, f" (class {', '.join(map(str, cls))})"

    def _counts(self):
        ds = self._bound_ds()
        return self._counts_of(ds) if ds is not None else dict(active=0, selected=0)

    def _update_points_info(self):
        self._refresh_data_card()

    def _update_surface_actions(self):
        self.act_exp_tin.setEnabled(bool(self.project.of_kind("tin")))
        self.act_exp_dem.setEnabled(bool(self.project.of_kind("grid")))

    def create_tin(self):
        src = self._pick_source_points("Create TIN")
        if src is None:
            return
        pc = src.obj
        idx = pc.active_indices()
        ext = pc.bbox_max - pc.bbox_min
        dlg = TinDialog(self._counts_of(src), mean_spacing(pc.xyz, idx), max(ext[0] * ext[1], 1e-6), self)
        if dlg.exec() != QDialog.Accepted:
            return
        prm = dlg.params()
        sidx, suffix = self._class_filter(src, self._source_idx(src, prm.pop("source")), dlg)
        if sidx is None:
            QMessageBox.warning(self, "Create TIN", suffix)
            return
        self._surface_src = src
        self._surface_suffix = suffix
        self._run_task("Building TIN…", build_tin, (pc.xyz, sidx), prm, self._on_tin_built, report_progress=True)

    @Slot(object)
    def _on_tin_built(self, tin):
        self._close_progress()
        src = self._surface_src
        dt = time.perf_counter() - self._t0
        ds = Dataset(self.project.unique_name(f"TIN · {src.name}{getattr(self, '_surface_suffix', '')}"), "tin", tin,
                     crs=self.project.crs,
                     source_crs=self.project.crs, derived_from=src.id, edit_version=src.edit_version)
        self.project.add(ds)
        self._project_changed()
        self._set_dirty(True)
        self.statusBar().showMessage(f"TIN built in {dt:.1f} s ({len(tin.triangles):,} triangles)", 8000)

    def create_dem(self):
        src = self._pick_source_points("Create DEM")
        if src is None:
            return
        pc = src.obj
        idx = pc.active_indices()
        tins = [d for d in self.project.of_kind("tin") if d.derived_from == src.id]
        tin_ds = tins[-1] if tins else None
        tin_state = None if tin_ds is None else ("ok" if tin_ds.edit_version == src.edit_version else "outdated")
        lo, hi = pc.bbox_min, pc.bbox_max
        dlg = DemDialog(self._counts_of(src), mean_spacing(pc.xyz, idx), (lo[0], lo[1], hi[0], hi[1]), tin_state,
                        self, zrange=(float(lo[2]), float(hi[2])))
        if dlg.exec() != QDialog.Accepted:
            return
        prm = dlg.params()
        sidx, suffix = self._class_filter(src, self._source_idx(src, prm.pop("source")), dlg)
        if sidx is None:
            QMessageBox.warning(self, "Create DEM", suffix)
            return
        self._surface_suffix = suffix
        tin = tin_ds.obj if prm.pop("use_tin") and tin_ds is not None else None
        self._surface_src = src
        self._run_task("Building DEM…", build_dem, (pc.xyz, sidx), dict(prm, tin=tin), self._on_dem_built,
                       report_progress=True)

    @Slot(object)
    def _on_dem_built(self, dem):
        self._close_progress()
        src = self._surface_src
        dt = time.perf_counter() - self._t0
        label = "Shoal DEM" if dem.has_true_position else "DEM"
        ds = Dataset(self.project.unique_name(f"{label} {dem.cell:g} m · {src.name}{getattr(self, '_surface_suffix', '')}"),
                     "grid", dem,
                     crs=self.project.crs, source_crs=self.project.crs, derived_from=src.id,
                     edit_version=src.edit_version)
        self.project.add(ds)
        self._project_changed()
        self._set_dirty(True)
        nr, nc = dem.shape
        info = f"{nc:,} × {nr:,} cells · {DEM_METHODS[dem.method]}"
        if dem.has_true_position:
            info += f" · {dem.meta['soundings']:,} shoal soundings"
        self.statusBar().showMessage(f"DEM built in {dt:.1f} s ({info})", 8000)

    # ================================================================ surface export
    def _surface_crs(self):
        if self.project.crs is not None:
            return self.project.crs, True
        ok, c = CrsDialog.ask(self, "Coordinate system for the GeoTIFF",
                              "The project has no coordinate system. Choose one to embed, or leave unknown.")
        return c, ok

    def export_tin(self, ds=None):
        ds = ds or (self.project.of_kind("tin")[-1] if self.project.of_kind("tin") else None)
        if ds is None:
            return
        path = self._save_surface_path("Export TIN", TIN_FORMATS, "_tin")
        if path:
            self._exporting = True
            self._run_task(f"Exporting {os.path.basename(path)}…", export_tin, (ds.obj, path), {},
                           lambda _: self._on_surface_exported(path), report_progress=True)

    def export_dem(self, ds=None):
        ds = ds or (self.project.of_kind("grid")[-1] if self.project.of_kind("grid") else None)
        if ds is None:
            return
        path = self._save_surface_path("Export DEM", DEM_FORMATS, "_dem")
        if not path:
            return
        crs = None
        if path.lower().endswith((".tif", ".tiff")):
            crs, ok = self._surface_crs()
            if not ok:
                return
        self._exporting = True
        self._run_task(f"Exporting {os.path.basename(path)}…", export_dem, (ds.obj, path),
                       dict(crs=crs), lambda _: self._on_surface_exported(path), report_progress=True)


# ==================================================================== project files (.pcsproj)
def _load_project_sources(doc, project_path, sources, progress=None):
    """Worker thread: read every dataset (originals + generated) and reproject. → [(entry, obj, error)]"""
    from core import project_io
    out = []
    n = max(len(doc["datasets"]), 1)
    for i, e in enumerate(doc["datasets"]):
        try:
            if e["kind"] == "vector":
                from core.vector_layer import VectorLayer
                out.append((e, VectorLayer(e["vector"]), None))
                if progress:
                    progress((i + 1) / n)
                continue
            if e["kind"] == "structure":
                from core.structures import Structures
                sd = e["structures"]
                out.append((e, Structures(sd["names"], np.asarray(sd["xy"], float), sd["diameter"]), None))
                if progress:
                    progress((i + 1) / n)
                continue
            if e["kind"] == "route":
                from core.route import Route
                rd = e["route"]
                def _arr(v):
                    return None if v is None else np.array([np.nan if a is None else a for a in v], float)
                obj = Route(rd.get("name", e["name"]), np.asarray(rd["xy"], float),
                            None if rd.get("kp") is None else np.asarray(rd["kp"], float), rd.get("start_kp", 0.0),
                            z=_arr(rd.get("z")), dob=_arr(rd.get("dob")),
                            meta={"attributes": rd.get("attributes") or {}})
                out.append((e, obj, None))
                if progress:
                    progress((i + 1) / n)
                continue
            src_path = sources.get(e["id"])
            if src_path is None and "data" in e and e["kind"] != "points":
                obj = project_io.load_generated(e, project_path)
            elif src_path is None:
                raise FileNotFoundError(e.get("path") or e["name"])
            else:
                opts = dict(e.get("import_opts") or {})
                as_grid = opts.pop("as_grid", False)
                ext = os.path.splitext(src_path)[1].lower()
                if e["kind"] == "points":
                    obj = read_las(src_path) if ext in LAS_EXT else read_xyz(src_path, **opts)
                elif as_grid:
                    obj = read_xyz_grid(src_path, **opts)
                else:
                    obj = read_raster(src_path)
                if e.get("z_flipped"):                       # file stores depths: flip as when it was saved
                    if e["kind"] == "points":
                        obj.xyz[:, 2] *= -1.0
                        obj.recompute_bbox()
                    else:
                        obj.z = -obj.z
                s, t = crsmod.from_text(e.get("source_crs")), crsmod.from_text(e.get("crs"))
                if s is not None and t is not None and crsmod.same(s, t) is False:
                    if e["kind"] == "points":
                        x, y = crsmod.transform_xy(obj.xyz[:, 0], obj.xyz[:, 1], s, t)
                        obj.xyz[:, 0], obj.xyz[:, 1] = x, y
                        obj.recompute_bbox()
                        obj.source["reprojected_from"] = crsmod.label(s)
                    else:
                        obj = reproject_dem(obj, s, t)
            out.append((e, obj, None))
        except Exception as ex:  # noqa: BLE001
            out.append((e, None, f"{e['name']}: {type(ex).__name__}: {ex}"))
        if progress:
            progress((i + 1) / n)
    return out


class ProjectFileMixin:
    def _subset_state(self):
        sr = None
        if self.subset_route:
            ds = self.project.get(self.subset_route["id"])
            sr = {"route_name": ds.name, "ch": self.subset_route["ch"]} if ds is not None else None
        return {"current": self.subset.to_dict() if self.subset is not None else None, "route": sr,
                "polygon": None if self.volume_polygon is None else np.asarray(self.volume_polygon).tolist(),
                "visible": {"polygon": self.region_visible, "subset": self.subset_visible,
                            "reviewed": self.reviewed_visible},
                "length": self.subset_panel.sp_length.value(),
                "done": [d.to_dict() for d in self.subset_done], "reject_mode": self.reject_mode, "reject_class": self.reject_class,
                "view": self.subset_view_mode}

    def _apply_subset_state(self, st):
        from core.subset import Subset
        if not st:
            return
        rm = st.get("reject_mode", "class")
        self.reject_mode = "class" if rm in ("class", "class7") else "trash"
        self.reject_class = int(st.get("reject_class", 7))
        vis = st.get("visible", {})
        self.region_visible = vis.get("polygon", True)
        self.subset_visible = vis.get("subset", True)
        self.reviewed_visible = vis.get("reviewed", True)
        if st.get("polygon"):
            self.volume_polygon = np.asarray(st["polygon"], float)
            self._show_region()
        self.subset_view_mode = st.get("view", "along")
        self.subset_done = [Subset.from_dict(d) for d in st.get("done", [])]
        cur = st.get("current")
        self.subset = Subset.from_dict(cur) if cur else None
        self.subset_panel.sp_length.blockSignals(True)
        self.subset_panel.sp_length.setValue(float(st.get("length", 100.0)))
        self.subset_panel.sp_length.blockSignals(False)
        sr = st.get("route")
        rds = next((d for d in self.project.of_kind("route") if sr and d.name == sr.get("route_name")), None)
        self.subset_route = {"id": rds.id, "ch": float(sr["ch"])} if rds is not None else None
        if self.subset_done:
            self._update_coverage()
        if self.subset is not None:
            self.subset_panel.sp_width.blockSignals(True)
            self.subset_panel.sp_width.setValue(self.subset.width)
            self.subset_panel.sp_width.blockSignals(False)
            self._show_dock(self.subset_dock)
            self.refresh_subset(fit=True)
        else:
            self._subset_overlay()

    def _view_state(self):
        v = self.viewer
        cam = v.camera
        return {
            "color_mode": v.color_mode, "depth": self.depth_mode, "z_scale": self.spin_z.value(),
            "point_size": self.sld_size.value(), "show_bar": self.scale_card.chk_bar.isChecked(),
            "hillshade": self.data_card.chk_shade.isChecked(),
            "scales": {m: {k: getattr(sc, k) for k in ("palette", "reverse", "amin", "amax", "vmin", "vmax", "auto",
                                                        "out_of_range")} for m, sc in v.scales.items()},
            "camera": {"center": [float(c) for c in cam.center], "elevation": float(cam.elevation),
                       "azimuth": float(cam.azimuth), "scale_factor": float(cam.scale_factor)},
        }

    def _apply_view_state(self, st):
        if not st:
            return
        v = self.viewer
        self.depth_mode = bool(st.get("depth", self.depth_mode))
        for m, d in (st.get("scales") or {}).items():
            if m in v.scales:
                for k, val in d.items():
                    setattr(v.scales[m], k, val)
        self.sld_size.setValue(int(st.get("point_size", self.sld_size.value())))
        self.spin_z.setValue(float(st.get("z_scale", 1.0)))
        self.scale_card.chk_bar.setChecked(bool(st.get("show_bar", True)))
        self.data_card.chk_shade.setChecked(bool(st.get("hillshade", True)))
        mode = st.get("color_mode")
        i = self.cmb_color.findData(mode)
        if i >= 0:
            self.cmb_color.setCurrentIndex(i)
        v.recolor()
        v.recolor_surfaces()
        cam = st.get("camera")
        if cam:
            v.camera.center = tuple(cam["center"])
            v.camera.elevation = cam["elevation"]
            v.camera.azimuth = cam["azimuth"]
            v.camera.scale_factor = cam["scale_factor"]
        v._notify()
        v.canvas.update()

    # ---------- save ----------
    def save_project(self):
        if not self.project.datasets:
            return False
        if not self.project_path:
            return self.save_project_as()
        return self._write_project(self.project_path)

    def save_project_as(self):
        if not self.project.datasets:
            return False
        start = self.project_path or (os.path.splitext(self._project_path_stem("project"))[0] + ".pcsproj")
        path, _ = QFileDialog.getSaveFileName(self, "Save project", start, "PointCloud Studio project (*.pcsproj)")
        if not path:
            return False
        if not path.lower().endswith(".pcsproj"):
            path += ".pcsproj"
        return self._write_project(path)

    def _write_project(self, path):
        from core import project_io
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            project_io.save_project(path, self.project, self.sections, self._view_state(), self._subset_state())
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Save failed", f"{type(e).__name__}: {e}")
            return False
        QApplication.restoreOverrideCursor()
        self.project_path = path
        self._set_dirty(False)
        self.statusBar().showMessage(f"Project saved: {path}", 8000)
        return True

    # ---------- open ----------
    def open_project(self, path):
        from core import project_io
        try:
            doc = project_io.read_project(path)
        except Exception as e:  # noqa: BLE001
            from core.deps import module_from_error
            mod = module_from_error(e)
            if mod:
                from ui.deps_dialog import missing_module_box
                missing_module_box(mod, self)
            else:
                QMessageBox.critical(self, "Cannot open project", f"{type(e).__name__}: {e}")
            return
        sources, skipped = {}, []
        for e in doc["datasets"]:
            if e["kind"] in ("route", "structure", "vector") or (e.get("path") is None and "data" in e):
                continue                                  # stored in the project file itself
            if e["kind"] != "points" and e.get("derived_from") is not None and "data" in e:
                continue
            p = project_io.resolve_source(e, path)
            if p is None:
                r = QMessageBox.question(self, "File not found",
                                         f"{e['name']} was not found at\n{e.get('path')}\n\nLocate it?",
                                         QMessageBox.Yes | QMessageBox.No)
                if r == QMessageBox.Yes:
                    p, _ = QFileDialog.getOpenFileName(self, f"Locate {e['name']}", "", ALL_FILTER)
            if p:
                sources[e["id"]] = p
            else:
                skipped.append(e["name"])
        self._open_doc = (doc, path, skipped)
        self._run_task(f"Opening project {os.path.basename(path)}…", _load_project_sources, (doc, path, sources),
                       {}, self._on_project_loaded, report_progress=True)

    @Slot(object)
    def _on_project_loaded(self, results):
        from core import profile as prof
        from core import project_io
        self._close_progress()
        doc, path, skipped = self._open_doc
        self._new_project()
        proj = self.project
        proj.crs = crsmod.from_text(doc.get("crs"))
        proj.origin = np.array(doc["origin"]) if doc.get("origin") else None
        warnings = [f"{n}: skipped (file not found)" for n in skipped]
        idmap, pending_links = {}, []
        for e, obj, err in results:
            if err:
                if not any(e["name"] in w for w in warnings):
                    warnings.append(err)
                continue
            src = project_io.resolve_source(e, path) if e.get("path") else None
            ds = Dataset(e["name"], e["kind"], obj, src, crs=crsmod.from_text(e.get("crs")),
                         source_crs=crsmod.from_text(e.get("source_crs")), visible=e.get("visible", True),
                         opacity=e.get("opacity", 1.0), import_opts=e.get("import_opts") or {},
                         edit_version=e.get("edit_version", 0), z_flipped=bool(e.get("z_flipped", False)),
                         survey_date=e.get("survey_date"), simulated=bool(e.get("simulated", False)))
            proj.add(ds)
            idmap[e["id"]] = ds.id
            if e.get("derived_from") is not None:
                pending_links.append((ds, e["derived_from"]))
            if e["kind"] == "points":
                w = project_io.apply_edits(ds, e, path)
                if w:
                    warnings.append(w)
        for ds, old in pending_links:
            ds.derived_from = idmap.get(old)
        proj.edit_id = idmap.get(doc.get("edit_id"))
        self._n_points_seen = len(proj.points)            # keep the saved edit target (no auto rule)
        if proj.datasets:
            self.depth_mode = bool(proj.bbox()[1][2] <= 0)
        self._project_changed(fit=True)
        for s in doc.get("sections", []):
            self.sections.append(prof.Section(s["name"], np.asarray(s["xy"], float), s["width"], s["start"],
                                              kp=None if s.get("kp") is None else np.asarray(s["kp"], float),
                                              visible=s.get("visible", True)))
        self.active_section = len(self.sections) - 1
        if self.sections:
            self._show_dock(self.profile_dock)
            self._recompute_section(fit=True)
            self._fit_profile_later()
        self._apply_view_state(doc.get("view"))
        self._apply_subset_state(doc.get("subsets"))
        self._overlay_sections()
        self._refresh_drawings()
        self.project_path = path
        self._set_dirty(False)
        msg = f"Opened project {os.path.basename(path)} ({len(proj.datasets)} datasets)"
        self.statusBar().showMessage(msg, 8000)
        if warnings:
            QMessageBox.warning(self, "Project opened with warnings", "\n".join(warnings))
