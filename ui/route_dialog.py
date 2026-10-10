"""Route import dialog: coordinate system (required), CSV column mapping or Shapefile features, KP options."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core import crs as crsmod
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


class RouteImportDialog(QDialog):
    def __init__(self, path, project_crs, parent=None):
        super().__init__(parent)
        self.path = path
        self.is_shp = path.lower().endswith(".shp")
        self.project_crs = project_crs
        self.setWindowTitle(f"Import route — {os.path.basename(path)}")
        self.resize(960, 760 if not self.is_shp else 560)
        self.crs = None
        self.routes = []
        self.combos = []
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(8)
        t = QLabel(f"Import route — {os.path.basename(path)}")
        t.setObjectName("title")
        v.addWidget(t)

        # coordinate system (required)
        v.addWidget(_section("Coordinate system of the file (required)"))
        row = QHBoxLayout()
        self.lbl_crs = QLabel()
        self.lbl_crs.setWordWrap(True)
        b = QPushButton("Choose EPSG…")
        b.clicked.connect(self._choose_crs)
        row.addWidget(self.lbl_crs, 1)
        row.addWidget(b)
        v.addLayout(row)
        self.lbl_reproj = _muted("")
        v.addWidget(self.lbl_reproj)

        if self.is_shp:
            self.shp_routes, prj_crs, info = rr.read_shp(path)
            v.addWidget(_section("Features"))
            v.addWidget(_muted(info))
            self.lst = QListWidget()
            for r in self.shp_routes:
                it = QListWidgetItem(f"{r.name}   ·   {r.length / 1000:,.3f} km (in file units)   ·   {len(r.xy)} vertices")
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                self.lst.addItem(it)
            self.lst.itemChanged.connect(self._update)
            v.addWidget(self.lst, 1)
            self.crs = prj_crs
            self.crs_note = "from the .prj file" if prj_crs is not None else ""
        else:
            self.lines, self.encoding = read_preview(path)
            v.addWidget(_section("Raw preview"))
            raw = QPlainTextEdit(readOnly=True)
            raw.setLineWrapMode(QPlainTextEdit.NoWrap)
            f = QFont()
            f.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono"])
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
            self.sp_start.valueChanged.connect(lambda: self._rebuild(True))
            self.cmb_delim.currentIndexChanged.connect(lambda: self._rebuild(True))
            self.crs_note = ""

        # KP options
        v.addWidget(_section("KP"))
        krow = QHBoxLayout()
        self.cmb_unit = QComboBox()
        self.cmb_unit.addItem("KP column in km", "km")
        self.cmb_unit.addItem("KP column in metres", "m")
        self.sp_kp0 = QDoubleSpinBox(minimum=-1e6, maximum=1e6, decimals=3, value=0.0, singleStep=0.1)
        self.sp_kp0.setSuffix(" km")
        self.sp_kp0.setToolTip("Used when the file has no KP column: KP = start + distance along the route")
        krow.addWidget(self.cmb_unit)
        krow.addSpacing(14)
        krow.addWidget(_muted("Start KP (no KP column)"))
        krow.addWidget(self.sp_kp0)
        krow.addStretch()
        v.addLayout(krow)
        self.cmb_unit.currentIndexChanged.connect(self._update)
        self.sp_kp0.valueChanged.connect(self._update)
        if self.is_shp:
            self.cmb_unit.setEnabled(False)

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
            self._rebuild(True)
        self._show_crs()
        self._update()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    # ---------- CSV mapping ----------
    def _rows(self):
        d = self.cmb_delim.currentData()
        st = self.sp_start.value() - 1
        rows = [r for r in (split_line(l, d) for l in self.lines[st:st + 30]) if r]
        hdr = split_line(self.lines[st - 1], d) if st > 0 else None
        return rows, hdr

    def _rebuild(self, guess):
        rows, hdr = self._rows()
        ncols = max((len(r) for r in rows), default=0)
        roles = rr.guess_roles(rows, hdr) if guess or len(self.combos) != ncols else [c.currentText() for c in self.combos]
        self.table.clear()
        self.table.setColumnCount(ncols)
        self.table.setRowCount(min(len(rows), 12) + 1)
        self.table.setHorizontalHeaderLabels(hdr if hdr and len(hdr) == ncols else [f"Column {i + 1}" for i in range(ncols)])
        self.table.setVerticalHeaderLabels(["Role"] + [str(i + 1) for i in range(min(len(rows), 12))])
        self.table.setRowHeight(0, 38)
        self.combos = []
        for c in range(ncols):
            cmb = QComboBox()
            cmb.addItems(rr.ROUTE_ROLES)
            cmb.setCurrentText(roles[c] if c < len(roles) else "Ignore")
            cmb.currentIndexChanged.connect(self._roles_changed)
            self.table.setCellWidget(0, c, cmb)
            self.combos.append(cmb)
        for r, tokens in enumerate(rows[:12], start=1):
            for c, t in enumerate(tokens):
                self.table.setItem(r, c, QTableWidgetItem(t))
        self.table.resizeColumnsToContents()
        for c in range(ncols):
            self.table.setColumnWidth(c, max(self.table.columnWidth(c), 140))
        if guess:
            self.cmb_unit.setCurrentIndex(self.cmb_unit.findData(rr.guess_kp_unit(rows, roles)))
        self._roles_changed()

    def _roles_changed(self):
        roles = [c.currentText() for c in self.combos]
        geo = "Longitude" in roles and "Latitude" in roles
        if geo and (self.crs is None or not crsmod.is_geographic(self.crs)):
            self.crs = crsmod.from_epsg(4326)
            self.crs_note = "latitude / longitude columns"
        elif not geo and self.crs is None and self.project_crs is not None:
            self.crs = self.project_crs
            self.crs_note = "assumed: same as the project — check!"
        self.cmb_unit.setEnabled("KP" in roles)
        self.sp_kp0.setEnabled("KP" not in roles)
        self._show_crs()
        self._update()

    # ---------- CRS ----------
    def _choose_crs(self):
        ok, c = CrsDialog.ask(self, "Coordinate system of the route file",
                              "EPSG code of the coordinates in this file.", initial=self.crs, allow_unknown=False)
        if ok and c is not None:
            self.crs = c
            self.crs_note = "chosen"
            self._show_crs()
            self._update()

    def _show_crs(self):
        if self.crs is None:
            self.lbl_crs.setText(f"<span style='color:{C['danger']}'>●</span>  Not set — click <b>Choose EPSG…</b>")
        else:
            note = f"  <span style='color:{C['muted']}'>({self.crs_note})</span>" if self.crs_note else ""
            self.lbl_crs.setText(f"<span style='color:{C['success']}'>●</span>  <b>{crsmod.label(self.crs)}</b>{note}")
        same = crsmod.same(self.crs, self.project_crs)
        if self.project_crs is None:
            self.lbl_reproj.setText("The project has no coordinate system yet — it will use this one.")
        elif same is False:
            self.lbl_reproj.setText(f"Will be reprojected to the project system: {crsmod.label(self.project_crs)}")
        else:
            self.lbl_reproj.setText("Same as the project coordinate system.")

    # ---------- parse / summary ----------
    def _parse(self):
        if self.is_shp:
            routes = [r for i, r in enumerate(self.shp_routes) if self.lst.item(i).checkState() == Qt.Checked]
            for r in routes:
                r.start_kp = self.sp_kp0.value()
            return routes
        st = self.sp_start.value() - 1
        routes, _ = rr.read_route_table(self.path, st, self.cmb_delim.currentData(),
                                        [c.currentText() for c in self.combos], self.encoding,
                                        kp_unit=self.cmb_unit.currentData(), start_kp=self.sp_kp0.value())
        return routes

    def _update(self, *_):
        try:
            self.routes = self._parse()
            err = "" if self.routes else "No route selected"
        except Exception as e:  # noqa: BLE001
            self.routes, err = [], str(e)
        if not err and self.crs is None:
            err = "Choose the coordinate system of the file"
        if err:
            self.lbl.setText(f"<span style='color:{C['danger']}'>●  {err}</span>")
        else:
            parts = []
            for r in self.routes[:6]:
                k0, k1 = r.kp_range()
                parts.append(f"<b>{r.name}</b>: {len(r.xy)} vertices, KP {k0:,.3f} – {k1:,.3f}")
            more = f" … (+{len(self.routes) - 6})" if len(self.routes) > 6 else ""
            self.lbl.setText(f"<span style='color:{C['success']}'>●</span>  {len(self.routes)} route(s): "
                             + " · ".join(parts) + more)
        self.ok.setEnabled(not err)
