"""Auto-filter dialog (non-modal): choose method + parameters + scope, Preview, then keep or cancel."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QPushButton, QRadioButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from core.filters import HELP, METHODS
from core.surface import nice_number
from ui.theme import C, dark_titlebar


def _section(t):
    l = QLabel(t.upper())
    l.setObjectName("sectionTitle")
    return l


def _muted(t):
    l = QLabel(t)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


def _dspin(v, lo, hi, step, dec=2, suffix=" m"):
    s = QDoubleSpinBox(minimum=lo, maximum=hi, singleStep=step, decimals=dec)
    s.setValue(v)
    s.setSuffix(suffix)
    return s


class FilterDialog(QDialog):
    """cb: filter_run(method, params, scope, op)  filter_finish(keep: bool)"""

    def __init__(self, cb, parent=None):
        super().__init__(parent)
        self.cb = cb
        self.setWindowTitle("Auto filter")
        self.setModal(False)
        self.resize(560, 760)
        self._finished = True
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        t = QLabel("Auto filter")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Finds candidate noise points and selects them. Nothing is rejected automatically — "
                           "review the selection (3D view / Subset editor), then press Reject."))
        v.addWidget(_section("Method"))
        self.cmb = QComboBox()
        for k, label in METHODS.items():
            self.cmb.addItem(label, k)
        v.addWidget(self.cmb)
        self.help = _muted("")
        v.addWidget(self.help)

        v.addWidget(_section("Parameters"))
        self.pages = QStackedWidget()
        self.w = {}
        # SOR
        f = QFormLayout(pg := QWidget())
        self.w["sor_k"] = QSpinBox(minimum=3, maximum=100, value=12)
        self.w["sor_n"] = _dspin(3.0, 0.5, 10, 0.5, 1, " σ")
        f.addRow(_muted("Neighbours k"), self.w["sor_k"])
        f.addRow(_muted("Threshold"), self.w["sor_n"])
        self.pages.addWidget(pg)
        # radius
        f = QFormLayout(pg := QWidget())
        self.w["rad_r"] = _dspin(1.0, 0.01, 1e4, 0.1)
        self.w["rad_n"] = QSpinBox(minimum=1, maximum=1000, value=4)
        f.addRow(_muted("Search radius"), self.w["rad_r"])
        f.addRow(_muted("Min. neighbours"), self.w["rad_n"])
        self.pages.addWidget(pg)
        # grid median
        f = QFormLayout(pg := QWidget())
        self.w["grid_cell"] = _dspin(2.0, 0.05, 1e4, 0.5)
        self.w["grid_mode"] = QComboBox()
        self.w["grid_mode"].addItem("Absolute distance (m)", "abs")
        self.w["grid_mode"].addItem("Multiple of robust σ", "mad")
        self.w["grid_val"] = _dspin(0.30, 0.001, 1e4, 0.05, 3)
        self.w["grid_side"] = QComboBox()
        self.w["grid_side"].addItem("Both sides", "both")
        self.w["grid_side"].addItem("Upper side only (above the surface — shoal / high points)", "shoal")
        self.w["grid_side"].addItem("Lower side only (below the surface — deep / low points)", "deep")
        self.w["grid_mode"].currentIndexChanged.connect(self._grid_mode)
        f.addRow(_muted("Cell size"), self.w["grid_cell"])
        f.addRow(_muted("Threshold type"), self.w["grid_mode"])
        f.addRow(_muted("Threshold"), self.w["grid_val"])
        f.addRow(_muted("Flag"), self.w["grid_side"])
        self.pages.addWidget(pg)
        # reference surface
        f = QFormLayout(pg := QWidget())
        self.w["surf"] = QComboBox()
        self.w["surf_above"] = _dspin(0.5, 0.0, 1e4, 0.05, 3)
        self.w["surf_below"] = _dspin(0.5, 0.0, 1e4, 0.05, 3)
        f.addRow(_muted("Surface"), self.w["surf"])
        f.addRow(_muted("Above the surface by"), self.w["surf_above"])
        f.addRow(_muted("Below the surface by"), self.w["surf_below"])
        self.pages.addWidget(pg)
        # range
        f = QFormLayout(pg := QWidget())
        self.lbl_r1, self.lbl_r2 = _muted("Shallowest"), _muted("Deepest")
        self.w["r1"] = _dspin(0.0, -1e5, 1e5, 0.5)
        self.w["r2"] = _dspin(100.0, -1e5, 1e5, 0.5)
        f.addRow(self.lbl_r1, self.w["r1"])
        f.addRow(self.lbl_r2, self.w["r2"])
        self.pages.addWidget(pg)
        self._extra_pages()
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(self.pages)
        sc.setMinimumHeight(220)
        v.addWidget(sc, 3)

        v.addWidget(_section("Scope"))
        self.grp = QButtonGroup(self)
        self.rb = {}
        for key, label in (("all", "All active points"), ("selected", "Selected points"),
                           ("subset", "Current subset")):
            rb = QRadioButton(label)
            self.grp.addButton(rb)
            self.rb[key] = rb
            v.addWidget(rb)
        self.rb["all"].setChecked(True)
        row = QHBoxLayout()
        row.addWidget(_muted("Result"))
        self.cmb_op = QComboBox()
        self.cmb_op.addItem("Replace the selection", "replace")
        self.cmb_op.addItem("Add to the selection", "add")
        row.addWidget(self.cmb_op, 1)
        v.addLayout(row)
        v.addStretch()
        self.lbl = QLabel(" ")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        foot = QHBoxLayout()
        self.btn_preview = QPushButton("Preview")
        self.btn_preview.setToolTip("Run the filter and select the candidates")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setToolTip("Close and restore the previous selection")
        self.btn_keep = QPushButton("Keep selection")
        self.btn_keep.setProperty("primary", True)
        self.btn_keep.setToolTip("Close and keep the candidates selected for review / Reject")
        self.btn_keep.setEnabled(False)
        foot.addWidget(self.btn_preview)
        foot.addStretch()
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_keep)
        v.addLayout(foot)
        self.btn_preview.clicked.connect(self._preview)
        self.btn_cancel.clicked.connect(lambda: self._finish(False))
        self.btn_keep.clicked.connect(lambda: self._finish(True))
        self.cmb.currentIndexChanged.connect(self._method)
        self._method()

    def _extra_pages(self):
        from core.s44 import ORDER_LABEL
        from ui.uncertainty_widget import UncertaintyWidget
        w = self.w
        # CUBE
        f = QFormLayout(pg := QWidget())
        w["cube_res"] = _dspin(1.0, 0.05, 1e3, 0.25)
        w["cube_cap"] = _dspin(5.0, 0.5, 50, 0.5, 1, " % of depth")
        w["cube_kh"] = _dspin(3.0, 1.0, 10, 0.25, 2, " σ")
        w["cube_kf"] = _dspin(3.0, 1.0, 10, 0.25, 2, " σ")
        w["cube_dis"] = QComboBox()
        for k, t in (("locale", "Locale — agree with the neighbourhood"), ("density", "Density — most soundings"),
                     ("prior", "Prior — closest to a reference surface")):
            w["cube_dis"].addItem(t, k)
        w["cube_surf"] = QComboBox()
        w["cube_layers"] = QCheckBox("Add the CUBE depth, uncertainty (95 %) and hypothesis-count grids as layers")
        w["cube_unc"] = UncertaintyWidget(thu=True)
        f.addRow(_muted("Node spacing"), w["cube_res"])
        f.addRow(_muted("Capture distance"), w["cube_cap"])
        f.addRow(_muted("New hypothesis beyond"), w["cube_kh"])
        f.addRow(_muted("Flag soundings beyond"), w["cube_kf"])
        f.addRow(_muted("Disambiguation"), w["cube_dis"])
        f.addRow(_muted("Reference surface"), w["cube_surf"])
        f.addRow(w["cube_layers"])
        f.addRow(_section("Uncertainty"))
        f.addRow(w["cube_unc"])
        self.pages.addWidget(pg)
        # IHO S-44
        f = QFormLayout(pg := QWidget())
        w["iho_order"] = QComboBox()
        for k, t in ORDER_LABEL.items():
            w["iho_order"].addItem(t, k)
        w["iho_order"].setCurrentIndex(2)
        w["iho_mode"] = QComboBox()
        w["iho_mode"].addItem("Residual to a reference exceeds the TVU limit", "residual")
        w["iho_mode"].addItem("Own TPU (TVU) exceeds the limit", "tpu")
        w["iho_mode"].addItem("Either", "either")
        w["iho_ref"] = QComboBox()
        w["iho_ref"].addItem("Local median surface", "local")
        w["iho_ref"].addItem("TIN / DEM", "surface")
        w["iho_cell"] = _dspin(1.0, 0.05, 1e3, 0.25)
        w["iho_surf"] = QComboBox()
        w["iho_unc"] = UncertaintyWidget(thu=False)
        f.addRow(_muted("Order"), w["iho_order"])
        f.addRow(_muted("Flag"), w["iho_mode"])
        f.addRow(_muted("Reference"), w["iho_ref"])
        f.addRow(_muted("Median cell"), w["iho_cell"])
        f.addRow(_muted("TIN / DEM"), w["iho_surf"])
        f.addRow(_section("TPU (for 'own TPU')"))
        f.addRow(w["iho_unc"])
        self.pages.addWidget(pg)
        # robust local surface
        f = QFormLayout(pg := QWidget())
        w["loc_k"] = QSpinBox(minimum=6, maximum=200, value=24)
        w["loc_n"] = _dspin(4.0, 1.0, 20, 0.5, 1, " σ")
        w["loc_min"] = _dspin(0.02, 0.0, 10, 0.005, 3)
        w["loc_side"] = QComboBox()
        w["loc_side"].addItem("Both sides", "both")
        w["loc_side"].addItem("Upper side only", "shoal")
        w["loc_side"].addItem("Lower side only", "deep")
        f.addRow(_muted("Neighbours k"), w["loc_k"])
        f.addRow(_muted("Threshold"), w["loc_n"])
        f.addRow(_muted("Minimum σ"), w["loc_min"])
        f.addRow(_muted("Flag"), w["loc_side"])
        self.pages.addWidget(pg)
        # clusters
        f = QFormLayout(pg := QWidget())
        w["cl_r"] = _dspin(0.5, 0.01, 1e3, 0.1)
        w["cl_n"] = QSpinBox(minimum=2, maximum=10_000_000, value=50)
        f.addRow(_muted("Voxel size"), w["cl_r"])
        f.addRow(_muted("Groups smaller than"), w["cl_n"])
        self.pages.addWidget(pg)
        # photogrammetry
        f = QFormLayout(pg := QWidget())
        w["ph_views"] = QCheckBox("Too few supporting image pairs ('views' attribute)")
        w["ph_views"].setChecked(True)
        w["ph_minv"] = QSpinBox(minimum=1, maximum=50, value=2)
        w["ph_dist"] = QCheckBox("Off the local surface (allowance grows with distance²)")
        w["ph_dist"].setChecked(True)
        w["ph_k"] = QSpinBox(minimum=6, maximum=200, value=24)
        w["ph_n"] = _dspin(4.0, 1.0, 20, 0.5, 1, " σ")
        w["ph_min"] = _dspin(0.01, 0.0, 10, 0.005, 3)
        w["ph_free"] = QCheckBox("Floating points in front of what the cameras saw")
        w["ph_free"].setChecked(True)
        w["ph_sky"] = QCheckBox("Sky-coloured points off the surface (RGB)")
        w["ph_cams"] = QComboBox()
        b = QPushButton("Camera file…")
        b.clicked.connect(self._pick_cameras)
        row = QHBoxLayout()
        row.addWidget(w["ph_cams"], 1)
        row.addWidget(b)
        f.addRow(w["ph_views"])
        f.addRow(_muted("Minimum pairs"), w["ph_minv"])
        f.addRow(w["ph_dist"])
        f.addRow(_muted("Neighbours k"), w["ph_k"])
        f.addRow(_muted("Threshold"), w["ph_n"])
        f.addRow(_muted("Minimum σ (at median distance)"), w["ph_min"])
        f.addRow(w["ph_free"])
        f.addRow(w["ph_sky"])
        f.addRow(_muted("Cameras"), row)
        f.addRow(_muted("Camera CSV: image, X, Y, Z, omega, phi, kappa, f_px, width, height_px, cx, cy (as written "
                        "by Reconstruct → Photos / Video). A camera layer gives positions only (no floating-point test)."))
        self.pages.addWidget(pg)
        w["cube_dis"].currentIndexChanged.connect(self._method)
        w["iho_ref"].currentIndexChanged.connect(self._method)
        w["iho_mode"].currentIndexChanged.connect(self._method)

    def _pick_cameras(self):
        p, _ = QFileDialog.getOpenFileName(self, "Camera file", "", "CSV / text (*.csv *.txt *.CSV *.TXT)")
        if p:
            import os
            self.w["ph_cams"].addItem(f"File: {os.path.basename(p)}", ("csv", p))
            self.w["ph_cams"].setCurrentIndex(self.w["ph_cams"].count() - 1)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def closeEvent(self, e):
        if not self._finished:
            self._finish(False)
        super().closeEvent(e)

    # ---------- context ----------
    def set_context(self, counts, surfaces, has_subset, depth, spacing, zrange, scope=None, attrs=(), cameras=()):
        """counts: dict(all, selected, subset); surfaces: [(id, name)]; zrange: (zmin, zmax);
        attrs: attribute names of the cloud; cameras: [(dataset id, name)] of point layers."""
        for k, label in (("all", "All active points"), ("selected", "Selected points"), ("subset", "Current subset")):
            self.rb[k].setText(f"{label}   ({counts.get(k, 0):,})")
        self.rb["selected"].setEnabled(counts.get("selected", 0) > 0)
        self.rb["subset"].setEnabled(has_subset and counts.get("subset", 0) > 0)
        if scope and self.rb[scope].isEnabled():
            self.rb[scope].setChecked(True)
        elif not self.grp.checkedButton().isEnabled():
            self.rb["all"].setChecked(True)
        for key in ("surf", "cube_surf", "iho_surf"):
            cur = self.w[key].currentData()
            self.w[key].clear()
            for sid, name in surfaces:
                self.w[key].addItem(name, sid)
            i = self.w[key].findData(cur)
            self.w[key].setCurrentIndex(max(i, 0))
        self.w["cube_unc"].set_attributes(list(attrs))
        self.w["iho_unc"].set_attributes(list(attrs))
        cur = self.w["ph_cams"].currentData()
        files = [(self.w["ph_cams"].itemText(i), self.w["ph_cams"].itemData(i)) for i in range(self.w["ph_cams"].count())
                 if isinstance(self.w["ph_cams"].itemData(i), tuple) and self.w["ph_cams"].itemData(i)[0] == "csv"]
        self.w["ph_cams"].clear()
        self.w["ph_cams"].addItem("(none)", None)
        for cid, name in cameras:
            self.w["ph_cams"].addItem(f"Layer: {name}", ("vector", cid))
        for t, d in files:
            self.w["ph_cams"].addItem(t, d)
        i = self.w["ph_cams"].findData(cur)
        if i < 0 and cameras:
            i = 1
        self.w["ph_cams"].setCurrentIndex(max(i, 0))
        self.depth = depth
        self.zrange = zrange
        if self._finished:                                # fresh session: data-scaled defaults
            self.w["grid_cell"].setValue(nice_number(max(spacing * 2.5, 0.05)))
            self.w["cube_res"].setValue(nice_number(max(spacing * 3, 0.05)))
            self.w["iho_cell"].setValue(nice_number(max(spacing * 3, 0.05)))
            self.w["cl_r"].setValue(nice_number(max(spacing * 4, 0.01)))
            self.w["rad_r"].setValue(nice_number(max(spacing * 3, 0.01)))
            if depth:
                self.lbl_r1.setText("Shallowest depth")
                self.lbl_r2.setText("Deepest depth")
                self.w["r1"].setValue(round(-zrange[1], 2))
                self.w["r2"].setValue(round(-zrange[0], 2))
            else:
                self.lbl_r1.setText("Lowest Z")
                self.lbl_r2.setText("Highest Z")
                self.w["r1"].setValue(round(zrange[0], 2))
                self.w["r2"].setValue(round(zrange[1], 2))
            self.lbl.setText(" ")
            self.btn_keep.setEnabled(False)
        self._finished = False
        self._method()

    def _grid_mode(self):
        if self.w["grid_mode"].currentData() == "mad":
            self.w["grid_val"].setSuffix(" σ")
            self.w["grid_val"].setValue(6.0)
        else:
            self.w["grid_val"].setSuffix(" m")
            self.w["grid_val"].setValue(0.30)

    def _method(self):
        m = self.cmb.currentData()
        self.pages.setCurrentIndex(list(METHODS).index(m))
        self.help.setText(HELP[m])
        no_surf = ((m == "surface" and self.w["surf"].count() == 0)
                   or (m == "cube" and self.w["cube_dis"].currentData() == "prior" and self.w["cube_surf"].count() == 0)
                   or (m == "iho" and self.w["iho_ref"].currentData() == "surface" and self.w["iho_surf"].count() == 0))
        self.w["cube_surf"].setEnabled(self.w["cube_dis"].currentData() == "prior")
        self.w["iho_surf"].setEnabled(self.w["iho_ref"].currentData() == "surface")
        self.w["iho_cell"].setEnabled(self.w["iho_ref"].currentData() == "local")
        self.w["iho_unc"].setEnabled(self.w["iho_mode"].currentData() != "residual")
        self.btn_preview.setEnabled(not no_surf)
        if no_surf:
            self.lbl.setText(f"<span style='color:{C['danger']}'>●  Create or load a TIN / DEM first</span>")

    def params(self):
        m = self.cmb.currentData()
        w = self.w
        if m == "sor":
            return m, dict(k=w["sor_k"].value(), n_std=w["sor_n"].value())
        if m == "radius":
            return m, dict(radius=w["rad_r"].value(), min_neighbors=w["rad_n"].value())
        if m == "grid":
            return m, dict(cell=w["grid_cell"].value(), mode=w["grid_mode"].currentData(),
                           value=w["grid_val"].value(), side=w["grid_side"].currentData())
        if m == "surface":
            return m, dict(surface_id=w["surf"].currentData(), above=w["surf_above"].value(),
                           below=w["surf_below"].value())
        if m == "cube":
            d = dict(resolution=w["cube_res"].value(), capture_pct=w["cube_cap"].value(), k_h=w["cube_kh"].value(),
                     k_f=w["cube_kf"].value(), disambiguation=w["cube_dis"].currentData(),
                     add_layers=w["cube_layers"].isChecked(), unc=w["cube_unc"].spec())
            if d["disambiguation"] == "prior":
                d["surface_id"] = w["cube_surf"].currentData()
            return m, d
        if m == "iho":
            d = dict(order=w["iho_order"].currentData(), mode=w["iho_mode"].currentData(),
                     reference=w["iho_ref"].currentData(), cell=w["iho_cell"].value(), unc=w["iho_unc"].spec())
            if d["reference"] == "surface":
                d["surface_id"] = w["iho_surf"].currentData()
            return m, d
        if m == "local":
            return m, dict(k=w["loc_k"].value(), n_sigma=w["loc_n"].value(), min_sigma=w["loc_min"].value(),
                           side=w["loc_side"].currentData())
        if m == "cluster":
            return m, dict(radius=w["cl_r"].value(), min_points=w["cl_n"].value())
        if m == "photo":
            return m, dict(use_views=w["ph_views"].isChecked(), min_views=w["ph_minv"].value(),
                           use_distance=w["ph_dist"].isChecked(), k=w["ph_k"].value(), n_sigma=w["ph_n"].value(),
                           min_sigma=w["ph_min"].value(), use_free=w["ph_free"].isChecked(),
                           use_sky=w["ph_sky"].isChecked(), cameras=w["ph_cams"].currentData())
        a, b = w["r1"].value(), w["r2"].value()
        zmin, zmax = (-b, -a) if self.depth else (a, b)
        return m, dict(zmin=min(zmin, zmax), zmax=max(zmin, zmax))

    def scope(self):
        return next(k for k, rb in self.rb.items() if rb.isChecked())

    # ---------- actions ----------
    def problem(self):
        m = self.cmb.currentData()
        if m == "cube":
            return self.w["cube_unc"].problem()
        if m == "iho" and self.w["iho_mode"].currentData() != "residual":
            return self.w["iho_unc"].problem()
        return None

    def _preview(self):
        err = self.problem()
        if err:
            self.lbl.setText(f"<span style='color:{C['danger']}'>●  {err}</span>")
            return
        m, p = self.params()
        self.lbl.setText(f"<span style='color:{C['muted']}'>Running…</span>")
        self.cb.filter_run(m, p, self.scope(), self.cmb_op.currentData())

    def show_result(self, n, n_scope, info):
        pct = n / max(n_scope, 1)
        color = C["success"] if n else C["muted"]
        self.lbl.setText(f"<span style='color:{color}'>●</span>  <b>{n:,}</b> candidates "
                         f"({pct:.2%} of {n_scope:,} points)<br><span style='color:{C['muted']}'>{info}</span>")
        self.btn_keep.setEnabled(True)

    def _finish(self, keep):
        self._finished = True
        self.cb.filter_finish(keep)
        self.hide()
