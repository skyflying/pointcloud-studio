"""Route import dialog: coordinate system (required), CSV column mapping or Shapefile features, KP options."""
from __future__ import annotations

import os
import re

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core import crs as crsmod
from fileio import route_reader as rr
from fileio.xyz_reader import DELIMITERS, read_preview, split_line
from ui.check_helpers import CheckList, check_bar
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
    def __init__(self, path, project_crs, parent=None, layer=None):
        super().__init__(parent)
        self.path = path
        from fileio.vector_io import is_vector
        self.is_shp = is_vector(path)          # any vector format (Shapefile, GeoPackage, GeoJSON, KML, GPX, DXF)
        self.layer = layer
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
            from fileio import vector_io
            self.feats, prj_crs, info = vector_io.read(path, layer)
            self.base = os.path.splitext(os.path.basename(path))[0]
            fields, has_gz, guess = rr.vector_fields(self.feats)
            v.addWidget(_section("Features"))
            v.addWidget(_muted(info + " — map the attribute fields (detected automatically, change if needed)."))
            g = QGridLayout()

            def fcombo(extra, cur):
                c = QComboBox()
                for lab, data in extra:
                    c.addItem(lab, data)
                for f_ in fields:
                    c.addItem(str(f_), f_)
                j_ = c.findData(cur)
                c.setCurrentIndex(j_ if j_ >= 0 else 0)
                c.currentIndexChanged.connect(self._update)
                return c
            self.f_name = fcombo([("(none)", None)], guess["name"])
            self.f_name.setToolTip("Name of each line — for point layers: which cable each point belongs to")
            self.f_kp = fcombo([("(none)", None)], guess["kp"])
            self.f_kp.setToolTip("Point layers: KP of each point (the points are ordered by it); lines: KP at the start")
            zopts = [("(no cable level)", None)] + ([("Geometry Z", "geometry")] if has_gz else [])
            self.f_z = fcombo(zopts, guess["z"])
            self.f_zdepth = QCheckBox("values are depths (+ down)")
            self.f_zdepth.setChecked(guess["z_depth"])
            self.f_zdepth.toggled.connect(self._update)
            self.f_dob = fcombo([("(none)", None)], guess["dob"])
            self.f_sb = fcombo([("(none)", None)], guess["seabed"])
            self.f_sbdepth = QCheckBox("seabed values are depths (+ down)")
            self.f_sbdepth.toggled.connect(self._update)
            rows_ = (("Name / cable", self.f_name, None), ("KP", self.f_kp, None),
                     ("Cable level from", self.f_z, self.f_zdepth), ("DoB (reported)", self.f_dob, None),
                     ("Seabed (for top = seabed − DoB)", self.f_sb, self.f_sbdepth))
            for r_, (lab, w1, w2) in enumerate(rows_):
                g.addWidget(_muted(lab), r_, 0)
                g.addWidget(w1, r_, 1)
                if w2 is not None:
                    g.addWidget(w2, r_, 2)
            v.addLayout(g)
            self.lst = CheckList()
            self.lst.itemChanged.connect(self._update)
            v.addWidget(check_bar(self.lst, "Import:"))
            v.addWidget(self.lst, 1)
            self._unchecked = set()
            self.crs = prj_crs
            self.crs_note = "from the file" if prj_crs is not None else ""
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
            self.sp_hdr = QSpinBox(minimum=0, maximum=max(1, len(self.lines)))
            self.sp_hdr.setSpecialValueText("none")
            self.sp_hdr.setToolTip("Line with the column names (used to suggest the roles); 0 = none")
            self.chk_dc = QCheckBox("Decimal comma")
            self.chk_dc.setToolTip("Numbers written as 21,50 (the delimiter must then not be a comma)")
            self.ed_comment = QLineEdit("#")
            self.ed_comment.setMaximumWidth(40)
            self.ed_comment.setToolTip("Lines starting with this character are skipped (empty = none)")
            for text, w in (("Start at line", self.sp_start), ("Delimiter", self.cmb_delim), ("Header line", self.sp_hdr)):
                prow.addWidget(_muted(text))
                prow.addWidget(w)
                prow.addSpacing(14)
            prow.addWidget(self.chk_dc)
            prow.addSpacing(10)
            prow.addWidget(_muted("Comment"))
            prow.addWidget(self.ed_comment)
            prow.addStretch()
            b_guess = QPushButton("Guess columns again")
            b_guess.clicked.connect(lambda: self._rebuild(True))
            prow.addWidget(b_guess)
            v.addLayout(prow)
            v.addWidget(_muted("If the columns are not recognised: set <i>Start at line</i> to the first data line, the "
                               "<i>Delimiter</i>, and choose the role of every column in the first row of the table "
                               "below (Easting / Northing or Latitude / Longitude, KP, cable level, seabed, DoB…). "
                               "Latitude / longitude may be decimal or degrees-minutes(-seconds) with N/S/E/W."))
            v.addWidget(_section("Column mapping"))
            self.table = QTableWidget()
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            v.addWidget(self.table, 3)
            d = rr.detect_delimiter(self.lines)
            self.cmb_delim.setCurrentIndex(self.cmb_delim.findData(d))
            self.sp_start.setValue(rr.detect_start_row(self.lines, d) + 1)
            self.sp_hdr.setValue(max(self.sp_start.value() - 1, 0))
            self.sp_start.valueChanged.connect(lambda: (self.sp_hdr.setValue(max(self.sp_start.value() - 1, 0)),
                                                        self._rebuild(True)))
            self.cmb_delim.currentIndexChanged.connect(lambda: self._rebuild(True))
            self.sp_hdr.valueChanged.connect(lambda: self._rebuild(True))
            self.chk_dc.toggled.connect(self._update)
            self.ed_comment.textChanged.connect(self._update)
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
        self.chk_sortkp = QCheckBox("Sort points by KP")
        self.chk_sortkp.setToolTip("Order the vertices by the KP column (for files not in route order)")
        self.chk_sortkp.toggled.connect(self._update)
        krow.addSpacing(14)
        krow.addWidget(self.chk_sortkp)
        krow.addStretch()
        v.addLayout(krow)
        self.cmb_unit.currentIndexChanged.connect(self._update)
        self.sp_kp0.valueChanged.connect(self._update)
        if self.is_shp:
            self.cmb_unit.setEnabled(False)

        # cable profile (top-of-cable Z) — CSV: column roles; Shapefile: PolyLineZ values
        v.addWidget(_section("Cable profile (optional)"))
        if not self.is_shp:
            v.addWidget(_muted("Map a column to <b>Cable top (elevation)</b> or <b>Cable top (depth, + down)</b> "
                               "to import a cable profile (SBI / TSS / ROV data) for depth-of-burial, free-span and "
                               "multi-year analyses. Without a cable column, <b>Seabed</b> + <b>DoB (reported)</b> give "
                               "the cable top as seabed − DoB."))
        zrow = QHBoxLayout()
        self.cmb_zunit = QComboBox()
        for u in rr.Z_UNITS:
            self.cmb_zunit.addItem(u, u)
        self.sp_zoff = QDoubleSpinBox(minimum=-1000, maximum=1000, decimals=3, value=0.0, singleStep=0.1)
        self.sp_zoff.setSuffix(" m")
        self.sp_zoff.setToolTip("Added to every level after unit / sign conversion (e.g. a vertical datum shift)")
        self.cmb_zref = QComboBox()
        for lab, k in (("top of the cable", "top"), ("centre of the cable", "centre"), ("bottom of the cable", "bottom")):
            self.cmb_zref.addItem(lab, k)
        self.sp_diam = QDoubleSpinBox(minimum=0.0, maximum=5.0, decimals=3, value=0.2, singleStep=0.01)
        self.sp_diam.setSuffix(" m")
        for text, w in (("Level unit", self.cmb_zunit), ("Datum shift", self.sp_zoff), ("Level refers to", self.cmb_zref),
                        ("Cable Ø", self.sp_diam)):
            zrow.addWidget(_muted(text))
            zrow.addWidget(w)
            zrow.addSpacing(10)
            (w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged).connect(self._update)
        zrow.addStretch()
        v.addLayout(zrow)

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
        h = self.sp_hdr.value() if hasattr(self, "sp_hdr") else st
        hdr = split_line(self.lines[h - 1], d) if 0 < h <= len(self.lines) else None
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
            # hints in the cable / seabed column names: unit, reference point, depth
            if hdr and len(hdr) == ncols:
                lvl = [h.lower() for h, r_ in zip(hdr, roles) if r_.startswith(("Cable top", "Seabed"))]
                txt = " ".join(lvl)
                for u in ("mm", "cm", "ft"):
                    if re.search(rf"(?<![a-z]){u}(?![a-z])", txt.replace("_", " ")):
                        self.cmb_zunit.setCurrentIndex(self.cmb_zunit.findData(u))
                        break
                if any(k in txt for k in ("centre", "center", "_cl", " cl")):
                    self.cmb_zref.setCurrentIndex(self.cmb_zref.findData("centre"))
                elif "bottom" in txt or "boc" in txt:
                    self.cmb_zref.setCurrentIndex(self.cmb_zref.findData("bottom"))
                for k, (h, r_) in enumerate(zip(hdr, roles)):
                    if r_ == "Cable top (elevation)" and ("depth" in h.lower() or "dep" in h.lower()):
                        self.combos[k].setCurrentText("Cable top (depth, + down)")
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
        zo = {"z_unit": self.cmb_zunit.currentData(), "z_offset": self.sp_zoff.value(),
              "z_ref": self.cmb_zref.currentData(), "diameter": self.sp_diam.value()}
        if self.is_shp:
            o = dict(zo, name=self.f_name.currentData(), kp=self.f_kp.currentData(), z=self.f_z.currentData(),
                     z_depth=self.f_zdepth.isChecked(), dob=self.f_dob.currentData(), seabed=self.f_sb.currentData(),
                     seabed_depth=self.f_sbdepth.isChecked())
            routes = rr.routes_from_features(self.feats, self.base, o)
            # remember unticked routes by name across rebuilds
            for k in range(self.lst.count()):
                it = self.lst.item(k)
                (self._unchecked.add if it.checkState() != Qt.Checked else self._unchecked.discard)(it.data(Qt.UserRole))
            self.lst.blockSignals(True)
            self.lst.clear()
            for r in routes:
                k0, k1 = r.kp_range()
                it = QListWidgetItem(f"{r.name}   ·   {len(r.xy)} vertices   ·   KP {k0:,.3f} – {k1:,.3f}"
                                     + ("   ·   cable level" if r.z is not None else ""))
                it.setData(Qt.UserRole, r.name)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Unchecked if r.name in self._unchecked else Qt.Checked)
                self.lst.addItem(it)
            self.lst.blockSignals(False)
            out = [r for k, r in enumerate(routes) if self.lst.item(k).checkState() == Qt.Checked]
            for r in out:
                if r.kp is None and not self.f_kp.currentData():
                    r.start_kp = self.sp_kp0.value()
            return out
        st = self.sp_start.value() - 1
        routes, _ = rr.read_route_table(self.path, st, self.cmb_delim.currentData(),
                                        [c.currentText() for c in self.combos], self.encoding,
                                        kp_unit=self.cmb_unit.currentData(), start_kp=self.sp_kp0.value(),
                                        opts=dict(zo, decimal_comma=self.chk_dc.isChecked(),
                                                  comment=self.ed_comment.text().strip(),
                                                  sort_kp=self.chk_sortkp.isChecked()))
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
                extra = ""
                if r.z is not None:
                    extra = f", cable top {np.nanmin(r.z):,.2f} … {np.nanmax(r.z):,.2f} m"
                if r.dob is not None:
                    extra += ", reported DoB"
                parts.append(f"<b>{r.name}</b>: {len(r.xy)} vertices, KP {k0:,.3f} – {k1:,.3f}{extra}")
            more = f" … (+{len(self.routes) - 6})" if len(self.routes) > 6 else ""
            self.lbl.setText(f"<span style='color:{C['success']}'>●</span>  {len(self.routes)} route(s): "
                             + " · ".join(parts) + more)
        self.ok.setEnabled(not err)
