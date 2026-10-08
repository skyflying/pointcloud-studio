"""Create-TIN and Create-DEM dialogs."""
from __future__ import annotations

import math

from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from core.surface import DEM_METHODS, THIN_MODES, grid_extent, nice_number
from ui.theme import C, dark_titlebar

WARN_TRIANGLES = 6_000_000
WARN_CELLS = 50_000_000


def _section(text):
    lbl = QLabel(text.upper())
    lbl.setObjectName("sectionTitle")
    return lbl


def _muted(text):
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl


def _dspin(value, lo, hi, step, dec=2, suffix=" m"):
    sp = QDoubleSpinBox(minimum=lo, maximum=hi, singleStep=step, decimals=dec)
    sp.setValue(value)
    sp.setSuffix(suffix)
    return sp


class _BaseDialog(QDialog):
    def __init__(self, title, subtitle, counts, parent):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.counts = counts
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 18, 22, 18)
        self.lay.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("title")
        self.lay.addWidget(t)
        self.lay.addWidget(_muted(subtitle))
        self.lay.addSpacing(4)
        self.lay.addWidget(_section("Source points"))
        self.grp = QButtonGroup(self)
        self.rb_active = QRadioButton(f"Active points   ({counts['active']:,})")
        self.rb_sel = QRadioButton(f"Selected points only   ({counts['selected']:,})")
        self.rb_sel.setEnabled(counts["selected"] >= 3)
        self.rb_active.setChecked(True)
        for rb in (self.rb_active, self.rb_sel):
            self.grp.addButton(rb)
            self.lay.addWidget(rb)
        self.grp.buttonToggled.connect(lambda *_: self._update())

    def _finish(self, ok_text):
        self.lay.addStretch()
        self.lbl_est = QLabel()
        self.lbl_est.setWordWrap(True)
        self.lay.addWidget(self.lbl_est)
        foot = QHBoxLayout()
        foot.addStretch()
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        self.btn_ok = QPushButton(ok_text)
        self.btn_ok.setProperty("primary", True)
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        foot.addWidget(b)
        foot.addWidget(self.btn_ok)
        self.lay.addLayout(foot)
        self._update()

    def source(self):
        return "selected" if self.rb_sel.isChecked() else "active"

    def n_source(self):
        return self.counts[self.source()]

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _est(self, ok, text):
        color = C["success"] if ok else C["danger"]
        self.lbl_est.setText(f"<span style='color:{color}'>●</span>  {text}")


class TinDialog(_BaseDialog):
    def __init__(self, counts, spacing, area, parent=None):
        super().__init__("Create TIN", "Delaunay triangulation of the points in plan view (2.5D).",
                         counts, parent)
        self.spacing, self.area = spacing, area
        self.lay.addSpacing(4)
        self.lay.addWidget(_section("Options"))
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.sp_edge = _dspin(nice_number(spacing * 8), 0, 1e6, 0.5)
        self.sp_edge.setSpecialValueText("No limit")
        self.sp_edge.setToolTip("Triangles with any edge longer than this are removed, so the TIN does "
                                "not bridge data gaps or stretch along the outer boundary.")
        form.addRow(_muted("Max edge length"), self.sp_edge)
        self.chk_thin = QCheckBox("Thin points to one per grid cell first")
        self.sp_thin = _dspin(nice_number(spacing * 2), 0.01, 1e5, 0.1)
        self.cmb_thin = QComboBox()
        for k, v in THIN_MODES.items():
            self.cmb_thin.addItem(v, k)
        big = counts["active"] > 3_000_000
        self.chk_thin.setChecked(big)
        if big:
            self.sp_thin.setValue(nice_number(math.sqrt(area / 2_000_000)))
        form.addRow(_muted(""), self.chk_thin)
        form.addRow(_muted("Cell size"), self.sp_thin)
        form.addRow(_muted("Keep point"), self.cmb_thin)
        self.lay.addLayout(form)
        self.lay.addWidget(_muted(f"Average point spacing ≈ {spacing:.2f} m. Thinning keeps real survey "
                                  "points (no averaging); “Max Z” keeps the shallowest point per cell."))
        for w in (self.sp_edge, self.sp_thin):
            w.valueChanged.connect(self._update)
        self.chk_thin.toggled.connect(self._update)
        self._finish("Create TIN")

    def _update(self):
        thin = self.chk_thin.isChecked()
        self.sp_thin.setEnabled(thin)
        self.cmb_thin.setEnabled(thin)
        n = self.n_source()
        if thin:
            n = min(n, int(self.area / self.sp_thin.value() ** 2) + 1)
        tris = 2 * n
        ok = tris <= WARN_TRIANGLES
        msg = f"≈ {n:,} vertices → ≈ {tris:,} triangles"
        if not ok:
            msg += " — large; thinning is recommended (slow, high memory)"
        self._est(ok, msg)

    def params(self):
        return dict(source=self.source(), max_edge=self.sp_edge.value(),
                    thin_cell=self.sp_thin.value() if self.chk_thin.isChecked() else 0.0,
                    thin_mode=self.cmb_thin.currentData())


