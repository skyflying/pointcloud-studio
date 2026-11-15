"""HD Map menu: annotate map features on the point cloud (lines, areas, points — snapped to the points), label
points with HD map classes, extract lane markings and poles semi-automatically, build lanelets, export to
Lanelet2 / Shapefile / GeoPackage / GeoJSON."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from core import hdmap as H
from ui.theme import dark_titlebar


def _muted(t):
    lab = QLabel(t)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


def _swatch(hexc):
    pm = QPixmap(12, 12)
    pm.fill(QColor(hexc))
    return QIcon(pm)


def _dsp(v, lo, hi, step=0.1, dec=2, suffix=""):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setValue(v)
    if suffix:
        w.setSuffix(suffix)
    return w


def hd_style():
    return {"color": "#ffffff", "color_attr": "TYPE", "color_map": dict(H.COLOR_MAP), "label": None}


# ================================================================ annotation panel
class AnnotationPanel(QDockWidget):
    def __init__(self, cb, parent=None):
        super().__init__("HD map annotation", parent)
        self.cb = cb
        self.setObjectName("hdmap_panel")
        self.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable |
                         QDockWidget.DockWidgetFloatable)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(10, 8, 10, 8)
        sc.setWidget(body)
        self.setWidget(sc)
        lr = QHBoxLayout()
        self.cmb_layer = QComboBox()
        b_new = QPushButton("New")
        b_new.clicked.connect(cb.hd_new_layer)
        lr.addWidget(self.cmb_layer, 1)
        lr.addWidget(b_new)
        v.addWidget(_muted("Layer"))
        v.addLayout(lr)
        v.addWidget(_muted("Feature type"))
        self.lst_type = QListWidget()
        for k, (geom, lab, col, _l2, _subs) in H.FEATURE_TYPES.items():
            if k == "lanelet":
                continue
            it = QListWidgetItem(_swatch(col), f"{lab}  ·  {geom.replace('LineString', 'line').lower()}")
            it.setData(Qt.UserRole, k)
            self.lst_type.addItem(it)
        self.lst_type.setCurrentRow(0)
        self.lst_type.setMaximumHeight(230)
        v.addWidget(self.lst_type)
        f = QFormLayout()
        self.cmb_sub = QComboBox()
        self.cmb_col = QComboBox()
        for c in ("white", "yellow", "blue", "red", "orange"):
            self.cmb_col.addItem(c)
        self.width_ = _dsp(0.15, 0.0, 5.0, 0.05, 2, " m")
        self.height_ = _dsp(0.0, 0.0, 50.0, 0.5, 2, " m")
        self.height_.setSpecialValueText("—")
        self.lane = QLineEdit()
        self.note = QLineEdit()
        f.addRow("Subtype", self.cmb_sub)
        f.addRow("Colour", self.cmb_col)
        f.addRow("Width", self.width_)
        f.addRow("Height", self.height_)
        f.addRow("Lane ID", self.lane)
        f.addRow("Note", self.note)
        self.snap = QCheckBox("Snap to the nearest point")
        self.snap.setChecked(True)
        self.snap_px = QSpinBox()
        self.snap_px.setRange(2, 60)
        self.snap_px.setValue(10)
        self.snap_px.setSuffix(" px")
        sr = QHBoxLayout()
        sr.addWidget(self.snap)
        sr.addWidget(self.snap_px)
        f.addRow(sr)
        v.addLayout(f)
        v.addWidget(_muted("Click to place vertices · double-click or Enter to finish · Backspace removes the last "
                           "vertex · Esc cancels. Points finish with one click. Rotate the view with the right or "
                           "middle mouse button."))
        br = QHBoxLayout()
        for t, fn in (("Finish", cb._hd_finish), ("Undo vertex", cb._hd_undo), ("Cancel", cb._hd_cancel)):
            b = QPushButton(t)
            b.clicked.connect(fn)
            br.addWidget(b)
        v.addLayout(br)
        self.lbl_draft = _muted(" ")
        v.addWidget(self.lbl_draft)
        g = QGroupBox("Features")
        gv = QVBoxLayout(g)
        self.lst_feat = QListWidget()
        self.lst_feat.setMinimumHeight(140)
        self.lst_feat.itemDoubleClicked.connect(lambda it: cb.hd_zoom_feature(it.data(Qt.UserRole)))
        gv.addWidget(self.lst_feat)
        fr = QHBoxLayout()
        b_z = QPushButton("Zoom")
        b_z.clicked.connect(lambda: self.lst_feat.currentItem() and cb.hd_zoom_feature(
            self.lst_feat.currentItem().data(Qt.UserRole)))
        b_d = QPushButton("Delete")
        b_d.clicked.connect(lambda: cb.hd_delete_features([it.data(Qt.UserRole)
                                                           for it in self.lst_feat.selectedItems()]))
        self.lst_feat.setSelectionMode(QListWidget.ExtendedSelection)
        fr.addWidget(b_z)
        fr.addWidget(b_d)
        gv.addLayout(fr)
        v.addWidget(g)
        g2 = QGroupBox("Lanelet (lane between two boundary lines)")
        f2 = QFormLayout(g2)
        self.cmb_left = QComboBox()
        self.cmb_right = QComboBox()
        self.cmb_llsub = QComboBox()
        for s in H.FEATURE_TYPES["lanelet"][4]:
            self.cmb_llsub.addItem(s)
        self.speed = QSpinBox()
        self.speed.setRange(0, 200)
        self.speed.setValue(50)
        self.speed.setSuffix(" km/h")
        self.speed.setSpecialValueText("—")
        f2.addRow("Left bound", self.cmb_left)
        f2.addRow("Right bound", self.cmb_right)
        f2.addRow("Subtype", self.cmb_llsub)
        f2.addRow("Speed limit", self.speed)
        b_ll = QPushButton("Create lanelet")
        b_ll.clicked.connect(cb.hd_make_lanelet)
        f2.addRow(b_ll)
        f2.addRow(_muted("Left and right in the driving direction; the direction is that of the left line as "
                         "drawn. A line shared by two lanes is used by both."))
        v.addWidget(g2)
        b_close = QPushButton("Stop annotating")
        b_close.clicked.connect(lambda: cb.hd_annotate(False))
        v.addWidget(b_close)
        v.addStretch(1)
        self.lst_type.currentRowChanged.connect(self._sync_type)
        self.cmb_layer.currentIndexChanged.connect(lambda _i: cb._hd_refresh_lists())
        self._sync_type()

    def ftype(self):
        it = self.lst_type.currentItem()
        return it.data(Qt.UserRole) if it is not None else "lane_line"

    def _sync_type(self, *_):
        k = self.ftype()
        geom, _lab, _col, _l2, subs = H.FEATURE_TYPES[k]
        self.cmb_sub.clear()
        for s in subs:
            self.cmb_sub.addItem(s or "—", s)
        self.cmb_col.setEnabled(k.startswith("lane_line") or k in ("stop_line", "crosswalk", "road_symbol"))
        self.width_.setEnabled(geom == "LineString")
        self.width_.setValue(0.15 if k.startswith("lane_line") else (0.3 if k == "stop_line" else 0.0))
        self.height_.setEnabled(geom == "Point" or k in ("guardrail", "curb"))
        self.cb._hd_cancel()

    def attrs(self):
        k = self.ftype()
        a = {"SUBTYPE": self.cmb_sub.currentData() or "", "LANE_ID": self.lane.text().strip(),
             "NOTE": self.note.text().strip()}
        if self.cmb_col.isEnabled():
            a["COLOR"] = self.cmb_col.currentText()
        else:
            a["COLOR"] = ""
        if self.width_.isEnabled():
            a["WIDTH"] = self.width_.value() or None
        if self.height_.isEnabled() and self.height_.value() > 0:
            a["HEIGHT"] = self.height_.value()
        return k, a


# ================================================================ dialogs
class _Dlg(QDialog):
    def __init__(self, title, intro, parent):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(18, 14, 18, 14)
        t = QLabel(title)
        t.setObjectName("title")
        self.v.addWidget(t)
        if intro:
            self.v.addWidget(_muted(intro))
        self.f = QFormLayout()
        self.v.addLayout(self.f)

    def finish(self):
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.v.addWidget(bb)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)


class LabelDialog(_Dlg):
    def __init__(self, n_sel, parent=None):
        super().__init__("Label points (HD map classes)",
                         f"{n_sel:,} selected points get the class below (Ctrl+Z to undo). Codes 64 and above are "
                         "user-definable LAS classes; 2, 6, 11, 13, 15, 17 are ASPRS standard classes.", parent)
        self.lst = QListWidget()
        for code, (_k, lab, col) in sorted(H.CLASSES.items(), key=lambda kv: (kv[0] < 64, kv[0])):
            it = QListWidgetItem(_swatch(col), f"{code:>3}  {lab}")
            it.setData(Qt.UserRole, code)
            self.lst.addItem(it)
        self.lst.setCurrentRow(0)
        self.lst.setMinimumHeight(360)
        self.lst.itemDoubleClicked.connect(lambda _it: self.accept())
        self.v.addWidget(self.lst)
        self.finish()

    def code(self):
        it = self.lst.currentItem()
        return it.data(Qt.UserRole) if it is not None else None


class MarkingDialog(_Dlg):
    def __init__(self, scopes, parent=None):
        super().__init__("Extract lane markings",
                         "Bright stripes on the road (intensity) → lane line features (solid / dashed). Works best "
                         "on road-surface (11) or ground (2) points of a mobile-mapping cloud.", parent)
        self.scope = QComboBox()
        for k, t in scopes:
            self.scope.addItem(t, k)
        self.f.addRow("Points", self.scope)
        self.k = _dsp(3.0, 1.0, 10.0, 0.5, 1)
        self.k.setToolTip("Bright = intensity above the local median + k × robust σ (2 m cells)")
        self.pct = _dsp(80.0, 0.0, 99.9, 1.0, 1, " %")
        self.minlen = _dsp(1.0, 0.2, 20.0, 0.1, 1, " m")
        self.maxw = _dsp(0.45, 0.05, 2.0, 0.05, 2, " m")
        self.gap = _dsp(12.0, 1.0, 50.0, 1.0, 1, " m")
        self.minline = _dsp(5.0, 0.0, 500.0, 1.0, 1, " m")
        for lab, w in (("Brightness k", self.k), ("And above percentile", self.pct), ("Stripe ≥", self.minlen),
                       ("Stripe width ≤", self.maxw), ("Join stripes over gaps ≤", self.gap),
                       ("Keep lines ≥", self.minline)):
            self.f.addRow(lab, w)
        self.cls = QCheckBox("Classify stripe points as 64 (lane marking) and other marks as 72 (symbol)")
        self.cls.setChecked(True)
        self.f.addRow(self.cls)
        self.finish()


class PoleDialog(_Dlg):
    def __init__(self, scopes, parent=None):
        super().__init__("Extract poles", "Thin vertical columns of points above the ground (class 2 needed) → pole "
                                          "point features.", parent)
        self.scope = QComboBox()
        for k, t in scopes:
            self.scope.addItem(t, k)
        self.f.addRow("Points", self.scope)
        self.minh = _dsp(2.5, 0.5, 50.0, 0.5, 1, " m")
        self.maxd = _dsp(0.6, 0.05, 3.0, 0.05, 2, " m")
        self.f.addRow("Height ≥", self.minh)
        self.f.addRow("Diameter ≤", self.maxd)
        self.cls = QCheckBox("Classify pole points as 66 (pole)")
        self.cls.setChecked(True)
        self.f.addRow(self.cls)
        self.finish()


# ================================================================ mixin
class HdMapMixin:
    # ---------------------------------------------------------------- layers
    def hd_layers(self):
        return [d for d in self.project.of_kind("vector") if H.is_hdmap(d.obj)]

    def hd_layer(self, create=True):
        """The HD map layer being edited (the one chosen in the panel, else the first, else a new one)."""
        lid = None
        p = getattr(self, "hd_panel", None)
        if p is not None and p.cmb_layer.currentData() is not None:
            lid = p.cmb_layer.currentData()
        ds = self.project.get(lid) if lid is not None else None
        if ds is None:
            ls = self.hd_layers()
            ds = ls[0] if ls else None
        if ds is None and create:
            ds = self.hd_new_layer()
        return ds

    def hd_new_layer(self):
        from core.project import Dataset
        P = self.project
        if P.crs is None and not P.datasets:
            QMessageBox.information(self, "HD map", "Load a point cloud first.")
            return None
        ds = Dataset(P.unique_name("HD map"), "vector", H.new_layer(), crs=P.crs, source_crs=P.crs, style=hd_style())
        P.add(ds)
        self._project_changed()
        self._set_dirty(True)
        self._hd_refresh_lists(select=ds.id)
        return ds

    def _hd_changed(self, ds):
        ds.edit_version += 1
        self._set_dirty(True)
        self._show_routes()
        self._refresh_data_card()
        self._hd_refresh_lists()

    def _hd_refresh_lists(self, select=None):
        p = getattr(self, "hd_panel", None)
        if p is None:
            return
        cur = select if select is not None else p.cmb_layer.currentData()
        p.cmb_layer.blockSignals(True)
        p.cmb_layer.clear()
        for d in self.hd_layers():
            p.cmb_layer.addItem(d.name, d.id)
        j = p.cmb_layer.findData(cur)
        p.cmb_layer.setCurrentIndex(j if j >= 0 else 0)
        p.cmb_layer.blockSignals(False)
        ds = self.project.get(p.cmb_layer.currentData()) if p.cmb_layer.count() else None
        p.lst_feat.clear()
        for cmb in (p.cmb_left, p.cmb_right):
            keep = cmb.currentData()
            cmb.clear()
            if ds is not None:
                for f in H.line_features(ds.obj):
                    a = f["attrs"]
                    cmb.addItem(f"#{a['FID']} {a['TYPE']} {a.get('SUBTYPE') or ''}".strip(), a["FID"])
            j = cmb.findData(keep)
            if j >= 0:
                cmb.setCurrentIndex(j)
        if p.cmb_right.count() > 1 and p.cmb_right.currentIndex() == p.cmb_left.currentIndex():
            p.cmb_right.setCurrentIndex(1)
        if ds is None:
            return
        for f in ds.obj.features:
            a = f["attrs"]
            q = f["parts"][0]
            if a.get("TYPE") == "lanelet":
                extra = f"w {a['WIDTH']:.2f} m · #{a.get('LEFT_FID')} | #{a.get('RIGHT_FID')}" if a.get("WIDTH") else ""
            elif f["type"] == "Point":
                extra = f"h {a['HEIGHT']:g} m" if a.get("HEIGHT") else ""
            else:
                extra = f"{np.hypot(*np.diff(q[:, :2], axis=0).T).sum():.1f} m"
            it = QListWidgetItem(_swatch(H.COLOR_MAP.get(a.get("TYPE"), "#888")),
                                 f"#{a.get('FID')}  {a.get('TYPE')}  {a.get('SUBTYPE') or ''}  {extra}")
            it.setData(Qt.UserRole, a.get("FID"))
            p.lst_feat.addItem(it)

    # ---------------------------------------------------------------- annotation
    def hd_annotate(self, on=True):
        if not on:
            if getattr(self, "hd_panel", None) is not None:
                self.hd_panel.hide()
            if self.selector.mode == "annotate":
                self.set_mode("navigate")
            return
        if not self.project.datasets:
            QMessageBox.information(self, "HD map", "Load a point cloud first.")
            self.mode_actions[self.selector.mode].setChecked(True)
            return
        if getattr(self, "hd_panel", None) is None:
            self.hd_panel = AnnotationPanel(self, self)
            self.addDockWidget(Qt.LeftDockWidgetArea, self.hd_panel)
            self.hd_panel.visibilityChanged.connect(lambda vis: (not vis) and self.selector.mode == "annotate"
                                                    and self._hd_leave())
        self._hd_draft = []
        self._hd_cursor = None
        sel = self.selector
        sel.on_annotate_click = self._hd_click
        sel.on_annotate_move = self._hd_move
        sel.on_annotate_finish = self._hd_finish
        sel.on_annotate_double = self._hd_double
        sel.on_annotate_undo = self._hd_undo
        sel.on_annotate_cancel = self._hd_cancel
        sel.annotate_has_draft = lambda: bool(getattr(self, "_hd_draft", None))
        if not self.hd_layers():
            self.hd_new_layer()
        self._hd_refresh_lists()
        self.hd_panel.show()
        self.hd_panel.raise_()
        self._hd_entering = True
        try:
            self.set_mode("annotate")
        finally:
            self._hd_entering = False
        self.statusBar().showMessage("HD map annotation: click to place vertices, double-click / Enter to finish",
                                     8000)

    def _hd_leave(self):
        self.set_mode("navigate")

    def _hd_point_at(self, pos):
        p = self.hd_panel
        if p.snap.isChecked() and self.pc is not None:
            from render.picking import pick_point
            i = pick_point(self.viewer, self.pc, pos, radius=float(p.snap_px.value()))
            if i is not None:
                return np.array(self.pc.xyz[i], float)
        return self.viewer.screen_to_world(pos)

    def _hd_redraw(self):
        k = self.hd_panel.ftype() if getattr(self, "hd_panel", None) is not None else "lane_line"
        geom = H.FEATURE_TYPES[k][0]
        pts = np.asarray(self._hd_draft, float).reshape(-1, 3) if self._hd_draft else np.empty((0, 3))
        self.viewer.set_annotation_draft(pts, self._hd_cursor if len(pts) else None, H.COLOR_MAP.get(k, "#ffd23d"),
                                         closed=geom == "Polygon")
        if getattr(self, "hd_panel", None) is not None:
            n = len(pts)
            L = float(np.hypot(*np.diff(pts[:, :2], axis=0).T).sum()) if n > 1 else 0.0
            self.hd_panel.lbl_draft.setText(f"{n} vertices · {L:.2f} m" if n else " ")
        self._update_edit_actions()                     # Backspace removes the last vertex while drawing

    def _hd_click(self, pos):
        if getattr(self, "hd_panel", None) is None:
            return
        w = self._hd_point_at(pos)
        if w is None:
            return
        if self._hd_draft:                                          # second click of a double-click
            last = self.viewer.xy_to_screen(np.asarray(self._hd_draft[-1][:2]))[0]
            if np.hypot(last[0] - pos[0], last[1] - pos[1]) < 4:
                return
        self._hd_draft.append(w)
        k = self.hd_panel.ftype()
        if H.FEATURE_TYPES[k][0] == "Point":
            self._hd_finish()
            return
        self._hd_redraw()

    def _hd_double(self, pos):
        """Double-click: the press / release before it normally placed the vertex already; if not (the window
        system delivered only the double-click), place it, then finish."""
        if getattr(self, "hd_panel", None) is None:
            return
        if self._hd_draft:
            last = self.viewer.xy_to_screen(np.asarray(self._hd_draft[-1][:2]))[0]
            if np.hypot(last[0] - pos[0], last[1] - pos[1]) >= 4:
                w = self._hd_point_at(pos)
                if w is not None:
                    self._hd_draft.append(w)
        self._hd_finish()

    def _hd_move(self, pos):
        if not getattr(self, "_hd_draft", None):
            return
        self._hd_cursor = self.viewer.screen_to_world(pos)
        self._hd_redraw()

    def _hd_undo(self):
        if getattr(self, "_hd_draft", None):
            self._hd_draft.pop()
            self._hd_redraw()

    def _hd_cancel(self):
        self._hd_draft = []
        self._hd_cursor = None
        if hasattr(self, "viewer"):
            self.viewer.set_annotation_draft(None)
        if getattr(self, "hd_panel", None) is not None:
            self.hd_panel.lbl_draft.setText(" ")
        if hasattr(self, "act_delete"):
            self._update_edit_actions()

    def _hd_finish(self):
        if not getattr(self, "_hd_draft", None) or getattr(self, "hd_panel", None) is None:
            return
        k, a = self.hd_panel.attrs()
        pts = np.asarray(self._hd_draft, float)
        geom = H.FEATURE_TYPES[k][0]
        need = {"Point": 1, "LineString": 2, "Polygon": 3}[geom]
        if len(pts) < need:
            self.statusBar().showMessage(f"A {geom.lower()} needs at least {need} vertices", 5000)
            return
        if not np.isfinite(pts[:, 2]).all():                       # vertex off the data: height from neighbours
            ok = np.isfinite(pts[:, 2])
            pts[:, 2] = np.interp(np.arange(len(pts)), np.flatnonzero(ok), pts[ok, 2]) if ok.any() else 0.0
        ds = self.hd_layer()
        if ds is None:
            return
        f = H.make_feature(ds.obj, k, pts, **a)
        self._hd_cancel()
        self._hd_changed(ds)
        self.statusBar().showMessage(f"#{f['attrs']['FID']} {H.FEATURE_TYPES[k][1]} added to {ds.name}", 5000)

    def hd_delete_features(self, fids):
        ds = self.hd_layer(create=False)
        if ds is None or not fids:
            return
        used = {int(f["attrs"].get(k) or -1) for f in ds.obj.features if f["attrs"].get("TYPE") == "lanelet"
                for k in ("LEFT_FID", "RIGHT_FID")}
        if used & {int(x) for x in fids}:
            r = QMessageBox.question(self, "HD map", "Some of these lines bound a lanelet — delete them anyway (the "
                                     "lanelets then have a missing bound and are skipped in a Lanelet2 export)?")
            if r != QMessageBox.Yes:
                return
        fs = {int(x) for x in fids}
        ds.obj.features = [f for f in ds.obj.features if int(f["attrs"].get("FID") or -1) not in fs]
        self._hd_changed(ds)

    def hd_zoom_feature(self, fid):
        ds = self.hd_layer(create=False)
        f = H.feature_by_fid(ds.obj, fid) if ds is not None else None
        if f is None:
            return
        q = f["parts"][0]
        m = max(5.0, float(np.ptp(q[:, :2], axis=0).max()) * 0.3)
        lo = np.r_[q[:, :2].min(axis=0) - m, np.nanmin(q[:, 2]) - 1]
        hi = np.r_[q[:, :2].max(axis=0) + m, np.nanmax(q[:, 2]) + 1]
        keep = self.viewer.bbox
        self.viewer.bbox = (lo, hi)
        self.viewer.fit_view()
        self.viewer.bbox = keep

    def hd_make_lanelet(self):
        p = self.hd_panel
        ds = self.hd_layer(create=False)
        if ds is None or p.cmb_left.currentData() is None or p.cmb_right.currentData() is None:
            QMessageBox.information(self, "Lanelet", "Draw or extract the two boundary lines first.")
            return
        try:
            f = H.make_lanelet(ds.obj, p.cmb_left.currentData(), p.cmb_right.currentData(),
                               subtype=p.cmb_llsub.currentText(), speed=p.speed.value() or None,
                               lane_id=p.lane.text().strip())
        except ValueError as e:
            QMessageBox.warning(self, "Lanelet", str(e))
            return
        self._hd_changed(ds)
        self.statusBar().showMessage(f"Lanelet #{f['attrs']['FID']} (width ≈ {f['attrs']['WIDTH']:.2f} m)", 6000)

    # ---------------------------------------------------------------- point labels
    def _hd_need_cloud(self):
        if self.pc is None:
            QMessageBox.information(self, "HD map", "Choose ✎ Edit on a point cloud first (Data panel).")
            return False
        return True

    def hd_label_points(self):
        from core.trash import ClassifyCommand
        if not self._hd_need_cloud():
            return
        idx = self.sel.indices() if self.sel is not None else np.empty(0, np.int64)
        if len(idx) == 0:
            QMessageBox.information(self, "Label points", "Select points first (Box / Lasso / Pick).")
            return
        dlg = LabelDialog(len(idx), self)
        if dlg.exec() != QDialog.Accepted or dlg.code() is None:
            return
        code = int(dlg.code())
        self.history.push(ClassifyCommand(self.pc, idx, values=np.full(len(idx), code),
                                          label=f"Label {len(idx):,} points as {H.class_name(code)}"))
        self._hd_show_classes(f"{len(idx):,} points labelled {H.class_name(code)} ({code}) · Ctrl+Z to undo")

    def _hd_show_classes(self, msg):
        from render.colormap import MODE_LABELS, available_modes
        self.cmb_color.blockSignals(True)
        self.cmb_color.clear()
        for m in available_modes(self.pc):
            self.cmb_color.addItem(MODE_LABELS[m], m)
        self.cmb_color.setCurrentIndex(self.cmb_color.findData("classification"))
        self.cmb_color.blockSignals(False)
        self.viewer.set_color_mode("classification")
        self._after_edit(msg)

    def _hd_scopes(self, default_classes):
        pc = self.pc
        cls = pc.attrs.get("Classification")
        out = []
        if cls is not None:
            present = set(np.unique(np.asarray(cls)[pc.active_indices()]).tolist())
            for codes, t in default_classes:
                if present & set(codes):
                    out.append((codes, t))
        if self.sel is not None and self.sel.count:
            out.append(("selected", f"Selected points ({self.sel.count:,})"))
        out.append(("all", "All active points"))
        return out

    def _hd_scope_idx(self, scope):
        pc = self.pc
        act = pc.active_indices()
        if scope == "all":
            return act
        if scope == "selected":
            return np.flatnonzero(self.sel.mask & pc.active_mask())
        cls = np.asarray(pc.attrs["Classification"])
        return act[np.isin(cls[act], scope)]

    # ---------------------------------------------------------------- extraction
    def hd_extract_markings(self):
        if not self._hd_need_cloud():
            return
        pc = self.pc
        if "Intensity" not in pc.attrs:
            QMessageBox.information(self, "Lane markings", "The point cloud has no intensity values.")
            return
        dlg = MarkingDialog(self._hd_scopes([((11,), "Road surface (11)"), ((2, 11), "Ground and road (2, 11)")]),
                            self)
        if dlg.exec() != QDialog.Accepted:
            return
        idx = self._hd_scope_idx(dlg.scope.currentData())
        if len(idx) < 100:
            QMessageBox.information(self, "Lane markings", "Too few points in the chosen scope.")
            return
        prm = dict(k=dlg.k.value(), min_pct=dlg.pct.value(), min_len=dlg.minlen.value(), max_width=dlg.maxw.value(),
                   max_gap=dlg.gap.value(), min_line=dlg.minline.value())
        self._hd_ctx = {"classify": dlg.cls.isChecked(), "pc": pc}
        self._run_task("Extracting lane markings…", H.extract_markings, (pc.xyz, pc.attrs["Intensity"], idx), prm,
                       self._on_hd_markings, report_progress=True)

    def _on_hd_markings(self, r):
        from core.trash import ClassifyCommand
        self._close_progress()
        if self._hd_ctx["pc"] is not self.pc:
            return
        ds = self.hd_layer()
        for coords, sub, n in r["lines"]:
            H.make_feature(ds.obj, "lane_line", coords, SUBTYPE=sub, NOTE=f"extracted, {n} stripe(s)")
        for coords, n in r.get("stop_lines", []):
            H.make_feature(ds.obj, "stop_line", coords, NOTE="extracted")
        msg = f"{len(r['lines'])} lane line(s)" + (f", {len(r['stop_lines'])} stop line(s)" if r.get("stop_lines")
                                                   else "") + f" extracted into {ds.name}"
        if self._hd_ctx["classify"] and (len(r["stripe_pts"]) or len(r["other_pts"])):
            idx = np.r_[r["stripe_pts"], r["other_pts"]].astype(np.int64)
            vals = np.r_[np.full(len(r["stripe_pts"]), 64), np.full(len(r["other_pts"]), 72)]
            self.history.push(ClassifyCommand(self.pc, idx, values=vals,
                                              label=f"Lane markings: {len(idx):,} points"))
            self._hd_show_classes(msg + f" · {len(r['stripe_pts']):,} marking points (64), "
                                        f"{len(r['other_pts']):,} other marks (72) · Ctrl+Z to undo the classes")
        else:
            self.statusBar().showMessage(msg, 10000)
        self._hd_changed(ds)

    def hd_extract_poles(self):
        if not self._hd_need_cloud():
            return
        pc = self.pc
        cls = pc.attrs.get("Classification")
        if cls is None or not (np.asarray(cls)[pc.active_indices()] == 2).any():
            QMessageBox.information(self, "Poles", "Pole extraction needs ground points (class 2) — Edit → Classify "
                                    "Points first.")
            return
        dlg = PoleDialog(self._hd_scopes([((1, 0, 3, 4, 5), "Above-ground classes (0, 1, 3–5)")]), self)
        if dlg.exec() != QDialog.Accepted:
            return
        idx = self._hd_scope_idx(dlg.scope.currentData())
        act = pc.active_indices()
        c = np.asarray(cls)
        ground = act[c[act] == 2]
        idx = idx[c[idx] != 2]
        self._hd_ctx = {"classify": dlg.cls.isChecked(), "pc": pc}
        self._run_task("Extracting poles…", H.extract_poles, (pc.xyz, idx, ground),
                       dict(min_height=dlg.minh.value(), max_diameter=dlg.maxd.value()), self._on_hd_poles,
                       report_progress=True)

    def _on_hd_poles(self, poles):
        from core.trash import ClassifyCommand
        self._close_progress()
        if self._hd_ctx["pc"] is not self.pc:
            return
        ds = self.hd_layer()
        for p in poles:
            H.make_feature(ds.obj, "pole", np.r_[p["xy"], p["z_ground"]], HEIGHT=round(p["height"], 2),
                           NOTE="extracted")
        msg = f"{len(poles)} pole(s) extracted into {ds.name}"
        if self._hd_ctx["classify"] and poles:
            idx = np.concatenate([p["idx"] for p in poles]).astype(np.int64)
            self.history.push(ClassifyCommand(self.pc, idx, values=np.full(len(idx), 66),
                                              label=f"Poles: {len(idx):,} points"))
            self._hd_show_classes(msg + f" · {len(idx):,} points classified 66 · Ctrl+Z to undo the classes")
        else:
            self.statusBar().showMessage(msg, 10000)
        self._hd_changed(ds)

    # ---------------------------------------------------------------- export
    def hd_export(self):
        ls = self.hd_layers()
        if not ls:
            QMessageBox.information(self, "Export HD map", "There is no HD map layer yet.")
            return
        ds = self.hd_layer(create=False) or ls[0]
        if len(ls) > 1:
            from PySide6.QtWidgets import QInputDialog
            names = [d.name for d in ls]
            name, ok = QInputDialog.getItem(self, "Export HD map", "Layer:", names, names.index(ds.name), False)
            if not ok:
                return
            ds = ls[names.index(name)]
        if not ds.obj.features:
            QMessageBox.information(self, "Export HD map", f"{ds.name} has no features.")
            return
        flt = ("Lanelet2 map (*.osm);;Shapefile — one file per feature type (*.shp);;GeoPackage (*.gpkg);;"
               "GeoJSON (*.geojson)")
        path, sel = QFileDialog.getSaveFileName(self, "Export HD map", ds.name.replace(" ", "_") + ".osm", flt)
        if not path:
            return
        ext = {"Lanelet2": ".osm", "Shapefile": ".shp", "GeoPackage": ".gpkg", "GeoJSON": ".geojson"}
        e = next((v for k, v in ext.items() if sel.startswith(k)), os.path.splitext(path)[1] or ".osm")
        if not path.lower().endswith(e):
            path = os.path.splitext(path)[0] + e
        try:
            if e == ".osm":
                from fileio.lanelet2 import write_lanelet2
                c = write_lanelet2(path, ds.obj, self.project.crs)
                txt = (f"{os.path.basename(path)}: {c['ways']} lines / areas, {c['lanelets']} lanelets, "
                       f"{c['nodes']:,} nodes. Origin for a UTM projector: {c['origin_lat']:.7f}, "
                       f"{c['origin_lon']:.7f}")
                if c["lanelets_skipped"]:
                    txt += f"\n{c['lanelets_skipped']} lanelet(s) skipped: a boundary line was deleted."
                QMessageBox.information(self, "Export HD map", txt)
            else:
                groups = {}
                for f in ds.obj.features:
                    groups.setdefault(f["attrs"].get("TYPE") or "feature", []).append(f)
                self._write_feature_groups(path, groups)
        except Exception as ex:  # noqa: BLE001
            QMessageBox.critical(self, "Export HD map", f"{type(ex).__name__}: {ex}")
