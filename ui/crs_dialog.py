"""EPSG coordinate-system picker."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from core import crs as crsmod
from ui.theme import C, dark_titlebar


class CrsDialog(QDialog):
    """CrsDialog.ask(parent, title, message, initial, allow_unknown) → (ok, crs_or_None)"""

    def __init__(self, title, message, initial=None, allow_unknown=True, warn_geographic=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 560)
        self.warn_geographic = warn_geographic
        self._crs = initial
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("title")
        v.addWidget(t)
        if message:
            m = QLabel(message)
            m.setObjectName("muted")
            m.setWordWrap(True)
            v.addWidget(m)
        self.ed = QLineEdit()
        self.ed.setPlaceholderText("Search EPSG code or name, e.g. 3826 or “TWD97 121” or “UTM 51N”")
        v.addWidget(self.ed)
        self.lst = QListWidget()
        v.addWidget(self.lst, 1)
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        self.chk_unknown = QCheckBox("Unknown / local coordinates (no reprojection possible)")
        self.chk_unknown.setVisible(allow_unknown)
        v.addWidget(self.chk_unknown)
        row = QHBoxLayout()
        row.addStretch()
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        self.ok = QPushButton("OK")
        self.ok.setProperty("primary", True)
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        row.addWidget(b)
        row.addWidget(self.ok)
        v.addLayout(row)

        self._timer = QTimer(self, singleShot=True, interval=250, timeout=self._search)
        self.ed.textChanged.connect(lambda _: self._timer.start())
        self.lst.currentItemChanged.connect(self._pick)
        self.lst.itemDoubleClicked.connect(lambda _: self.accept())
        self.chk_unknown.toggled.connect(self._update)
        if initial is not None and crsmod.epsg_of(initial):
            self.ed.setText(str(crsmod.epsg_of(initial)))
        self._search()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _search(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rows = crsmod.search(self.ed.text())
        finally:
            QApplication.restoreOverrideCursor()
        self.lst.clear()
        for code, name, kind in rows:
            it = QListWidgetItem(f"EPSG:{code}   {name}" + ("   (lat/lon)" if kind == "geographic" else ""))
            it.setData(Qt.UserRole, code)
            self.lst.addItem(it)
        if self.lst.count():
            self.lst.setCurrentRow(0)
        self._update()

    def _pick(self, it, _prev=None):
        if it is not None:
            try:
                self._crs = crsmod.from_epsg(it.data(Qt.UserRole))
            except Exception:
                self._crs = None
        self._update()

    def _update(self):
        unknown = self.chk_unknown.isChecked()
        self.lst.setEnabled(not unknown)
        self.ed.setEnabled(not unknown)
        if unknown:
            self.lbl.setText(f"<span style='color:{C['muted']}'>Coordinates are used as they are.</span>")
            self.ok.setEnabled(True)
            return
        if self._crs is None:
            self.lbl.setText(f"<span style='color:{C['danger']}'>Select a coordinate system</span>")
            self.ok.setEnabled(False)
            return
        txt = f"<b>{crsmod.label(self._crs)}</b>"
        if self.warn_geographic and crsmod.is_geographic(self._crs):
            txt += (f"<br><span style='color:{C['danger']}'>Latitude/longitude is not suitable as the project "
                    "system (distances, volumes in metres) — choose a projected system such as TWD97.</span>")
        self.lbl.setText(txt)
        self.ok.setEnabled(True)

    def result_crs(self):
        return None if self.chk_unknown.isChecked() else self._crs

    @classmethod
    def ask(cls, parent, title, message="", initial=None, allow_unknown=True, warn_geographic=False):
        dlg = cls(title, message, initial, allow_unknown, warn_geographic, parent)
        if dlg.exec() != QDialog.Accepted:
            return False, None
        return True, dlg.result_crs()