class DemDialog(_BaseDialog):
    def __init__(self, counts, spacing, bbox, tin_state, parent=None, *, zrange=(-1.0, 0.0)):
        """tin_state: None (no TIN) | 'ok' | 'outdated'"""
        super().__init__("Create DEM", "Regular elevation grid from the points.", counts, parent)
        self.bbox = bbox
        self.lay.addSpacing(4)
        self.lay.addWidget(_section("Grid"))
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        self.sp_cell = _dspin(nice_number(max(spacing * 2, 0.01)), 0.01, 1e5, 0.1)
        self.cmb_method = QComboBox()
        for k, v in DEM_METHODS.items():
            self.cmb_method.addItem(v, k)
        self.chk_snap = QCheckBox("Snap grid origin to a multiple of the cell size")
        self.chk_snap.setChecked(True)
        self.sp_fill = QSpinBox(minimum=0, maximum=100000, value=4)
        self.sp_fill.setSpecialValueText("Off")
        self.sp_fill.setSuffix(" cells")
        self.sp_fill.setToolTip("Interior NoData holes up to this size are filled with the nearest value")
        form.addRow(_muted("Cell size"), self.sp_cell)
        form.addRow(_muted("Method"), self.cmb_method)
        form.addRow(_muted("Fill holes up to"), self.sp_fill)
        form.addRow(_muted(""), self.chk_snap)
        self.lay.addLayout(form)

        # method-specific options
        self.box_tin = QWidget()
        f1 = QFormLayout(self.box_tin)
        f1.setContentsMargins(0, 0, 0, 0)
        f1.setHorizontalSpacing(14)
        self.chk_use_tin = QCheckBox("Use the existing TIN layer")
        self.chk_use_tin.setEnabled(tin_state is not None)
        self.chk_use_tin.setChecked(tin_state == "ok")
        if tin_state == "outdated":
            self.chk_use_tin.setText("Use the existing TIN layer (out of date)")
        self.sp_edge = _dspin(nice_number(spacing * 8), 0, 1e6, 0.5)
        self.sp_edge.setSpecialValueText("No limit")
        f1.addRow(_muted(""), self.chk_use_tin)
        f1.addRow(_muted("Max edge length"), self.sp_edge)
        self.box_idw = QWidget()
        f2 = QFormLayout(self.box_idw)
        f2.setContentsMargins(0, 0, 0, 0)
        f2.setHorizontalSpacing(14)
        self.sp_pow = _dspin(2.0, 0.5, 6, 0.5, 1, "")
        self.sp_rad = _dspin(0.0, 0, 1e5, 0.5)
        self.sp_rad.setSpecialValueText("Auto (3 × cell)")
        self.sp_k = QSpinBox(minimum=1, maximum=64, value=12)
        f2.addRow(_muted("Power"), self.sp_pow)
        f2.addRow(_muted("Search radius"), self.sp_rad)
        f2.addRow(_muted("Neighbours"), self.sp_k)
        self.box_shoal = QWidget()
        f3 = QFormLayout(self.box_shoal)
        f3.setContentsMargins(0, 0, 0, 0)
        f3.setHorizontalSpacing(14)
        self.cmb_shoal = QComboBox()
        self.cmb_shoal.addItem("Highest Z  (Z up, depths negative)", True)
        self.cmb_shoal.addItem("Lowest Z  (Z is positive depth)", False)
        # all-positive Z in a hydrographic context usually means depths stored positive-down
        self.cmb_shoal.setCurrentIndex(1 if zrange[0] > 0 else 0)
        f3.addRow(_muted("Shoalest ="), self.cmb_shoal)
        self.lay.addWidget(self.box_tin)
        self.lay.addWidget(self.box_idw)
        self.lay.addWidget(self.box_shoal)
        self.lbl_help = _muted("")
        self.lay.addWidget(self.lbl_help)

        for w in (self.sp_cell,):
            w.valueChanged.connect(self._update)
        self.cmb_method.currentIndexChanged.connect(self._on_method)
        self._last_method = self.cmb_method.currentData()
        self.chk_use_tin.toggled.connect(self._update)
        self.chk_snap.toggled.connect(self._update)
        self._finish("Create DEM")

    HELP = {
        "tin": "Linear interpolation on the TIN: continuous surface, no averaging. Best for design surfaces.",
        "mean": "Average Z of the points inside each cell. Cells without points are NoData.",
        "median": "Median Z per cell — robust against remaining spikes.",
        "min": "Deepest point per cell (min Z).",
        "max": "Shallowest point per cell (max Z) — conservative for clearance checks.",
        "count": "Number of points per cell — useful for coverage / density QA.",
        "shoal": "Shoal-biased: each cell keeps its shoalest real sounding at its TRUE position (not the "
                 "cell centre) — nothing is averaged, smoothed or moved, so the least depth is never lost. "
                 "XYZ export writes the true X/Y of each selected sounding.",
        "idw": "Inverse-distance weighting of nearby points; smooth, fills small gaps.",
    }

    def _on_method(self):
        m = self.cmb_method.currentData()
        if m == "shoal":            # never invent depths in a shoal-biased grid unless asked to
            self.sp_fill.setValue(0)
        elif self._last_method == "shoal" and self.sp_fill.value() == 0:
            self.sp_fill.setValue(4)
        self._last_method = m
        self._update()

    def _update(self):
        m = self.cmb_method.currentData()
        self.box_tin.setVisible(m == "tin")
        self.box_idw.setVisible(m == "idw")
        self.box_shoal.setVisible(m == "shoal")
        self.sp_edge.setEnabled(not self.chk_use_tin.isChecked())
        self.sp_fill.setEnabled(m != "count")
        self.lbl_help.setText(self.HELP[m])
        x0, ytop, nr, nc = grid_extent(*self.bbox, self.sp_cell.value(), self.chk_snap.isChecked())
        cells = nr * nc
        ok = cells <= WARN_CELLS
        msg = f"Grid {nc:,} × {nr:,} = {cells:,} cells"
        if not ok:
            msg += " — very large; use a bigger cell size"
        self._est(ok, msg)
        self.btn_ok.setEnabled(cells <= 4 * WARN_CELLS)

    def params(self):
        m = self.cmb_method.currentData()
        return dict(source=self.source(), cell=self.sp_cell.value(), method=m,
                    use_tin=m == "tin" and self.chk_use_tin.isChecked(), max_edge=self.sp_edge.value(),
                    idw_power=self.sp_pow.value(), idw_radius=self.sp_rad.value(), idw_k=self.sp_k.value(),
                    fill=self.sp_fill.value() if m != "count" else 0, snap=self.chk_snap.isChecked(),
                    shoal_high=self.cmb_shoal.currentData())
