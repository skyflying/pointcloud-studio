"""Auto-filter dialog (non-modal): choose method + parameters + scope, Preview, then keep or cancel."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
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
        self.resize(500, 640)
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
        v.addWidget(self.pages)

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

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def closeEvent(self, e):
        if not self._finished:
            self._finish(False)
        super().closeEvent(e)

    # ---------- context ----------
    def set_context(self, counts, surfaces, has_subset, depth, spacing, zrange, scope=None):
        """counts: dict(all, selected, subset); surfaces: [(id, name)]; zrange: (zmin, zmax)."""
        for k, label in (("all", "All active points"), ("selected", "Selected points"), ("subset", "Current subset")):
            self.rb[k].setText(f"{label}   ({counts.get(k, 0):,})")
        self.rb["selected"].setEnabled(counts.get("selected", 0) > 0)
        self.rb["subset"].setEnabled(has_subset and counts.get("subset", 0) > 0)
        if scope and self.rb[scope].isEnabled():
            self.rb[scope].setChecked(True)
        elif not self.grp.checkedButton().isEnabled():
            self.rb["all"].setChecked(True)
        cur = self.w["surf"].currentData()
        self.w["surf"].clear()
        for sid, name in surfaces:
            self.w["surf"].addItem(name, sid)
        i = self.w["surf"].findData(cur)
        self.w["surf"].setCurrentIndex(max(i, 0))
        self.depth = depth
        self.zrange = zrange
        if self._finished:                                # fresh session: data-scaled defaults
            self.w["grid_cell"].setValue(nice_number(max(spacing * 2.5, 0.05)))
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
        no_surf = m == "surface" and self.w["surf"].count() == 0
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
        a, b = w["r1"].value(), w["r2"].value()
        zmin, zmax = (-b, -a) if self.depth else (a, b)
        return m, dict(zmin=min(zmin, zmax), zmax=max(zmin, zmax))

    def scope(self):
        return next(k for k, rb in self.rb.items() if rb.isChecked())

    # ---------- actions ----------
    def _preview(self):
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
