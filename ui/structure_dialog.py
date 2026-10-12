"""Import structures (wind turbines, substations, piles): CSV / TXT column mapping or Shapefile points."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from core import crs as crsmod
from core import structures as stmod
from fileio import route_reader as rr
from fileio.xyz_reader import DELIMITERS, read_preview, split_line
from ui.crs_dialog import CrsDialog
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


class StructureImportDialog(QDialog):
    def __init__(self, path, project_crs, parent=None, layer=None):
        super().__init__(parent)
        from fileio.vector_io import is_vector
        self.path = path
        self.layer = layer
        self.is_shp = is_vector(path)
        self.project_crs = project_crs
        self.crs, self.crs_note = None, ""
        self.result = None
        self.combos = []
        self.setWindowTitle(f"Import structures — {os.path.basename(path)}")
        self.resize(900, 700 if not self.is_shp else 380)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        t = QLabel(f"Import structures — {os.path.basename(path)}")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Wind turbines, substations or piles: a name, a position and the foundation diameter "
                           "(used for scour analysis radii)."))
        v.addWidget(_section("Coordinate system of the file (required)"))
        row = QHBoxLayout()
        self.lbl_crs = QLabel()
        self.lbl_crs.setWordWrap(True)
        b = QPushButton("Choose EPSG…")
        b.clicked.connect(self._choose_crs)
        row.addWidget(self.lbl_crs, 1)
        row.addWidget(b)
        v.addLayout(row)
        drow = QHBoxLayout()
        drow.addWidget(_muted("Diameter where the file has none"))
        self.sp_d = QDoubleSpinBox(minimum=0.1, maximum=100.0, value=8.0, decimals=2, singleStep=0.5)
        self.sp_d.setSuffix(" m")
        self.sp_d.valueChanged.connect(self._update)
        drow.addWidget(self.sp_d)
        drow.addStretch()
        v.addLayout(drow)
        if self.is_shp:
            self.info = _muted("")
            v.addWidget(self.info)
            _s, crs, _i = stmod.read_shp(path, layer=layer)
            if crs is not None:
                self.crs, self.crs_note = crs, "from the file"
        else:
            self.lines, self.encoding = read_preview(path)
            v.addWidget(_section("Raw preview"))
            raw = QPlainTextEdit(readOnly=True)
            raw.setLineWrapMode(QPlainTextEdit.NoWrap)
            f = QFont()
            f.setFamilies(["Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"])
            raw.setFont(f)
            raw.setPlainText("\n".join(f"{i + 1:>5}  {l}" for i, l in enumerate(self.lines[:200])))
            v.addWidget(raw, 2)
            prow = QHBoxLayout()
            self.sp_start = QSpinBox(minimum=1, maximum=max(1, len(self.lines)))
            self.cmb_delim = QComboBox()
            for key, (label, _) in DELIMITERS.items():
                self.cmb_delim.addItem(label, key)
            for text, w in (("Start at line", self.sp_start), ("Delimiter", self.cmb_delim)):
                prow.addWidget(_muted(text))
                prow.addWidget(w)
                prow.addSpacing(14)
            prow.addStretch()
            v.addLayout(prow)
            v.addWidget(_section("Column mapping"))
            self.table = QTableWidget()
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            v.addWidget(self.table, 3)
            d = rr.detect_delimiter(self.lines)
            self.cmb_delim.setCurrentIndex(self.cmb_delim.findData(d))
            self.sp_start.setValue(rr.detect_start_row(self.lines, d) + 1)
            self.sp_start.valueChanged.connect(self._rebuild)
            self.cmb_delim.currentIndexChanged.connect(self._rebuild)
        self.lbl = QLabel(" ")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        foot = QHBoxLayout()
        foot.addStretch()
        bc = QPushButton("Cancel")
        bc.clicked.connect(self.reject)
        self.ok = QPushButton("Import")
        self.ok.setProperty("primary", True)
        self.ok.clicked.connect(self.accept)
        foot.addWidget(bc)
        foot.addWidget(self.ok)
        v.addLayout(foot)
        if not self.is_shp:
            self._rebuild()
        if self.crs is None and self.project_crs is not None:
            self.crs, self.crs_note = self.project_crs, "assumed: same as the project — check!"
        self._show_crs()
        self._update()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _rows(self):
        d = self.cmb_delim.currentData()
        st = self.sp_start.value() - 1
        rows = [r for r in (split_line(l, d) for l in self.lines[st:st + 30]) if r]
        hdr = split_line(self.lines[st - 1], d) if st > 0 else None
        return rows, hdr

    def _rebuild(self, *_):
        rows, hdr = self._rows()
        ncols = max((len(r) for r in rows), default=0)
        roles = stmod.guess_roles(rows, hdr)
        self.table.clear()
        self.table.setColumnCount(ncols)
        self.table.setRowCount(min(len(rows), 12) + 1)
        self.table.setHorizontalHeaderLabels(hdr if hdr and len(hdr) == ncols else [f"Column {i + 1}" for i in range(ncols)])
        self.table.setVerticalHeaderLabels(["Role"] + [str(i + 1) for i in range(min(len(rows), 12))])
        self.table.setRowHeight(0, 38)
        self.combos = []
        for c in range(ncols):
            cmb = QComboBox()
            cmb.addItems(stmod.ROLES)
            cmb.setCurrentText(roles[c] if c < len(roles) else "Ignore")
            cmb.currentIndexChanged.connect(self._roles_changed)
            self.table.setCellWidget(0, c, cmb)
            self.combos.append(cmb)
        for r, tokens in enumerate(rows[:12], start=1):
            for c, t in enumerate(tokens):
                self.table.setItem(r, c, QTableWidgetItem(t))
        self.table.resizeColumnsToContents()
        for c in range(ncols):
            self.table.setColumnWidth(c, max(self.table.columnWidth(c), 130))
        self._roles_changed()

    def _roles_changed(self, *_):
        roles = [c.currentText() for c in self.combos]
        if "Longitude" in roles and "Latitude" in roles and (self.crs is None or not crsmod.is_geographic(self.crs)):
            self.crs, self.crs_note = crsmod.from_epsg(4326), "latitude / longitude columns"
            self._show_crs()
        self._update()

    def _choose_crs(self):
        ok, c = CrsDialog.ask(self, "Coordinate system of the file", "EPSG code of the coordinates in this file.",
                              initial=self.crs, allow_unknown=False)
        if ok and c is not None:
            self.crs, self.crs_note = c, "chosen"
            self._show_crs()
            self._update()

    def _show_crs(self):
        if self.crs is None:
            self.lbl_crs.setText(f"<span style='color:{C['danger']}'>●</span>  Not set — click <b>Choose EPSG…</b>")
        else:
            note = f"  <span style='color:{C['muted']}'>({self.crs_note})</span>" if self.crs_note else ""
            self.lbl_crs.setText(f"<span style='color:{C['success']}'>●</span>  <b>{crsmod.label(self.crs)}</b>{note}")

    def _parse(self):
        if self.is_shp:
            st, _crs, info = stmod.read_shp(self.path, self.sp_d.value(), self.layer)
            self.info.setText(info)
            return st
        st, _geo = stmod.read_table(self.path, self.sp_start.value() - 1, self.cmb_delim.currentData(),
                                    [c.currentText() for c in self.combos], self.encoding, self.sp_d.value())
        return st

    def _update(self, *_):
        try:
            self.result = self._parse()
            err = ""
        except Exception as e:  # noqa: BLE001
            self.result, err = None, str(e)
        if not err and self.crs is None:
            err = "Choose the coordinate system of the file"
        if err:
            self.lbl.setText(f"<span style='color:{C['danger']}'>●  {err}</span>")
        else:
            s = self.result
            names = ", ".join(s.names[:8]) + (f" … (+{len(s) - 8})" if len(s) > 8 else "")
            self.lbl.setText(f"<span style='color:{C['success']}'>●</span>  {len(s)} structure(s): {names} · "
                             f"Ø {np.min(s.diameter):g}–{np.max(s.diameter):g} m")
        self.ok.setEnabled(not err)
