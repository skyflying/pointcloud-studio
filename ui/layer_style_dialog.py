"""Colour and text label of a route / vector / structure layer."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout,
)

from core import layer_style as ls
from ui.theme import dark_titlebar


class LayerStyleDialog(QDialog):
    def __init__(self, ds, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Style — {ds.name}")
        self.kind = ds.kind
        st = dict(ds.style or {})
        self.color = QColor(ls.color_of(ds.kind, st, ds.obj))
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        t = QLabel(ds.name)
        t.setObjectName("title")
        v.addWidget(t)
        f = QFormLayout()
        row = QHBoxLayout()
        self.btn = QPushButton()
        self.btn.setFixedWidth(120)
        self.btn.clicked.connect(self._pick)
        rst = QPushButton("Default")
        rst.clicked.connect(lambda: self._set(QColor(ls.color_of(ds.kind, {}, ds.obj))))
        row.addWidget(self.btn)
        row.addWidget(rst)
        row.addStretch(1)
        f.addRow("Colour", row)
        self.cmb = QComboBox()
        for key, lab in ls.label_fields(ds.kind, ds.obj):
            self.cmb.addItem(lab, key)
        j = self.cmb.findData(ls.label_key(ds.kind, st))
        self.cmb.setCurrentIndex(max(j, 0))
        f.addRow("Text label", self.cmb)
        self.chk_kp = None
        if ds.kind == "route":
            self.chk_kp = QCheckBox("Show KP labels along the route")
            self.chk_kp.setChecked(st.get("kp", True))
            f.addRow(self.chk_kp)
        v.addLayout(f)
        if self.cmb.count() <= 1 and ds.kind == "vector":
            n = QLabel("This layer has no attributes to show as text.")
            n.setObjectName("muted")
            v.addWidget(n)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._set(self.color)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _set(self, c):
        self.color = QColor(c)
        txt = "#000000" if self.color.lightnessF() > 0.6 else "#ffffff"
        self.btn.setText(self.color.name())
        self.btn.setStyleSheet(f"background:{self.color.name()}; color:{txt}; border-radius:4px; padding:4px")

    def _pick(self):
        c = QColorDialog.getColor(self.color, self, "Layer colour")
        if c.isValid():
            self._set(c)

    def style(self):
        st = {"color": self.color.name(), "label": self.cmb.currentData()}
        if self.chk_kp is not None:
            st["kp"] = self.chk_kp.isChecked()
        return st
