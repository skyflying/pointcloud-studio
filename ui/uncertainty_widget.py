"""Widget to choose where the vertical (TVU) and horizontal (THU) uncertainty of the points come from."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QStackedWidget, QWidget,
)

from core.s44 import ORDER_LABEL

SOURCES = (("attribute", "Point attribute (TPU / TVU / THU column)"), ("table", "XYZ / CSV file (x, y, value)"),
           ("raster", "Raster (GeoTIFF / BAG band)"), ("iho", "IHO S-44 order model"), ("constant", "Constant"))


def _muted(t):
    l = QLabel(t)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


class _OneSource(QWidget):
    def __init__(self, kind, allow_none=False, parent=None):
        super().__init__(parent)
        self.kind = kind
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        self.cmb = QComboBox()
        if allow_none:
            self.cmb.addItem("Not used", None)
        for k, t in SOURCES:
            self.cmb.addItem(t, k)
        f.addRow(_muted(kind.upper()), self.cmb)
        self.stack = QStackedWidget()
        # attribute
        self.attr = QComboBox()
        self.stack.addWidget(self.attr)
        # table
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self.tab_path = QLineEdit()
        self.tab_path.setPlaceholderText("file with x, y, value columns")
        b = QPushButton("…")
        b.setFixedWidth(30)
        b.clicked.connect(self._pick_table)
        self.tab_col = QSpinBox(minimum=3, maximum=50, value=3)
        self.tab_col.setPrefix("col ")
        self.tab_col.setToolTip("Column (1-based) holding the value; x and y are columns 1 and 2")
        self.tab_dist = QDoubleSpinBox(minimum=0, maximum=1e5, decimals=2, value=5.0, singleStep=1)
        self.tab_dist.setSuffix(" m")
        self.tab_dist.setToolTip("Maximum distance from a table position (0 = no limit)")
        for x in (self.tab_path, b, self.tab_col, self.tab_dist):
            h.addWidget(x, 1 if x is self.tab_path else 0)
        self.stack.addWidget(w)
        # raster
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self.ras_path = QLineEdit()
        self.ras_path.setPlaceholderText("GeoTIFF / BAG")
        b = QPushButton("…")
        b.setFixedWidth(30)
        b.clicked.connect(self._pick_raster)
        self.ras_band = QComboBox()
        for x in (self.ras_path, b, self.ras_band):
            h.addWidget(x, 1 if x is self.ras_path else 0)
        self.stack.addWidget(w)
        # iho
        self.order = QComboBox()
        for k, t in ORDER_LABEL.items():
            self.order.addItem(t, k)
        self.order.setCurrentIndex(2)
        self.stack.addWidget(self.order)
        # constant
        self.const = QDoubleSpinBox(minimum=0.0, maximum=100, decimals=3, value=0.15 if kind == "tvu" else 0.5,
                                    singleStep=0.01)
        self.const.setSuffix(" m")
        self.stack.addWidget(self.const)
        self.stack.addWidget(QWidget())                  # "not used"
        f.addRow("", self.stack)
        self.conf = QComboBox()
        self.conf.addItem("Values are at 95 % confidence (TPU as reported by CARIS / Qimera)", "95")
        self.conf.addItem("Values are 1σ (standard uncertainty)", "1s")
        f.addRow("", self.conf)
        self.cmb.currentIndexChanged.connect(self._changed)
        self._changed()

    def _changed(self):
        k = self.cmb.currentData()
        page = {"attribute": 0, "table": 1, "raster": 2, "iho": 3, "constant": 4, None: 5}[k]
        self.stack.setCurrentIndex(page)
        self.conf.setVisible(k in ("attribute", "table", "raster", "constant"))

    def _pick_table(self):
        p, _ = QFileDialog.getOpenFileName(self, "Uncertainty table", "", "Text (*.xyz *.txt *.csv *.XYZ *.TXT *.CSV);;All (*)")
        if p:
            self.tab_path.setText(p)

    def _pick_raster(self):
        p, _ = QFileDialog.getOpenFileName(self, "Uncertainty raster", "",
                                           "Rasters (*.tif *.tiff *.bag *.TIF *.TIFF *.BAG);;All (*)")
        if not p:
            return
        self.ras_path.setText(p)
        self.ras_band.clear()
        try:
            from core.uncertainty import raster_bands
            for i, n in enumerate(raster_bands(p)):
                self.ras_band.addItem(n, i + 1)
            # BAG: band 2 is the uncertainty
            low = [self.ras_band.itemText(i).lower() for i in range(self.ras_band.count())]
            j = next((i for i, n in enumerate(low) if "uncert" in n), 1 if p.lower().endswith(".bag") else 0)
            self.ras_band.setCurrentIndex(min(j, self.ras_band.count() - 1))
        except Exception as e:  # noqa: BLE001
            self.ras_band.addItem(f"cannot read: {type(e).__name__}", 1)

    def set_attributes(self, names):
        from core.uncertainty import guess_attribute
        cur = self.attr.currentText()
        self.attr.clear()
        num = [n for n in names if n not in ("R", "G", "B", "Classification")]
        self.attr.addItems(num)
        g = guess_attribute(num, self.kind)
        if cur in num:
            self.attr.setCurrentText(cur)
        elif g:
            self.attr.setCurrentText(g)
            self.cmb.setCurrentIndex(self.cmb.findData("attribute"))
        elif self.cmb.currentData() == "attribute":
            self.cmb.setCurrentIndex(self.cmb.findData("iho"))

    def spec(self):
        k = self.cmb.currentData()
        if k is None:
            return None
        s = {"source": k, "confidence": self.conf.currentData()}
        if k == "attribute":
            s["attribute"] = self.attr.currentText()
        elif k == "table":
            s.update(path=self.tab_path.text(), column=self.tab_col.value() - 1, max_dist=self.tab_dist.value())
        elif k == "raster":
            s.update(path=self.ras_path.text(), band=int(self.ras_band.currentData() or 1))
        elif k == "iho":
            s["order"] = self.order.currentData()
        else:
            s["value"] = self.const.value()
        return s

    def problem(self):
        k = self.cmb.currentData()
        if k == "attribute" and not self.attr.currentText():
            return f"{self.kind.upper()}: the cloud has no numeric attribute to use"
        if k in ("table", "raster"):
            p = self.tab_path.text() if k == "table" else self.ras_path.text()
            if not p or not os.path.isfile(p):
                return f"{self.kind.upper()}: choose the {'table' if k == 'table' else 'raster'} file"
        return None


class UncertaintyWidget(QWidget):
    """TVU (required) and THU (optional) sources + the order used where a value is missing."""

    def __init__(self, thu=True, parent=None):
        super().__init__(parent)
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        self.tvu = _OneSource("tvu")
        f.addRow(self.tvu)
        self.thu = _OneSource("thu", allow_none=True) if thu else None
        if self.thu is not None:
            f.addRow(self.thu)
        self.fill = QComboBox()
        for k, t in ORDER_LABEL.items():
            self.fill.addItem(t, k)
        self.fill.setCurrentIndex(2)
        f.addRow(_muted("Points without a value use"), self.fill)

    def set_attributes(self, names):
        self.tvu.set_attributes(names)
        if self.thu is not None:
            self.thu.set_attributes(names)

    def spec(self):
        return {"tvu": self.tvu.spec(), "thu": self.thu.spec() if self.thu is not None else None,
                "fill_order": self.fill.currentData()}

    def problem(self):
        return self.tvu.problem() or (self.thu.problem() if self.thu is not None else None)
