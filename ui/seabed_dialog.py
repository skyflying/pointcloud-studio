"""Cable & Foundations → scour monitoring and sand-wave migration (non-modal).

cb = SeabedMixin: seabed_run(kind, spec), seabed_set_date(ds_id, text), seabed_export_csv(),
seabed_export_shp(), seabed_save_chart(), seabed_add_layer(structure, epoch), seabed_export_tifs(),
seabed_clear_overlay(), seabed_goto(row), import_structures().
"""
from __future__ import annotations

import re

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QRadioButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from render.colormap import get_lut
from ui.cable_dialog import GREEN, GREY, ORANGE, RED, KPChart, _dsp, _fmt, _muted, _section
from ui.theme import C, dark_titlebar

EPOCH_COLORS = ["#8b919c", "#4cc9f0", "#ffd166", "#ef476f", "#06d6a0", "#b8b8ff", "#ff9f1c", "#f72585"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DepthMap(QWidget):
    """Scour depth grid of one structure (blue = deeper than the reference), with the pile and r_max circles."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(260, 260)
        self.img = None
        self.info = None

    def set_grid(self, depth, rec, vmax):
        self.info = (rec, vmax)
        if depth is None:
            self.img = None
            self.update()
            return
        lut = (np.asarray(get_lut("Diverging")) * 255).astype(np.uint8)
        t = np.clip(0.5 - np.nan_to_num(depth) / (2 * max(vmax, 1e-3)), 0, 1)    # deep → blue end
        rgb = lut[(t * 255).astype(int)][..., :3]
        a = np.where(np.isfinite(depth), 255, 0).astype(np.uint8)
        rgba = np.ascontiguousarray(np.dstack([rgb, a]))
        h, w = depth.shape
        self.img = QImage(rgba.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(C["surface"]))
        if self.img is None or self.info is None:
            p.end()
            return
        rec, vmax = self.info
        s = min(self.width(), self.height()) - 30
        x0, y0 = (self.width() - s) / 2, 6
        p.drawImage(QRectF(x0, y0, s, s), self.img)
        scale = s / (rec["n"] * rec["cell"])
        cx, cy = x0 + s / 2, y0 + s / 2
        for r, col in ((rec["diameter"] / 2, "#ffffff"), (rec["r_max"], C["muted"])):
            p.setPen(QPen(QColor(col), 1, Qt.DashLine if col != "#ffffff" else Qt.SolidLine))
            p.drawEllipse(QRectF(cx - r * scale, cy - r * scale, 2 * r * scale, 2 * r * scale))
        p.setPen(QPen(QColor(C["muted"]), 1))
        f = p.font()
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1.5))
        p.setFont(f)
        p.drawText(QRectF(0, y0 + s + 4, self.width(), 16), Qt.AlignHCenter,
                   f"N ↑   blue = scour (to {vmax:.1f} m)   red = above reference   dashed: r max")
        p.end()


class EpochTable(QTableWidget):
    """Surveys to use: checkbox, name, survey date (editable, YYYY-MM-DD)."""

    def __init__(self, on_date):
        super().__init__(0, 3)
        self.on_date = on_date
        self.setHorizontalHeaderLabels(["Use", "Survey (surface)", "Date (YYYY-MM-DD)"])
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.setMinimumHeight(130)
        self._ids = []
        self._filling = False
        self.itemChanged.connect(self._changed)

    def fill(self, items):
        """items: [(id, name, date)]"""
        keep = {self._ids[r]: self.item(r, 0).checkState() == Qt.Checked for r in range(self.rowCount())
                if self.item(r, 0) is not None}
        self._filling = True
        self.setRowCount(len(items))
        self._ids = [i for i, _n, _d in items]
        for r, (i, n, d) in enumerate(items):
            c = QTableWidgetItem()
            c.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            c.setCheckState(Qt.Checked if keep.get(i, True) else Qt.Unchecked)
            self.setItem(r, 0, c)
            it = QTableWidgetItem(n)
            it.setFlags(Qt.ItemIsEnabled)
            self.setItem(r, 1, it)
            self.setItem(r, 2, QTableWidgetItem(d or ""))
        self.resizeColumnToContents(0)
        self._filling = False

    def _changed(self, it):
        if self._filling or it.column() != 2:
            return
        t = it.text().strip()
        if t and not DATE_RE.match(t):
            it.setForeground(QColor(C["danger"]))
            return
        it.setForeground(QColor(C["text"]))
        self.on_date(self._ids[it.row()], t or None)

    def chosen(self):
        """[(id, date or None)] of checked rows, sorted by date (undated last, table order)."""
        rows = []
        for r in range(self.rowCount()):
            if self.item(r, 0).checkState() != Qt.Checked:
                continue
            t = self.item(r, 2).text().strip()
            rows.append((self._ids[r], t if DATE_RE.match(t) else None, r))      # invalid date = no date
        rows.sort(key=lambda t: (t[1] is None, t[1] or "", t[2]))
        return [(i, d) for i, d, _r in rows]


class SeabedDialog(QDialog):
    TABS = ("scour", "sandwave")

    def __init__(self, cb, parent=None):
        super().__init__(parent)
        self.cb = cb
        self.depth = False
        self.res = None
        self.setWindowTitle("Scour & sand waves")
        self.setModal(False)
        self.resize(720, 980)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("dialogBody")
        scroll.setWidget(body)
        outer.addWidget(scroll)
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(7)
        t = QLabel("Scour & sand waves")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Repeated surveys as epochs, ordered by survey date. Set the dates in the tables "
                           "(or Data panel → ⋯ → Survey date) — rates per year need them."))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_scour(), "Scour monitoring")
        self.tabs.addTab(self._tab_sw(), "Sand wave migration")
        v.addWidget(self.tabs)
        row = QHBoxLayout()
        self.lbl_msg = QLabel(" ")
        self.lbl_msg.setWordWrap(True)
        row.addWidget(self.lbl_msg, 1)
        self.btn_run = QPushButton("Run analysis")
        self.btn_run.setProperty("primary", True)
        self.btn_run.clicked.connect(self._run)
        row.addWidget(self.btn_run)
        v.addLayout(row)

        v.addWidget(_section("Result"))
        self.lbl_sum = QLabel("Run an analysis to see the result.")
        self.lbl_sum.setWordWrap(True)
        self.lbl_sum.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_sum)
        srow = QHBoxLayout()
        self.cmb_struct = QComboBox()
        self.cmb_epoch = QComboBox()
        for w_ in (self.cmb_struct, self.cmb_epoch):
            w_.currentIndexChanged.connect(self._scour_view)
        srow.addWidget(QLabel("Structure"))
        srow.addWidget(self.cmb_struct, 1)
        srow.addWidget(QLabel("Epoch"))
        srow.addWidget(self.cmb_epoch, 1)
        self.w_srow = QWidget()
        self.w_srow.setLayout(srow)
        self.w_srow.setVisible(False)
        v.addWidget(self.w_srow)
        self.map = DepthMap()
        self.map.setVisible(False)
        v.addWidget(self.map)
        self.chart = KPChart()
        v.addWidget(self.chart)
        trow = QHBoxLayout()
        self.cmb_table = QComboBox()
        self.cmb_table.currentIndexChanged.connect(self._fill_table)
        trow.addWidget(self.cmb_table)
        trow.addStretch()
        v.addLayout(trow)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(220)
        self.table.cellDoubleClicked.connect(self._row_clicked)
        v.addWidget(self.table)
        brow = QHBoxLayout()
        self.btn_csv = QPushButton("Export CSV…")
        self.btn_shp = QPushButton("Export GIS…")
        self.btn_png = QPushButton("Save chart…")
        self.btn_layer = QPushButton("Add depth layer")
        self.btn_layer.setToolTip("Scour depth grid of the chosen structure and epoch as a project layer")
        self.btn_tifs = QPushButton("Depth GeoTIFFs…")
        self.btn_tifs.setToolTip("One scour-depth GeoTIFF per structure (chosen epoch) into a folder")
        self.btn_clear = QPushButton("Clear 3D")
        for b, fn in ((self.btn_csv, cb.seabed_export_csv), (self.btn_shp, cb.seabed_export_shp),
                      (self.btn_png, cb.seabed_save_chart),
                      (self.btn_layer, lambda: cb.seabed_add_layer(self.cmb_struct.currentIndex(),
                                                                   self.cmb_epoch.currentIndex())),
                      (self.btn_tifs, lambda: cb.seabed_export_tifs(self.cmb_epoch.currentIndex())),
                      (self.btn_clear, cb.seabed_clear_overlay)):
            b.clicked.connect(fn)
            b.setEnabled(False)
            brow.addWidget(b)
        v.addLayout(brow)
        v.addWidget(_muted("Double-click a row to go to it in the 3D view."))

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    # ---------------------------------------------------------------- tabs
    def _tab_scour(self):
        w = QWidget()
        f = QFormLayout(w)
        srow = QHBoxLayout()
        self.c_struct = QComboBox()
        b = QPushButton("Import…")
        b.clicked.connect(self.cb.import_structures)
        srow.addWidget(self.c_struct, 1)
        srow.addWidget(b)
        f.addRow("Structures", srow)
        self.c_which = QComboBox()
        self.c_which.setToolTip("All structures of the layer, or one")
        f.addRow("Analyse", self.c_which)
        self.c_struct.currentIndexChanged.connect(self._fill_which)
        self.c_epochs = EpochTable(self.cb.seabed_set_date)
        f.addRow(_section("Surveys (DEM / TIN)"))
        f.addRow(self.c_epochs)
        self.c_grp = QButtonGroup(self)
        self.c_ff = QRadioButton("Far field: median seabed of each survey in a ring around the pile")
        self.c_srf = QRadioButton("Baseline surface (e.g. pre-installation survey)")
        self.c_lvl = QRadioButton("Fixed level")
        self.c_ff.setChecked(True)
        for rb in (self.c_ff, self.c_srf, self.c_lvl):
            self.c_grp.addButton(rb)
        f.addRow(_section("Reference level"))
        f.addRow(self.c_ff)
        h = QHBoxLayout()
        self.c_ffin = _dsp(8.0, 1.0, 100.0, 1.0, 1, " D")
        self.c_ffout = _dsp(10.0, 1.5, 200.0, 1.0, 1, " D")
        h.addWidget(QLabel("ring"))
        h.addWidget(self.c_ffin)
        h.addWidget(QLabel("–"))
        h.addWidget(self.c_ffout)
        h.addStretch()
        f.addRow(h)
        f.addRow(self.c_srf)
        self.c_base = QComboBox()
        f.addRow("  Baseline", self.c_base)
        f.addRow(self.c_lvl)
        from ui.design_dialog import LevelInput
        self.c_level = LevelInput(-20.0)
        f.addRow("  Level", self.c_level)
        f.addRow(_section("Analysis"))
        self.c_rmax = _dsp(5.0, 1.0, 50.0, 0.5, 1, " D")
        self.c_rmax.setToolTip("Analysis radius in pile diameters")
        self.c_thr = _dsp(0.5, 0.0, 20.0, 0.1, 2)
        self.c_thr.setToolTip("Scour area / extent: depth below the reference greater than this")
        self.c_rays = QSpinBox(minimum=4, maximum=72, value=16)
        self.c_margin = _dsp(0.0, 0.0, 10.0, 0.1, 2)
        self.c_margin.setToolTip("Excluded distance outside the pile wall (scour protection edge, data shadow)")
        self.c_cell = _dsp(0.0, 0.0, 5.0, 0.05, 2)
        self.c_cell.setSpecialValueText("auto (D / 40)")
        f.addRow("Analysis radius", self.c_rmax)
        f.addRow("Scour threshold", self.c_thr)
        f.addRow("Radial profiles", self.c_rays)
        f.addRow("Exclude beyond pile wall", self.c_margin)
        f.addRow("Grid cell", self.c_cell)

        def sync():
            self.c_ffin.setEnabled(self.c_ff.isChecked())
            self.c_ffout.setEnabled(self.c_ff.isChecked())
            self.c_base.setEnabled(self.c_srf.isChecked())
            self.c_level.setEnabled(self.c_lvl.isChecked())
        for rb in (self.c_ff, self.c_srf, self.c_lvl):
            rb.toggled.connect(sync)
        sync()
        return w

    def _tab_sw(self):
        w = QWidget()
        f = QFormLayout(w)
        self.w_route = QComboBox()
        f.addRow("Route", self.w_route)
        self.w_epochs = EpochTable(self.cb.seabed_set_date)
        f.addRow(_section("Surveys (DEM / TIN / point cloud) — at least two, with dates"))
        f.addRow(self.w_epochs)
        self.w_m2d = QRadioButton("2D surface matching (direction + speed)")
        self.w_m1d = QRadioButton("Profile along the route (along-route shift only)")
        self.w_m2d.setChecked(True)
        g = QButtonGroup(self)
        for rb in (self.w_m2d, self.w_m1d):
            g.addButton(rb)
            f.addRow(rb)
        self.w_win = _dsp(200.0, 20.0, 5000.0, 25.0, 0)
        self.w_win.setToolTip("Window length — about one to two bedform wavelengths")
        self.w_spc = _dsp(50.0, 5.0, 5000.0, 25.0, 0)
        self.w_max = _dsp(40.0, 1.0, 1000.0, 5.0, 0)
        self.w_max.setToolTip("Largest shift searched between two surveys — keep it below half a wavelength")
        self.w_hp = _dsp(400.0, 20.0, 20000.0, 50.0, 0)
        self.w_hp.setToolTip("Long-wave (regional) seabed: running mean over this length; bedforms = the rest")
        self.w_cell = _dsp(1.0, 0.1, 20.0, 0.5, 2)
        self.w_step = _dsp(1.0, 0.1, 20.0, 0.5, 2)
        self.w_corr = _dsp(0.6, 0.0, 1.0, 0.05, 2, "")
        self.w_hmin = _dsp(0.3, 0.0, 20.0, 0.1, 2)
        f.addRow("Window", self.w_win)
        f.addRow("Window spacing", self.w_spc)
        f.addRow("Maximum shift", self.w_max)
        f.addRow("Long-wave filter", self.w_hp)
        f.addRow("Grid cell (2D)", self.w_cell)
        f.addRow("Profile step", self.w_step)
        f.addRow("Minimum correlation", self.w_corr)
        f.addRow("Minimum bedform height", self.w_hmin)
        f.addRow(_section("Burial forecast (optional)"))
        self.w_cable = QComboBox()
        self.w_target = _dsp(1.0, 0.0, 20.0, 0.1, 2)
        self.w_hz = QComboBox()
        self.w_hz.setEditable(True)
        self.w_hz.addItems(["1, 2, 5, 10", "1, 5, 10, 20", "1, 2, 3, 4, 5"])
        f.addRow("Cable profile", self.w_cable)
        f.addRow("Target DoB", self.w_target)
        f.addRow("Horizons (years)", self.w_hz)
        f.addRow(_muted("Linear extrapolation of the observed along-route migration with unchanged bedform shape — "
                        "an estimate for planning surveys, not a prediction."))
        return w

    # ---------------------------------------------------------------- context
    def set_context(self, structures, surfaces, clouds, routes, cables, depth):
        """structures: [(id, name, names)], surfaces/clouds: [(id, name, date)], routes/cables: [(id, name)]"""
        self.depth = depth
        self._structs = {i: names for i, _n, names in structures}

        def fill(cmb, items, none=False):
            cur = cmb.currentData()
            cmb.blockSignals(True)
            cmb.clear()
            if none:
                cmb.addItem("(none)", None)
            for it in items:
                cmb.addItem(it[1], it[0])
            j = cmb.findData(cur)
            cmb.setCurrentIndex(j if j >= 0 else 0)
            cmb.blockSignals(False)

        fill(self.c_struct, [(i, n) for i, n, _x in structures])
        self._fill_which()
        self.c_epochs.fill(surfaces)
        fill(self.c_base, [(i, n) for i, n, _d in surfaces])
        fill(self.w_route, routes)
        self.w_epochs.fill(surfaces + clouds)
        fill(self.w_cable, cables, none=True)

    def _fill_which(self, *_):
        names = self._structs.get(self.c_struct.currentData(), []) if hasattr(self, "_structs") else []
        cur = self.c_which.currentText()
        self.c_which.blockSignals(True)
        self.c_which.clear()
        self.c_which.addItem(f"All ({len(names)})", -1)
        for k, n in enumerate(names):
            self.c_which.addItem(n, k)
        j = self.c_which.findText(cur)
        self.c_which.setCurrentIndex(max(j, 0))
        self.c_which.blockSignals(False)

    # ---------------------------------------------------------------- run
    def show_error(self, msg):
        self.lbl_msg.setText(f"<span style='color:{C['danger']}'>●  {msg}</span>")

    def _run(self):
        self.lbl_msg.setText(" ")
        kind = self.TABS[self.tabs.currentIndex()]
        if kind == "scour":
            if self.c_struct.currentData() is None:
                return self.show_error("Import structures first (wind turbines / piles with their diameter)")
            eps = self.c_epochs.chosen()
            if not eps:
                return self.show_error("Tick at least one survey")
            if self.c_srf.isChecked():
                base = {"mode": "surface", "id": self.c_base.currentData()}
                if base["id"] is None:
                    return self.show_error("Choose the baseline surface")
            elif self.c_lvl.isChecked():
                lv = self.c_level.sp.value()
                base = {"mode": "level", "level": -lv if self.c_level.mode() == "depth" else lv}
            else:
                if self.c_ffout.value() <= self.c_ffin.value():
                    return self.show_error("The far-field ring must have an outer radius larger than the inner")
                base = {"mode": "farfield", "ff_in": self.c_ffin.value(), "ff_out": self.c_ffout.value()}
            spec = {"structures": self.c_struct.currentData(), "which": self.c_which.currentData(), "epochs": eps,
                    "baseline": base, "r_max": self.c_rmax.value(), "threshold": self.c_thr.value(),
                    "n_rays": self.c_rays.value(), "margin": self.c_margin.value(),
                    "cell": self.c_cell.value() or None}
        else:
            if self.w_route.currentData() is None:
                return self.show_error("Import a route first")
            eps = self.w_epochs.chosen()
            if len(eps) < 2:
                return self.show_error("Tick at least two surveys")
            try:
                hz = [float(x) for x in re.split(r"[,\s;]+", self.w_hz.currentText().strip()) if x]
            except ValueError:
                return self.show_error("Horizons: numbers of years, e.g. 1, 2, 5, 10")
            spec = {"route": self.w_route.currentData(), "epochs": eps, "method": "2d" if self.w_m2d.isChecked()
                    else "profile", "window": self.w_win.value(), "spacing": self.w_spc.value(),
                    "max_shift": self.w_max.value(), "hp_len": self.w_hp.value(), "cell": self.w_cell.value(),
                    "step": self.w_step.value(), "min_corr": self.w_corr.value(), "min_height": self.w_hmin.value(),
                    "cable": self.w_cable.currentData(), "target": self.w_target.value(), "horizons": hz}
            if spec["max_shift"] >= spec["window"] / 2:
                return self.show_error("Maximum shift must be smaller than half the window")
        self.cb.seabed_run(kind, spec)

    # ---------------------------------------------------------------- results
    def show_result(self, res, depth, notes=(), title=None):
        self.res = res
        self.depth = depth
        k = res["kind"]
        self.cmb_table.blockSignals(True)
        self.cmb_table.clear()
        if k == "scour":
            tab = res["table"]
            last = {}
            for r in tab:
                last[r["structure"]] = r
            lastv = list(last.values())
            mx = max(lastv, key=lambda r: (np.nan_to_num(r["max_depth"], nan=-1e9)))
            rows = [("Structures", f"{len(res['structures'])} · surveys: {', '.join(res['params']['epochs'])}"),
                    ("Reference", {"farfield": "far field ring {ff_in:g}–{ff_out:g} D", "surface": "baseline surface",
                                   "level": "level {level:g} m"}[res["params"]["baseline"]["mode"]].format(
                                       **res["params"]["baseline"])),
                    ("Deepest scour (latest survey)", f"<span style='color:{RED}'>{_fmt(mx['max_depth'])} m</span> at "
                     f"{mx['structure']} ({_fmt(mx['r_at_max'], 1)} m, bearing {_fmt(mx['bearing_at_max'], 0)}°)"),
                    ("Largest extent", "{} m".format(_fmt(max((r["extent"] for r in lastv), default=np.nan), 1))),
                    ("Structures with scour > threshold",
                     str(sum(1 for r in lastv if np.isfinite(r["extent"]) and r["extent"] > 0)))]
            low = [r["structure"] for r in lastv if r["coverage_pct"] < 80]
            if low:
                rows.append(("Coverage < 80 %", ", ".join(low[:10]) + (" …" if len(low) > 10 else "")))
            self.cmb_table.addItem("Per structure and survey", "table")
            self.cmb_struct.blockSignals(True)
            self.cmb_epoch.blockSignals(True)
            self.cmb_struct.clear()
            self.cmb_epoch.clear()
            for s in res["structures"]:
                self.cmb_struct.addItem(s["name"])
            for e in res["params"]["epochs"]:
                self.cmb_epoch.addItem(e)
            self.cmb_epoch.setCurrentIndex(self.cmb_epoch.count() - 1)
            self.cmb_struct.blockSignals(False)
            self.cmb_epoch.blockSignals(False)
        else:
            s = res["summary"]
            p = res["params"]
            rows = [("Surveys", " → ".join(f"{n} ({d or 'no date'})" for n, d in
                                            zip(res["epoch_names"], res["epoch_dates"]))),
                    ("Method", f"{s['method']} · {s['windows']} window(s) of {p['window']:g} m every {p['spacing']:g} m"),
                    ("Reliable windows (first → last)", f"{s['reliable_windows']} of {s['windows']}"),
                    ("Migration rate", f"median {_fmt(s['rate_median'])} m/yr, max {_fmt(s['rate_max'])} m/yr"
                     + (f", towards {_fmt(s['azimuth_median'], 0)}°" if s["method"] == "2D" else "")),
                    ("Along the route", f"{_fmt(s['rate_along_median'])} m/yr (+ = towards higher KP); pattern "
                                        f"passes the route at {_fmt(s.get('rate_apparent_median'))} m/yr"),
                    ("Bedform height", f"median {_fmt(s['bedform_height_median'])} m · {s['crests']} crest(s)")]
            if not np.isfinite(s.get("years", np.nan)):
                rows.append(("Note", f"<span style='color:{RED}'>survey dates missing — shifts only, no rates or "
                                     "forecast</span>"))
            if res["forecast"]:
                f_ = res["forecast"][-1]
                rows.append((f"Forecast {f_['years']:g} yr", f"min DoB {_fmt(f_['dob_min'])} m · exposed "
                                                            f"{f_['exposed_m']:,.0f} m · below target "
                                                            f"{f_['below_target_m']:,.0f} m"))
                if "first_exposure_years" in s:
                    rows.append(("First exposure", f"<span style='color:{RED}'>after ~{s['first_exposure_years']:g} "
                                                   f"yr</span> ({s['exposure_segments']} segment(s))"))
            for key, label in (("windows", "Migration windows"), ("crests", "Crests (first → last)"),
                               ("forecast", "Burial forecast"), ("segments", "Exposure risk segments")):
                if res.get(key):
                    self.cmb_table.addItem(label, key)
        rows += [("Note", n) for n in notes]
        self.cmb_table.blockSignals(False)
        title = title or res.get("route") or "Result"
        self.lbl_sum.setText(f"<b>{title}</b><br>" + "<br>".join(
            f"<span style='color:{C['muted']}'>{a}:</span> {b}" for a, b in rows))
        self.w_srow.setVisible(k == "scour")
        self.map.setVisible(k == "scour")
        if k == "scour":
            self._scour_view()
        else:
            self._sw_chart()
        self._fill_table()
        for b in (self.btn_csv, self.btn_shp, self.btn_png, self.btn_clear):
            b.setEnabled(True)
        self.btn_layer.setEnabled(k == "scour")
        self.btn_tifs.setEnabled(k == "scour")

    def _scour_view(self, *_):
        r = self.res
        if r is None or r["kind"] != "scour" or not r["structures"]:
            return
        s = r["structures"][max(self.cmb_struct.currentIndex(), 0)]
        ei = min(max(self.cmb_epoch.currentIndex(), 0), len(s["epochs"]) - 1)
        e = s["epochs"][ei]
        allmax = [np.nanmax(np.abs(x["depth"])) for x in s["epochs"] if np.isfinite(x["depth"]).any()]
        self.map.set_grid(e["depth"], s, max(allmax) if allmax else 1.0)
        # radial profiles: mean (line) and deepest ray of each epoch
        panels_lines = []
        for k, ep in enumerate(s["epochs"]):
            d = ep["ray_depth"]
            col = EPOCH_COLORS[k % len(EPOCH_COLORS)] if len(s["epochs"]) > 1 else ORANGE
            with np.errstate(all="ignore"):
                panels_lines.append((f"{ep['name']} mean", col, -np.nanmean(d, axis=0)))
                panels_lines.append((f"{ep['name']} deepest", col, -np.nanmax(d, axis=0)))
        thr = r["params"]["threshold"]
        self.chart.setMinimumHeight(240)
        self.chart.set_data(s["r"], [{"lines": panels_lines, "ylabel": "Seabed relative to reference (m)",
                                      "hlines": [(0.0, GREY, "reference"), (-thr, RED, f"−{thr:g} m threshold")]}],
                            xlabel="r (m)", xdec=1)

    def _sw_chart(self):
        r = self.res
        se = r["series"]
        kp = r["st"]["kp"]
        ne = len(r["epoch_names"])
        prof = [(n, EPOCH_COLORS[i % len(EPOCH_COLORS)], (-se[f"z_{i}"] if self.depth else se[f"z_{i}"]))
                for i, n in enumerate(r["epoch_names"])]
        main = [w for w in r["windows"] if w["a"] == 0 and w["b"] == ne - 1]
        wk = np.array([w["kp"] for w in main])
        o = np.argsort(wk)
        rel = np.array([bool(w.get("reliable")) for w in main])[o]
        ra = np.array([w.get("rate_along", np.nan) for w in main], float)[o]
        rt = np.array([w.get("rate", np.nan) for w in main], float)[o]
        bands = [(g["kp_from"], g["kp_to"], RED) for g in r.get("segments", [])]
        panels = [{"lines": prof, "ylabel": "Depth (m)" if self.depth else "Elevation (m)", "bands": bands},
                  {"x": wk[o], "lines": [("rate", ORANGE, np.where(rel, rt, np.nan)),
                                         ("along route", GREEN, np.where(rel, ra, np.nan))],
                   "ylabel": "m / year (reliable windows)", "hlines": [(0.0, GREY, "")]}]
        if "dob_now" in se:
            panels.append({"lines": [("DoB now", GREEN, se["dob_now"])], "ylabel": "DoB (m)", "bands": bands,
                           "hlines": [(r["params"]["target"], RED, f"target {r['params']['target']:g} m"),
                                      (0.0, GREY, "seabed")]})
        self.chart.setMinimumHeight(150 * len(panels) + 30)
        self.chart.set_data(kp, panels)

    def _rows(self):
        r = self.res
        key = self.cmb_table.currentData()
        if r is None or key is None:
            return []
        return r[key]

    def _fill_table(self, *_):
        self.table.clear()
        rows = self._rows()
        hide = {"i0", "i1", "x_from", "y_from", "x_to", "y_to", "a", "b", "ch"}
        cols = []
        for row in rows:
            for c in row:
                if c not in hide and c not in cols:
                    cols.append(c)
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels([c.replace("_", " ") for c in cols])
        for i, row in enumerate(rows):
            for j, c in enumerate(cols):
                v = row.get(c, "")
                if isinstance(v, (bool, np.bool_)):
                    txt = "yes" if v else "no"
                elif isinstance(v, float):
                    txt = _fmt(v, 3 if c.startswith("kp") else 2)
                else:
                    txt = str(v)
                it = QTableWidgetItem(txt)
                if isinstance(v, float):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()

    def _row_clicked(self, i, _j):
        rows = self._rows()
        if 0 <= i < len(rows):
            self.cb.seabed_goto(rows[i])
