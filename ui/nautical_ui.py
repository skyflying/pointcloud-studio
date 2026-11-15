"""Chart menu: vertical datum to chart datum, BAG / S-102 bathymetric surfaces, chart layers (soundings, depth
contours, depth areas) — always shoal-biased."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from core.datum import S100_VERTICAL_DATUMS, datum_label
from ui.theme import dark_titlebar


def _muted(t):
    lab = QLabel(t)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


def _dsp(v, lo, hi, step=0.1, dec=2, suffix=""):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setValue(v)
    if suffix:
        w.setSuffix(suffix)
    return w


def _datum_combo(default=12):
    c = QComboBox()
    for code in sorted(S100_VERTICAL_DATUMS):
        c.addItem(datum_label(code), code)
    c.setCurrentIndex(max(c.findData(int(default)), 0))
    return c


def _file_row(caption, flt, save=False):
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    ed = QLineEdit()
    b = QPushButton("…")
    b.setFixedWidth(30)

    def pick():
        if save:
            p, _ = QFileDialog.getSaveFileName(row, caption, ed.text(), flt)
        else:
            p, _ = QFileDialog.getOpenFileName(row, caption, ed.text(), flt)
        if p:
            ed.setText(p)
    b.clicked.connect(pick)
    h.addWidget(ed, 1)
    h.addWidget(b)
    row.edit = ed
    return row


class _Base(QDialog):
    def __init__(self, title, intro, parent):
        super().__init__(parent)
        self.setWindowTitle(title)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        t = QLabel(title)
        t.setObjectName("title")
        outer.addWidget(t)
        if intro:
            outer.addWidget(_muted(intro))
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.v = QVBoxLayout(self.body)
        self.v.setContentsMargins(0, 0, 0, 0)
        sc.setWidget(self.body)
        outer.addWidget(sc, 1)
        self.msg = QLabel(" ")
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet("color:#ff6b6b")
        outer.addWidget(self.msg)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)
        self._sc = sc

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)
        self.body.layout().activate()
        need = self.body.minimumSizeHint().width() + self._sc.verticalScrollBar().sizeHint().width() + 48
        self.setMinimumWidth(max(need, 520))

    def error(self, t):
        self.msg.setText(t)

    def _ok(self):
        err = self.validate()
        if err:
            self.error(err)
            return
        self.accept()

    def validate(self):
        return None


# ================================================================ vertical datum
class SeparationBox(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Separation S — height of the target datum in the current datum (m, up +)", parent)
        f = QFormLayout(self)
        self.rb = {}
        grp = QButtonGroup(self)
        row = QHBoxLayout()
        for k, t in (("constant", "Constant"), ("raster", "Raster (GeoTIFF / ASC …)"), ("table", "Table (X Y S)")):
            self.rb[k] = QRadioButton(t)
            grp.addButton(self.rb[k])
            row.addWidget(self.rb[k])
        self.rb["constant"].setChecked(True)
        f.addRow(row)
        self.value = _dsp(0.0, -1000, 1000, 0.01, 3, " m")
        f.addRow("S", self.value)
        self.file = _file_row("Separation file", "Rasters / tables (*.tif *.tiff *.asc *.grd *.xyz *.txt *.csv);;"
                              "All files (*)")
        f.addRow("File", self.file)
        tb = QHBoxLayout()
        self.xc, self.yc, self.sc_ = QSpinBox(), QSpinBox(), QSpinBox()
        for w_, v in ((self.xc, 1), (self.yc, 2), (self.sc_, 3)):
            w_.setRange(1, 50)
            w_.setValue(v)
        self.method = QComboBox()
        self.method.addItem("Inverse distance (4 nearest)", "idw")
        self.method.addItem("Nearest", "nearest")
        self.maxd = _dsp(0.0, 0.0, 1e6, 100.0, 0, " m")
        self.maxd.setSpecialValueText("no limit")
        for lab, w_ in (("X col", self.xc), ("Y col", self.yc), ("S col", self.sc_)):
            tb.addWidget(_muted(lab))
            tb.addWidget(w_)
        f.addRow("Table", tb)
        f.addRow("Interpolation", self.method)
        f.addRow("Max. distance", self.maxd)
        f.addRow(_muted("Example: chart datum 1.80 m below mean sea level → S = −1.80; new heights = height − S, "
                        "depths below chart datum = −height. Points or cells without a separation value are left "
                        "out (grids) or unchanged and reported (point clouds)."))
        for r in self.rb.values():
            r.toggled.connect(self._sync)
        self._sync()

    def _sync(self):
        k = self.kind()
        self.value.setEnabled(k == "constant")
        self.file.setEnabled(k != "constant")
        for w_ in (self.xc, self.yc, self.sc_, self.method, self.maxd):
            w_.setEnabled(k == "table")

    def kind(self):
        return next(k for k, r in self.rb.items() if r.isChecked())

    def separation(self, crs):
        from core.datum import Separation
        k = self.kind()
        if k == "constant":
            return Separation("constant", value=self.value.value(), crs=crs)
        if k == "raster":
            return Separation("raster", path=self.file.edit.text().strip(), crs=crs)
        return Separation("table", path=self.file.edit.text().strip(), x_col=self.xc.value() - 1,
                          y_col=self.yc.value() - 1, value_col=self.sc_.value() - 1, method=self.method.currentData(),
                          max_dist=self.maxd.value(), crs=crs)

    def check(self):
        if self.kind() != "constant" and not os.path.isfile(self.file.edit.text().strip()):
            return "Choose the separation file"
        return None


class VerticalDatumDialog(_Base):
    def __init__(self, layers, current, parent=None):
        super().__init__("Vertical datum", "Shift heights from the survey's vertical datum (e.g. TWVD2001, mean sea "
                                          "level, ellipsoid) to a chart datum (LAT, MLLW …). Grids give a new "
                                          "layer; point clouds a new layer or shifted heights.", parent)
        self.layers = layers
        g = QGroupBox("Data")
        f = QFormLayout(g)
        self.cmb = QComboBox()
        for i, n, k, npts in layers:
            self.cmb.addItem(f"{n}  ({'points' if k == 'points' else 'grid'})", i)
        self.cmb.setCurrentIndex(max(self.cmb.findData(current), 0))
        f.addRow("Layer", self.cmb)
        self.inplace = QCheckBox("Point clouds: shift the heights of the layer itself (no copy — saves memory)")
        f.addRow(self.inplace)
        self.v.addWidget(g)
        self.sep = SeparationBox()
        self.v.addWidget(self.sep)
        g2 = QGroupBox("Target datum")
        f2 = QFormLayout(g2)
        self.datum = _datum_combo(12)
        f2.addRow("Datum (S-100 code)", self.datum)
        f2.addRow(_muted("Recorded with the layer and written to BAG / S-102 exports and chart layers."))
        self.v.addWidget(g2)
        self.cmb.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self):
        k, n = next(((k, n) for i, _n, k, n in self.layers if i == self.cmb.currentData()), ("grid", 0))
        self.inplace.setEnabled(k == "points")
        self.inplace.setChecked(k == "points" and n > 5_000_000)

    def validate(self):
        return self.sep.check()


# ================================================================ BAG / S-102
class GridProductDialog(_Base):
    def __init__(self, grids, current, default_name, parent=None):
        super().__init__("Export bathymetric surface (BAG / S-102)",
                         "Depth + uncertainty grid for navigation products. Use a shoal-biased surface: a DEM with "
                         "the 'shoal' method (shoalest sounding per cell) or a CUBE surface with the 'Shoal' "
                         "disambiguation. Resampling always keeps the shoalest value.", parent)
        self.grids = grids                       # [(id, name, method, cell, has_cvals, vdatum code or None)]
        g = QGroupBox("Surface")
        f = QFormLayout(g)
        self.cmb = QComboBox()
        for i, n, m, c, hc, vd in grids:
            if not hc:
                self.cmb.addItem(f"{n}  ({m}, {c:g} m)", i)
        self.cmb.setCurrentIndex(max(self.cmb.findData(current), 0))
        f.addRow("Depth grid", self.cmb)
        self.note = _muted("")
        f.addRow(self.note)
        self.unc = QComboBox()
        self.unc.addItem("IHO S-44 order TVU (from depth)", ("iho", None))
        self.unc.addItem("Constant", ("const", None))
        for i, n, m, c, hc, vd in grids:
            self.unc.addItem(f"Grid: {n}" + (" (colour values)" if hc else ""), ("grid", i))
        self.unc.addItem("None", ("none", None))
        ci = next((k for k in range(self.unc.count()) if "uncertainty" in self.unc.itemText(k).lower()), 0)
        self.unc.setCurrentIndex(ci)
        f.addRow("Uncertainty (95 %)", self.unc)
        self.order = QComboBox()
        from core.s44 import ORDER_LABEL
        for k, t in ORDER_LABEL.items():
            self.order.addItem(t, k)
        self.order.setCurrentIndex(max(self.order.findData("1a"), 0))
        self.uconst = _dsp(0.5, 0.0, 100.0, 0.05, 2, " m")
        hr = QHBoxLayout()
        hr.addWidget(self.order)
        hr.addWidget(self.uconst)
        f.addRow("Order / constant", hr)
        self.datum = _datum_combo(12)
        f.addRow("Vertical datum", self.datum)
        self.v.addWidget(g)
        g2 = QGroupBox("Format")
        f2 = QFormLayout(g2)
        self.rb_bag = QRadioButton("BAG (ONS 1.6, in the project coordinate system)")
        self.rb_102 = QRadioButton("IHO S-102 3.0.0 (WGS 84 / UTM)")
        self.rb_102.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_bag)
        grp.addButton(self.rb_102)
        f2.addRow(self.rb_102)
        f2.addRow(self.rb_bag)
        self.zone = QSpinBox()
        self.zone.setRange(0, 60)
        self.zone.setSpecialValueText("automatic")
        self.zone.setPrefix("zone ")
        self.cell = _dsp(0.0, 0.0, 10000.0, 0.5, 2, " m")
        self.cell.setSpecialValueText("as the grid")
        f2.addRow("UTM zone (S-102)", self.zone)
        f2.addRow("Cell size", self.cell)
        self.prod = QLineEdit("TW00")
        self.prod.setMaxLength(4)
        self.dsname = QLineEdit(default_name)
        self.dsname.setMaxLength(12)
        nr = QHBoxLayout()
        nr.addWidget(_muted("producer"))
        nr.addWidget(self.prod)
        nr.addWidget(_muted("name"))
        nr.addWidget(self.dsname, 1)
        f2.addRow("S-102 file name", nr)
        self.v.addWidget(g2)
        g3 = QGroupBox("Metadata (BAG)")
        f3 = QFormLayout(g3)
        self.who = QLineEdit()
        self.org = QLineEdit()
        self.abstract = QLineEdit()
        f3.addRow("Responsible person", self.who)
        f3.addRow("Organisation", self.org)
        f3.addRow("Abstract", self.abstract)
        self.v.addWidget(g3)
        self.folder = QLineEdit(os.path.expanduser("~"))
        b = QPushButton("…")
        b.setFixedWidth(30)
        b.clicked.connect(lambda: self.folder.setText(QFileDialog.getExistingDirectory(self, "Output folder",
                                                                                      self.folder.text())
                                                      or self.folder.text()))
        fr = QHBoxLayout()
        fr.addWidget(self.folder, 1)
        fr.addWidget(b)
        self.v.addLayout(fr)
        for w_ in (self.cmb, self.unc):
            w_.currentIndexChanged.connect(self._sync)
        self.rb_bag.toggled.connect(self._sync)
        self._sync()

    def _sync(self):
        gi = self.cmb.currentData()
        row = next((r for r in self.grids if r[0] == gi), None)
        if row is not None:
            m = row[2]
            safe = m in ("shoal", "max", "cube")
            self.note.setText("Shoal-biased surface ✓" if safe else
                              f"This grid uses the '{m}' method — for navigation build a 'shoal' DEM or a CUBE "
                              "surface with 'Shoal' disambiguation.")
            self.note.setStyleSheet("" if safe else "color:#ffb347")
            if row[5] is not None:
                self.datum.setCurrentIndex(max(self.datum.findData(int(row[5])), 0))
        k = self.unc.currentData()[0]
        self.order.setEnabled(k == "iho")
        self.uconst.setEnabled(k == "const")
        s102 = self.rb_102.isChecked()
        for w_ in (self.zone, self.prod, self.dsname):
            w_.setEnabled(s102)

    def validate(self):
        if self.cmb.currentData() is None:
            return "No depth grid — build a DEM (Surface → Create DEM, method 'shoal') or run a CUBE filter first"
        if not os.path.isdir(self.folder.text()):
            return "Choose an existing output folder"
        return None

    def settings(self):
        return {"grid": self.cmb.currentData(), "unc": self.unc.currentData(), "order": self.order.currentData(),
                "uconst": self.uconst.value(), "datum": self.datum.currentData(),
                "format": "s102" if self.rb_102.isChecked() else "bag", "zone": self.zone.value(),
                "cell": self.cell.value() or None, "producer": self.prod.text(), "name": self.dsname.text(),
                "who": self.who.text(), "org": self.org.text(), "abstract": self.abstract.text(),
                "folder": self.folder.text()}


# ================================================================ chart layers
class ChartLayersDialog(_Base):
    def __init__(self, clouds, grids, current, has_sel, parent=None):
        super().__init__("Chart layers", "Shoal-biased chart content as S-57 objects: selected soundings (SOUNDG, "
                                        "VALSOU), depth contours (DEPCNT, VALDCO) and depth areas (DEPARE, DRVAL1 / "
                                        "DRVAL2). Depths are positive down below the layer's vertical datum.", parent)
        g = QGroupBox("Soundings (SOUNDG)")
        f = QFormLayout(g)
        self.do_snd = QCheckBox("Select soundings")
        self.do_snd.setChecked(True)
        f.addRow(self.do_snd)
        self.src = QComboBox()
        for i, n in clouds:
            self.src.addItem(f"Points: {n}", ("points", i))
        for i, n, m in grids:
            self.src.addItem(f"Grid: {n}  ({m})", ("grid", i))
        j = next((k for k in range(self.src.count()) if self.src.itemData(k)[1] == current), 0)
        self.src.setCurrentIndex(j)
        f.addRow("From", self.src)
        self.scope = QComboBox()
        self.scope.addItem("All active points", "all")
        if has_sel:
            self.scope.addItem("Selected points (edit target)", "selected")
        f.addRow("Scope", self.scope)
        self.scale = QSpinBox()
        self.scale.setRange(100, 5_000_000)
        self.scale.setSingleStep(1000)
        self.scale.setValue(10000)
        self.scale.setPrefix("1 : ")
        self.mm = _dsp(5.0, 0.5, 50.0, 0.5, 1, " mm")
        self.rad = QLabel()
        sr = QHBoxLayout()
        sr.addWidget(self.scale)
        sr.addWidget(_muted("spacing on the chart"))
        sr.addWidget(self.mm)
        f.addRow("Chart scale", sr)
        f.addRow("Minimum spacing", self.rad)
        self.v.addWidget(g)
        g2 = QGroupBox("Depth contours (DEPCNT) and depth areas (DEPARE)")
        f2 = QFormLayout(g2)
        self.do_cnt = QCheckBox("Depth contours")
        self.do_area = QCheckBox("Depth areas")
        self.do_cnt.setChecked(bool(grids))
        self.do_area.setChecked(bool(grids))
        hr = QHBoxLayout()
        hr.addWidget(self.do_cnt)
        hr.addWidget(self.do_area)
        f2.addRow(hr)
        self.grid = QComboBox()
        for i, n, m in grids:
            self.grid.addItem(f"{n}  ({m})", i)
        gi = self.grid.findData(current)
        self.grid.setCurrentIndex(max(gi, 0))
        f2.addRow("Surface", self.grid)
        from core.nautical import STANDARD_DEPTHS
        self.levels = QLineEdit(", ".join(f"{d:g}" for d in STANDARD_DEPTHS))
        f2.addRow("Depths (m)", self.levels)
        self.bias = QSpinBox()
        self.bias.setRange(0, 20)
        self.bias.setValue(1)
        self.bias.setSuffix(" cells")
        self.bias.setToolTip("Safety bias: before contouring, every cell takes the shoalest value within this many "
                             "cells — contours move into deeper water, never shallower")
        f2.addRow("Shoal bias", self.bias)
        self.simp = _dsp(0.0, 0.0, 1000.0, 0.5, 1, " m")
        self.simp.setSpecialValueText("off")
        f2.addRow("Simplify", self.simp)
        self.v.addWidget(g2)
        g3 = QGroupBox("Output")
        f3 = QFormLayout(g3)
        self.add = QCheckBox("Add to the project as vector layers")
        self.add.setChecked(True)
        f3.addRow(self.add)
        self.out = _file_row("Export chart layers", "Shapefile (*.shp);;GeoPackage (*.gpkg);;GeoJSON (*.geojson)",
                             save=True)
        self.out.edit.setPlaceholderText("optional — one file per object class (_SOUNDG, _DEPCNT, _DEPARE)")
        f3.addRow("Export", self.out)
        self.v.addWidget(g3)
        for w_ in (self.scale, self.mm):
            w_.valueChanged.connect(self._sync)
        self.src.currentIndexChanged.connect(self._sync)
        self._sync()

    def radius(self):
        return self.scale.value() * self.mm.value() / 1000.0

    def _sync(self):
        self.rad.setText(f"{self.radius():,.1f} m on the ground")
        self.scope.setEnabled(self.src.currentData() is not None and self.src.currentData()[0] == "points")

    def validate(self):
        if not (self.do_snd.isChecked() or self.do_cnt.isChecked() or self.do_area.isChecked()):
            return "Choose at least one chart layer"
        if self.do_snd.isChecked() and self.src.currentData() is None:
            return "No soundings: load a point cloud or build a DEM"
        if (self.do_cnt.isChecked() or self.do_area.isChecked()) and self.grid.currentData() is None:
            return "Contours and depth areas need a grid — build a DEM with the 'shoal' method first"
        try:
            self.depths()
        except ValueError:
            return "Depths: numbers separated by commas"
        if not self.add.isChecked() and not self.out.edit.text().strip():
            return "Add the layers to the project or choose an export file"
        return None

    def depths(self):
        vals = sorted({float(t) for t in self.levels.text().replace(";", ",").split(",") if t.strip()})
        if not vals:
            raise ValueError
        return vals

    def settings(self):
        return {"snd": self.do_snd.isChecked(), "src": self.src.currentData(), "scope": self.scope.currentData(),
                "radius": self.radius(), "cnt": self.do_cnt.isChecked(), "area": self.do_area.isChecked(),
                "grid": self.grid.currentData(), "levels": self.depths(), "bias": self.bias.value(),
                "simplify": self.simp.value(), "add": self.add.isChecked(), "out": self.out.edit.text().strip(),
                "scale": self.scale.value()}


# ================================================================ mixin
class NauticalMixin:
    def _vd_text(self, ds):
        vd = getattr(ds, "vdatum", None) or {}
        if vd.get("code"):
            return S100_VERTICAL_DATUMS.get(int(vd["code"]), ("", ""))[1] or S100_VERTICAL_DATUMS[int(vd["code"])][0]
        return ""

    # ---------------------------------------------------------------- vertical datum
    def open_vertical_datum(self):
        P = self.project
        layers = [(d.id, d.name, d.kind, len(d.obj) if d.kind == "points" else 0) for d in P.datasets
                  if d.kind == "points" or (d.kind == "grid" and getattr(d.obj, "cvals", None) is None)]
        if not layers:
            QMessageBox.information(self, "Vertical datum", "Load a point cloud or a grid first.")
            return
        if P.crs is None:
            QMessageBox.information(self, "Vertical datum", "Set the project coordinate system first.")
            return
        dlg = VerticalDatumDialog(layers, self._bound_id, self)
        if dlg.exec() != QDialog.Accepted:
            return
        ds = P.get(dlg.cmb.currentData())
        sep = dlg.sep.separation(P.crs)
        code = int(dlg.datum.currentData())
        abbr = S100_VERTICAL_DATUMS[code][1] or S100_VERTICAL_DATUMS[code][0]
        vd = {"code": code, "name": S100_VERTICAL_DATUMS[code][0], "separation": sep.spec(),
              "from": (ds.vdatum or {}).get("name") or "survey datum"}
        from core import datum as dm
        try:
            if ds.kind == "grid":
                dem, miss = dm.apply_dem(ds.obj, sep)
                from core.project import Dataset
                nd = Dataset(P.unique_name(f"{ds.name} [{abbr}]"), "grid", dem, crs=P.crs, source_crs=P.crs,
                             derived_from=ds.id, edit_version=ds.edit_version, vdatum=vd)
                P.add(nd)
                self._project_changed()
                self._set_dirty(True)
                self.statusBar().showMessage(f"{nd.name}: heights relative to {abbr} ({sep.describe()})"
                                             + (f" · {miss:,} cells without a separation value left empty" if miss
                                                else ""), 12000)
                return
            pc = ds.obj
            if dlg.inplace.isChecked():
                if ds.vdatum.get("in_place"):
                    QMessageBox.warning(self, "Vertical datum", f"The heights of {ds.name} were already shifted to "
                                        f"{ds.vdatum.get('name')} — shift a copy instead, or reload the file.")
                    return
                miss = dm.apply_xyz(pc.xyz, sep)
                pc.recompute_bbox()
                ds.vdatum = {**vd, "in_place": True}
                ds.edit_version += 1
                if ds.id == self._bound_id:
                    self.viewer.refresh()
                else:
                    self.viewer.remove_reference(ds.id)
                self._project_changed()
                self._set_dirty(True)
            else:
                from core.pointcloud import PointCloud
                from core.project import Dataset
                act = pc.active_indices()
                xyz = pc.xyz[act].copy()
                miss = dm.apply_xyz(xyz, sep)
                attrs = {k: np.asarray(v)[act] for k, v in pc.attrs.items()}
                npc = PointCloud(xyz, attrs, {"type": "derived", "vertical_datum": vd["name"]},
                                 name=f"{ds.name} [{abbr}]")
                nd = Dataset(P.unique_name(f"{ds.name} [{abbr}]"), "points", npc, crs=P.crs, source_crs=P.crs,
                             derived_from=ds.id, vdatum=vd)
                P.add(nd)
                self._project_changed()
                self._set_dirty(True)
            self.statusBar().showMessage(f"Heights relative to {abbr} ({sep.describe()})"
                                         + (f" · {miss:,} points without a separation value were NOT shifted"
                                            if miss else ""), 12000)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Vertical datum", f"{type(e).__name__}: {e}")

    # ---------------------------------------------------------------- BAG / S-102
    def _grid_rows(self):
        out = []
        for d in self.project.of_kind("grid"):
            o = d.obj
            out.append((d.id, d.name, o.method, o.cell, getattr(o, "cvals", None) is not None,
                        (d.vdatum or {}).get("code")))
        return out

    def open_grid_product(self):
        P = self.project
        grids = self._grid_rows()
        if not any(not r[4] for r in grids):
            QMessageBox.information(self, "BAG / S-102", "Build a depth grid first: Surface → Create DEM (method "
                                    "'shoal'), or a CUBE filter with grid layers.")
            return
        if P.crs is None:
            QMessageBox.information(self, "BAG / S-102", "Set the project coordinate system first.")
            return
        cur = next((r[0] for r in grids if not r[4] and r[2] in ("shoal", "cube")), None)
        dlg = GridProductDialog(grids, cur, "SURFACE", self)
        if dlg.exec() != QDialog.Accepted:
            return
        s = dlg.settings()
        ds = P.get(s["grid"])
        dem = ds.obj
        kind, uid = s["unc"]
        if kind == "grid":
            ud = P.get(uid).obj
            uz = ud.cvals if getattr(ud, "cvals", None) is not None else ud.z
            if uz.shape != dem.z.shape or abs(ud.x0 - dem.x0) > 1e-6 or abs(ud.ytop - dem.ytop) > 1e-6 \
                    or abs(ud.cell - dem.cell) > 1e-9:
                QMessageBox.warning(self, "BAG / S-102", "The uncertainty grid must have the same cells as the "
                                    "depth grid.")
                return
            unc = np.asarray(uz, np.float32)
        elif kind == "iho":
            from core.nautical import iho_uncertainty
            unc = iho_uncertainty(dem.z, s["order"])
        elif kind == "const":
            unc = np.where(np.isfinite(dem.z), s["uconst"], np.nan).astype(np.float32)
        else:
            unc = None
        crs = P.crs

        def job(progress=None):
            from core import nautical as N
            if s["format"] == "s102":
                from fileio.s102_writer import product_name, write_s102
                from fileio.validate_s102 import check
                if s["zone"]:
                    lon, lat = N.lonlat_center(dem, crs)
                    epsg = (32600 if lat >= 0 else 32700) + int(s["zone"])
                else:
                    epsg = N.utm_epsg(*N.lonlat_center(dem, crs))
                g = N.product_grid(dem, unc, crs, epsg, s["cell"] or dem.cell,
                                   progress=lambda f: progress and progress(0.5 * f))
                path = os.path.join(s["folder"], product_name(s["producer"], s["name"]))
                warn = write_s102(path, g["z"], g["unc"], g["transform"], g["epsg"], s["datum"],
                                  progress=lambda f: progress and progress(0.5 + 0.4 * f))
                errors, warnings, _done = check(path)
                return {"path": path, "format": "S-102", "epsg": g["epsg"], "shape": g["z"].shape, "cell": g["cell"],
                        "warn": warn + [w for w in warnings if "Quality feature" not in w and "GDAL not" not in w],
                        "errors": [f"{c}: {m}" for c, m in errors]}
            from fileio.bag_writer import write_bag
            g = N.product_grid(dem, unc, crs, None, s["cell"])
            base = s["name"] or ds.name
            path = os.path.join(s["folder"], "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in base)
                                + ".bag")
            write_bag(path, g["z"], g["unc"], g["transform"], crs, S100_VERTICAL_DATUMS[int(s["datum"])][0],
                      s["who"], s["org"], s["abstract"],
                      f"PointCloud Studio: {dem.method} grid {dem.cell:g} m; uncertainty: {kind}")
            return {"path": path, "format": "BAG", "epsg": None, "shape": g["z"].shape, "cell": g["cell"],
                    "warn": [], "errors": []}

        self._run_task("Writing bathymetric surface…", job, (), {}, self._on_grid_product, report_progress=True)

    def _on_grid_product(self, r):
        self._close_progress()
        txt = (f"{r['format']}: {os.path.basename(r['path'])} — {r['shape'][1]:,} × {r['shape'][0]:,} cells of "
               f"{r['cell']:g} m" + (f", EPSG:{r['epsg']}" if r["epsg"] else ""))
        if r["format"] == "S-102":
            txt += ("\n\nValidation (GDAL validate_s102): " + ("passed." if not r["errors"] else
                                                              "FAILED:\n" + "\n".join(r["errors"][:10])))
        if r["warn"]:
            txt += "\n\nWarnings:\n" + "\n".join(r["warn"][:10])
        (QMessageBox.warning if r["errors"] else QMessageBox.information)(self, "Bathymetric surface", txt)

    # ---------------------------------------------------------------- chart layers
    def open_chart_layers(self):
        P = self.project
        clouds = [(d.id, d.name) for d in P.points]
        grids = [(d.id, d.name, d.obj.method) for d in P.of_kind("grid") if getattr(d.obj, "cvals", None) is None]
        if not clouds and not grids:
            QMessageBox.information(self, "Chart layers", "Load soundings (a point cloud) or build a DEM first.")
            return
        cur = self._bound_id if self._bound_id is not None else (grids[0][0] if grids else None)
        shoal = next((g[0] for g in grids if g[2] in ("shoal", "cube")), None)
        dlg = ChartLayersDialog(clouds, grids, shoal if self._bound_id is None and shoal else cur,
                                self.sel is not None and self.sel.count > 0, self)
        if dlg.exec() != QDialog.Accepted:
            return
        s = dlg.settings()
        src_xyz = None
        vd_label = ""
        if s["snd"]:
            kind, sid = s["src"]
            sd = P.get(sid)
            vd_label = self._vd_text(sd)
            if kind == "points":
                pc = sd.obj
                if s["scope"] == "selected" and sd.id == self._bound_id:
                    idx = np.flatnonzero(self.sel.mask & pc.active_mask())
                else:
                    idx = pc.active_indices()
                src_xyz = pc.xyz[idx]
            else:
                d = sd.obj
                xs, ys = d.centers()
                X, Y = np.meshgrid(xs, ys)
                if d.has_true_position:
                    X = np.where(np.isfinite(d.tx), d.tx, X)
                    Y = np.where(np.isfinite(d.ty), d.ty, Y)
                ok = np.isfinite(d.z)
                src_xyz = np.column_stack([X[ok], Y[ok], d.z[ok]])
        gdem = P.get(s["grid"]).obj if (s["cnt"] or s["area"]) and s["grid"] is not None else None
        if gdem is not None and not vd_label:
            vd_label = self._vd_text(P.get(s["grid"]))

        def job(progress=None):
            from core import nautical as N
            out = {}
            if src_xyz is not None:
                sel = N.select_soundings(src_xyz[:, 0], src_xyz[:, 1], src_xyz[:, 2], s["radius"],
                                         progress=lambda f: progress and progress(0.6 * f))
                out["SOUNDG"] = N.soundings_features(src_xyz[:, 0], src_xyz[:, 1], src_xyz[:, 2], sel, vd_label)
            if gdem is not None and s["cnt"]:
                out["DEPCNT"] = N.depth_contours(gdem, s["levels"], s["bias"], s["simplify"])
                for f in out["DEPCNT"]:
                    f["attrs"]["VERDAT"] = vd_label
            if progress:
                progress(0.8)
            if gdem is not None and s["area"]:
                out["DEPARE"] = N.depth_areas(gdem, s["levels"], s["bias"], s["simplify"])
                for f in out["DEPARE"]:
                    f["attrs"]["VERDAT"] = vd_label
            return out

        self._chart_ctx = s
        self._run_task("Building chart layers…", job, (), {}, self._on_chart_layers, report_progress=True)

    def _on_chart_layers(self, out):
        from core.project import Dataset
        from core.vector_layer import VectorLayer
        self._close_progress()
        s = self._chart_ctx
        P = self.project
        colors = {"SOUNDG": "#e6e6e6", "DEPCNT": "#4cc9f0", "DEPARE": "#2a7fbf"}
        labels = {"SOUNDG": "VALSOU", "DEPCNT": "VALDCO", "DEPARE": None}
        if s["add"]:
            for k, feats in out.items():
                if feats:
                    P.add(Dataset(P.unique_name(f"Chart {k} 1:{s['scale']:,}"), "vector",
                                  VectorLayer(feats, meta={"chart": k}), crs=P.crs, source_crs=P.crs,
                                  style={"color": colors[k], "label": labels[k]}))
            self._project_changed()
            self._set_dirty(True)
        if s["out"]:
            self._write_feature_groups(s["out"], {k: v for k, v in out.items() if v})
        self.statusBar().showMessage(" · ".join(f"{k} {len(v):,}" for k, v in out.items()), 12000)
