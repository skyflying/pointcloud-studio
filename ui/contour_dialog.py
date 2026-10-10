"""Contour creation dialog."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from core.surface import nice_number
from ui.theme import dark_titlebar


def _muted(t):
    l = QLabel(t)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


class ContourDialog(QDialog):
    def __init__(self, surfaces, zrange, depth, parent=None):
        """surfaces: [(id, name, is_tin)]"""
        super().__init__(parent)
        self.setWindowTitle("Contours")
        self.depth = depth
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        t = QLabel("Contours")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Contour lines from a DEM or TIN. Every vertex keeps its elevation (3D lines); "
                           "export as Shapefile (PolyLineZ) or DXF (3D polylines) from the Data panel."))
        f = QFormLayout()
        f.setHorizontalSpacing(14)
        f.setVerticalSpacing(8)
        self.cmb = QComboBox()
        for sid, name, is_tin in surfaces:
            self.cmb.addItem(name, (sid, is_tin))
        f.addRow(_muted("Surface"), self.cmb)
        span = max(zrange[1] - zrange[0], 1e-3)
        self.sp_int = QDoubleSpinBox(minimum=0.01, maximum=1e4, decimals=2, singleStep=0.25,
                                     value=nice_number(span / 15))
        self.sp_int.setSuffix(" m")
        f.addRow(_muted("Interval"), self.sp_int)
        self.sp_base = QDoubleSpinBox(minimum=-1e5, maximum=1e5, decimals=2, value=0.0)
        self.sp_base.setSuffix(" m")
        self.sp_base.setToolTip("Contours are drawn at base + k × interval")
        f.addRow(_muted("Base"), self.sp_base)
        self.sp_idx = QSpinBox(minimum=0, maximum=100, value=5)
        self.sp_idx.setSpecialValueText("none")
        self.sp_idx.setToolTip("Every N-th contour is a major (index) contour — own layer / attribute")
        f.addRow(_muted("Major every"), self.sp_idx)
        self.sp_smooth = QDoubleSpinBox(minimum=0.0, maximum=50, decimals=1, singleStep=0.5, value=0.0)
        self.sp_smooth.setSuffix(" cells")
        self.sp_smooth.setSpecialValueText("off")
        self.sp_smooth.setToolTip("Gaussian smoothing of the grid before contouring (cartographic contours)")
        f.addRow(_muted("Smoothing"), self.sp_smooth)
        self.sp_simp = QDoubleSpinBox(minimum=0.0, maximum=1e3, decimals=3, singleStep=0.05, value=0.0)
        self.sp_simp.setSuffix(" m")
        self.sp_simp.setSpecialValueText("off")
        self.sp_simp.setToolTip("Douglas–Peucker tolerance: removes vertices closer than this to the line")
        f.addRow(_muted("Simplify"), self.sp_simp)
        self.sp_min = QDoubleSpinBox(minimum=0.0, maximum=1e5, decimals=1, singleStep=1, value=0.0)
        self.sp_min.setSuffix(" m")
        self.sp_min.setSpecialValueText("keep all")
        self.sp_min.setToolTip("Drop short lines (noise rings) below this length")
        f.addRow(_muted("Min. length"), self.sp_min)
        self.sp_cell = QDoubleSpinBox(minimum=0.05, maximum=1e3, decimals=2, value=1.0)
        self.sp_cell.setSuffix(" m")
        self.lbl_cell = _muted("TIN grid cell")
        f.addRow(self.lbl_cell, self.sp_cell)
        v.addLayout(f)
        self.lbl = _muted("")
        v.addWidget(self.lbl)
        row = QHBoxLayout()
        row.addStretch()
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        ok = QPushButton("Create")
        ok.setProperty("primary", True)
        ok.clicked.connect(self.accept)
        row.addWidget(b)
        row.addWidget(ok)
        v.addLayout(row)
        self.zrange = zrange
        self.cmb.currentIndexChanged.connect(self._update)
        self.sp_int.valueChanged.connect(self._update)
        self.sp_base.valueChanged.connect(self._update)
        self._update()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _update(self):
        d = self.cmb.currentData()
        tin = bool(d and d[1])
        self.sp_cell.setVisible(tin)
        self.lbl_cell.setVisible(tin)
        import math
        lo, hi = self.zrange
        b, i = self.sp_base.value(), self.sp_int.value()
        n = max(0, math.floor((hi - b) / i) - math.ceil((lo - b) / i) + 1)
        if self.depth:
            self.lbl.setText(f"≈ {n} contour levels between depth {-hi:.2f} and {-lo:.2f} m")
        else:
            self.lbl.setText(f"≈ {n} contour levels between {lo:.2f} and {hi:.2f} m")

    def params(self):
        sid, is_tin = self.cmb.currentData()
        return sid, dict(interval=self.sp_int.value(), base=self.sp_base.value(), index_every=self.sp_idx.value(),
                         cell=self.sp_cell.value() if is_tin else None, smooth=self.sp_smooth.value(),
                         simplify_tol=self.sp_simp.value(), min_length=self.sp_min.value())


class LabelDialog(QDialog):
    """DXF contour label options."""

    def __init__(self, cs, depth, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DXF contour labels")
        lo, hi = cs.bbox()
        extent = float(max(hi[0] - lo[0], hi[1] - lo[1], 1.0))
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        t = QLabel("DXF contour labels")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Depth / elevation text along the contours (layer CONTOUR_LABEL), placed at the "
                           "contour's elevation, rotated along the line and kept upright."))
        f = QFormLayout()
        f.setHorizontalSpacing(14)
        self.cmb_which = QComboBox()
        self.cmb_which.addItem("Major contours", "major")
        self.cmb_which.addItem("All contours", "all")
        self.cmb_which.addItem("No labels", "none")
        if not cs.major.any():
            self.cmb_which.setCurrentIndex(1)
        f.addRow(_muted("Label"), self.cmb_which)
        self.cmb_val = QComboBox()
        self.cmb_val.addItem("Depth (positive down)", True)
        self.cmb_val.addItem("Elevation (Z)", False)
        self.cmb_val.setCurrentIndex(0 if depth else 1)
        f.addRow(_muted("Value"), self.cmb_val)
        import math
        dec = max(0, -math.floor(math.log10(cs.interval) + 1e-9)) + (1 if round(cs.interval / 10 ** math.floor(
            math.log10(cs.interval)), 6) not in (1.0, 2.0, 5.0) else 0)
        self.sp_dec = QSpinBox(minimum=0, maximum=4, value=min(dec, 3))
        f.addRow(_muted("Decimals"), self.sp_dec)
        def nice_down(x):                      # 1 / 2 / 2.5 / 5 × 10^n, not larger than x
            import math as _m
            mag = 10 ** _m.floor(_m.log10(max(x, 1e-9)))
            return max(m * mag for m in (1, 2, 2.5, 5) if m * mag <= x * 1.0001)
        self.sp_h = QDoubleSpinBox(minimum=0.01, maximum=1e4, decimals=2, value=max(nice_down(extent / 200), 0.2))
        self.sp_h.setSuffix(" m")
        self.sp_h.setToolTip("Text height in map units (metres)")
        f.addRow(_muted("Text height"), self.sp_h)
        self.sp_sp = QDoubleSpinBox(minimum=1.0, maximum=1e6, decimals=1, value=nice_number(extent / 4))
        self.sp_sp.setSuffix(" m")
        self.sp_sp.setToolTip("Distance between labels along a contour")
        f.addRow(_muted("Spacing"), self.sp_sp)
        v.addLayout(f)
        row = QHBoxLayout()
        row.addStretch()
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        ok = QPushButton("Export")
        ok.setProperty("primary", True)
        ok.clicked.connect(self.accept)
        row.addWidget(b)
        row.addWidget(ok)
        v.addLayout(row)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def options(self):
        return dict(which=self.cmb_which.currentData(), height=self.sp_h.value(), spacing=self.sp_sp.value(),
                    depth=bool(self.cmb_val.currentData()), decimals=self.sp_dec.value())
