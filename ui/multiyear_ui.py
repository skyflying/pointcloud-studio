"""Lines & Structures → Multi-year Cable Assessment: exposure, free spans and re-burial need for many years
(surveys and modelled seabed predictions) in one run (core/multiyear.py)."""
from __future__ import annotations

import os
import re
import sys

import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QListWidgetItem, QRadioButton,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui.cable_dialog import GREEN, GREY, ORANGE, RED, KPChart, _dsp, _fmt, _muted, _section
from ui.check_helpers import CheckList, ClickToggleMixin, check_bar
from ui.theme import C, dark_titlebar

YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
GRID_FILTER = ("Seabed per year (*.tif *.tiff *.asc *.grd *.xyz *.txt *.csv *.las *.laz);;"
               "DEM grids (*.tif *.tiff *.asc *.grd *.xyz);;Point clouds (*.las *.laz *.xyz *.txt *.csv);;All files (*)")


def trend_panels(res):
    """(years, panels) of the trend chart (also used for the exported figures)."""
    s, p = res["summary"], res["params"]
    tab = res["table"]
    yrs = np.array([r["year"] for r in tab], float)
    simm = np.array([r["data"] == "simulated" for r in tab])

    note = "  ·  solid = survey, dashed = simulated" if simm.any() and (~simm).any() else (
        "  ·  dashed = simulated" if simm.any() else "")

    def split(key, col, lab):
        v = np.array([r.get(key, np.nan) for r in tab], float)
        out = [(lab, col, np.where(~simm, v, np.nan))] if (~simm).any() else []
        if simm.any():
            # join the dashed simulated line to the last survey point before it
            vs = np.where(simm, v, np.nan)
            last_s = np.flatnonzero(~simm)
            if len(last_s) and last_s[-1] + 1 < len(v) and simm[last_s[-1] + 1]:
                vs[last_s[-1]] = v[last_s[-1]]
            out.append(("" if (~simm).any() else lab, col, vs, "dash"))     # one legend entry per metric
        return out
    panels = [{"lines": split("reburial_m", RED, "re-burial m") + split("exposed_m", ORANGE, "exposed m")
               + split("span_length_m", "#b8b8ff", "free spans m")
               + (split("cumulative_reburial_m", "#ffd166", "cumulative re-burial m") if s["mode"] == "cumulative"
                  else []), "ylabel": "length (m)" + note},
              {"lines": split("min_cover", GREEN, "min cover") + split("max_lowering", RED, "max lowering"),
               "ylabel": "m" + note, "hlines": [(p["required"], RED, f"required {p['required']:g} m"), (0.0, GREY, "")]}]
    if p["rate"]:
        panels.append({"lines": split("trenching_h", "#4cc9f0", "trenching h")
                       + (split("cumulative_hours", "#ffd166", "cumulative h") if s["mode"] == "cumulative" else []),
                       "ylabel": "hours" + note})
    return yrs, panels


def year_colors(years):
    """Years → colours of the 'jet' scale (early = blue, late = red), as in the original burial-check plots.
    The darkest blue end is skipped so the first year stays visible on the dark background."""
    y0, y1 = min(years), max(years)
    out = {}
    for y in years:
        t = 0.0 if y1 == y0 else (y - y0) / (y1 - y0)
        t = 0.10 + 0.82 * t                       # skip the darkest ends (dark background)

        def f(x, t=t):
            return min(max(1.5 - abs(4.0 * t - x), 0.0), 1.0)
        out[y] = "#{:02x}{:02x}{:02x}".format(int(f(3.0) * 255), int(f(2.0) * 255), int(f(1.0) * 255))
    return out


PURPLE, BLUE_BAR = "#b46bd6", "#4c9be8"


def figure_set(res, depth):
    """The figures written by "Export figures…" for one cable: [(file stem, title, x, panels, xlabel, xdec)].
    All in the program's chart style (legends above the frames):
    00 ISBL (or first seabed) / DSBL / cable level · 01 seabed of every year, lower envelope, DSBL ·
    02 seabed lowering (inverted) with the required burial, and the minimum cover ·
    03 cable level after maintenance, each year · 04 re-burial events and first year along the cable ·
    05 per-year trends."""
    p = res["params"]
    kp = res["st"]["kp"]
    years = sorted(res["series"])
    col = year_colors(years)
    ev = (lambda a: -np.asarray(a, float)) if depth else (lambda a: np.asarray(a, float))
    zl = "Depth (m)" if depth else "Elevation (m)"
    sb = np.vstack([res["series"][y]["seabed"] for y in years])
    has_isbl = res.get("isbl") is not None
    base = np.asarray(res["isbl"], float) if has_isbl else sb[0]
    base_lab = "ISBL" if has_isbl else f"seabed {years[0]}"
    dsbl = np.asarray(res["dsbl"], float) if res.get("dsbl") is not None else None
    with np.errstate(all="ignore"):
        env = np.nanmin(np.vstack(([base] if has_isbl else []) + [sb]), axis=0)
        low = base - env
        covers = np.vstack([res["series"][y]["cover"] for y in years])
        mincov = np.nanmin(covers, axis=0)
    figs = []
    # 00
    l0 = [(base_lab + ("" if has_isbl else " (first year)"), GREY, ev(base))]
    if res.get("isbl_shaved") is not None:
        l0.append(("ISBL (peak shaving)", GREY, ev(res["isbl_shaved"]), "dash"))
    if dsbl is not None:
        l0.append(("DSBL", RED, ev(dsbl)))
    l0.append(("buried cable level (initial)", PURPLE, ev(res["cable_top"])))
    figs.append(("00_seabed_DSBL_cable_level", f"{'ISBL' if has_isbl else 'Seabed'} / DSBL / buried cable level", kp,
                 [{"lines": l0, "ylabel": zl, "invert": depth}], None, 3))
    # 01
    l1 = [(str(y), col[y], ev(res["series"][y]["seabed"])) for y in years]
    if dsbl is not None:
        l1.append(("DSBL", RED, ev(dsbl)))
    l1 += [(f"lower envelope (min {'ISBL & ' if has_isbl else ''}{years[0]}–{years[-1]})", "#ffffff", ev(env)),
           ("cable level (initial)", PURPLE, ev(res["cable_top"]), "dash")]
    figs.append(("01_seabed_level", f"Seabed level of every year ({years[0]}–{years[-1]}) and lower envelope", kp,
                 [{"lines": l1, "ylabel": zl, "invert": depth}], None, 3))
    # 02
    req = res.get("req_cover")
    l2 = [("required burial depth", PURPLE, np.asarray(req, float) if req is not None
           else np.full(len(kp), float(p["required"])))]
    l2.append((f"{base_lab} − lower envelope", GREY, low))
    if dsbl is not None:
        l2.append((f"max lowering ({base_lab} − DSBL)", "#ffffff", base - dsbl))
    hl = sorted({float(np.nanmin(req)), float(np.nanmax(req))} if req is not None
                else {float(p["required"]), float(p.get("reburial_to") or p["required"])})
    figs.append(("02_seabed_lowering", "Seabed lowering (downwards) and minimum cover", kp,
                 [{"lines": l2, "ylabel": "lowering (m)", "invert": True,
                   "fills": [(f"0 to ({base_lab} − lower envelope)", "#ff9db0", 0.0,
                              np.where(np.isfinite(low) & (low > 0), low, np.nan))],
                   "hlines": [(v, PURPLE, f"{v:g} m") for v in hl]},
                  {"lines": [("minimum cover over all years", GREEN, mincov)], "ylabel": "cover (m)",
                   "hlines": [(p["required"], RED, f"trigger {p['required']:g} m"),
                              (p["target"], ORANGE, f"target {p['target']:g} m"), (0.0, GREY, "exposed below")]}],
                 None, 3))
    # 03
    l3 = [(str(y), col[y], ev(res["series"][y]["top_after"])) for y in years]
    l3 += [("lower envelope", "#ffffff", ev(env), "dash")]
    figs.append(("03_cable_level_after_maintenance", "Cable level after maintenance, each year"
                 + ("" if p["mode"] == "cumulative" else " (independent mode: unchanged)"), kp,
                 [{"lines": l3, "ylabel": zl, "invert": depth}], None, 3))
    # 04
    pts = res["points"]
    cnt_lab = "re-burial events" if p["mode"] == "cumulative" else "years needing re-burial"
    figs.append(("04_reburial_events", "Re-burial events and first re-burial year along the cable", kp,
                 [{"lines": [], "bars": [(cnt_lab, BLUE_BAR, pts["count"].astype(float))], "ylabel": "count",
                   "ydec": 0},
                  {"lines": [("first year", "#4cc9f0", pts["first_year"])], "ylabel": "year", "ydec": 0}], None, 3))
    # 05
    yrs, tp = trend_panels(res)
    figs.append(("05_per_year_trends", "Per-year trends", yrs, tp, "Year", 0))
    return figs


def render_figure(path, title, x, panels, xlabel=None, xdec=3, width=1800):
    """Draw one figure with the chart widget (off screen) and save it as PNG."""
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from ui.chart_theme import colors
    T = colors()
    from PySide6.QtGui import QFont, QFontMetrics
    ch = KPChart()
    fnt = QFont(ch.font())
    fnt.setPointSizeF(max(7.5, fnt.pointSizeF() - 1.5))
    fm = QFontMetrics(fnt)
    h = 50
    for pan in panels:                            # each panel: plot + the rows its legend needs above the frame
        labels = [ln[0] for ln in pan["lines"] if ln[0]] + [f[0] for f in pan.get("fills", [])] + \
            [b[0] for b in pan.get("bars", [])]
        room = width - 80 - fm.horizontalAdvance(pan.get("ylabel", "")) - 16
        rows, x_ = 1, 0
        for lab in labels:
            w_ = fm.horizontalAdvance("■ " + lab) + 14
            if x_ > 0 and x_ + w_ > room:
                rows += 1
                x_ = 0
            x_ += w_
        h += 230 + (rows - 1) * fm.height()
    ch.setMinimumHeight(0)
    ch.setFixedSize(width, h)                     # the widget's minimum height must not crop the figure
    ch.set_data(x, panels, xlabel=xlabel, xdec=xdec)
    img = ch.grab()
    out = QPixmap(width, h + 34)
    out.fill(QColor(T["surface"]))
    pnt = QPainter(out)
    pnt.setPen(QColor(T["text"]))
    f = pnt.font()
    f.setPointSizeF(f.pointSizeF() + 1)
    f.setBold(True)
    pnt.setFont(f)
    pnt.drawText(12, 23, title)
    pnt.drawPixmap(0, 34, img)
    pnt.end()
    out.save(path)
    ch.deleteLater()


class YearTable(ClickToggleMixin, QTableWidget):
    """Use · dataset · year · survey / simulated (edits are written to the datasets)."""

    def __init__(self, on_change):
        super().__init__(0, 4)
        self.on_change = on_change
        self.setHorizontalHeaderLabels(["Use", "Seabed (surface or points)", "Year", "Data"])
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.setMinimumHeight(180)
        self._ids = []

    def fill(self, items):
        """items: [(id, name, year or None, simulated)] sorted by year."""
        keep = {}
        for r in range(self.rowCount()):
            if self.item(r, 0) is not None:
                keep[self._ids[r]] = self.item(r, 0).checkState() == Qt.Checked
        self.blockSignals(True)
        self.setRowCount(len(items))
        self._ids = [i for i, *_ in items]
        for r, (i, n, y, sim) in enumerate(items):
            c = QTableWidgetItem()
            c.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            c.setCheckState(Qt.Checked if keep.get(i, y is not None) else Qt.Unchecked)
            self.setItem(r, 0, c)
            it = QTableWidgetItem(n)
            it.setFlags(Qt.ItemIsEnabled)
            self.setItem(r, 1, it)
            sp = QSpinBox(minimum=1900, maximum=2200, value=int(y) if y else 2025)
            sp.setSpecialValueText("—")
            if not y:
                sp.setMinimum(1899)
                sp.setValue(1899)
            sp.valueChanged.connect(lambda v, ds=i, row=r: self._year_set(ds, row, v))
            self.setCellWidget(r, 2, sp)
            cb = QComboBox()
            cb.addItems(["Survey", "Simulated"])
            cb.setCurrentIndex(1 if sim else 0)
            cb.currentIndexChanged.connect(lambda k, ds=i: self.on_change(ds, simulated=bool(k)))
            self.setCellWidget(r, 3, cb)
        self.resizeColumnToContents(0)
        self.blockSignals(False)

    def _year_set(self, ds, row, v):
        self.on_change(ds, year=v if v >= 1900 else None)
        it = self.item(row, 0)
        if v >= 1900 and it is not None and it.checkState() != Qt.Checked:
            it.setCheckState(Qt.Checked)            # giving a year means: use it

    def chosen(self):
        out = []
        for r in range(self.rowCount()):
            if self.item(r, 0).checkState() == Qt.Checked:
                y = self.cellWidget(r, 2).value()
                out.append((self._ids[r], y if y >= 1900 else None, self.cellWidget(r, 3).currentIndex() == 1))
        return out


class MultiYearDialog(QDialog):
    def __init__(self, cb, parent=None):
        super().__init__(parent)
        self.cb = cb
        self.res = None
        self.batch = None
        self.depth = False
        self.setWindowTitle("Multi-year cable assessment")
        self.setModal(False)
        self.resize(760, 1000)
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
        t = QLabel("Multi-year cable assessment")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("One cable profile against the seabed of many years — surveys and modelled predictions. "
                           "Per year: exposure, free spans and the re-burial needed to restore the required cover "
                           "(jetting trencher). The cable is assumed not to sag, so spans are upper estimates."))
        f = QFormLayout()
        g = QButtonGroup(self)
        self.rb_single = QRadioButton("One cable")
        self.rb_batch = QRadioButton("Batch — several cables / strings")
        self.rb_single.setChecked(True)
        hb = QHBoxLayout()
        for rb in (self.rb_single, self.rb_batch):
            g.addButton(rb)
            hb.addWidget(rb)
        hb.addStretch()
        f.addRow("Run for", hb)
        self.cmb_cable = QComboBox()
        f.addRow("Cable profile", self.cmb_cable)
        self.batch_box = QWidget()
        bv = QVBoxLayout(self.batch_box)
        bv.setContentsMargins(0, 0, 0, 0)
        self.lst_cables = CheckList()
        self.lst_cables.setMinimumHeight(130)
        bv.addWidget(check_bar(self.lst_cables, "Cables:"))
        bv.addWidget(self.lst_cables)
        sr = QHBoxLayout()
        self.cmb_string = QComboBox()
        self.cmb_string.setToolTip("Attribute (from a GIS import) shared by the cables of one string; they are joined "
                                   "end to end (nearest endpoints) into one continuous cable")
        self.cmb_what = QComboBox()
        for lab, k in (("cables", "cables"), ("cables and joined strings", "both"), ("joined strings only", "strings")):
            self.cmb_what.addItem(lab, k)
        for lab_, w_ in (("String field", self.cmb_string), ("Assess", self.cmb_what)):
            sr.addWidget(_muted(lab_))
            sr.addWidget(w_)
            sr.addSpacing(10)
        sr.addStretch()
        bv.addLayout(sr)
        f.addRow(self.batch_box)
        self.batch_box.setVisible(False)
        self.lim = QCheckBox("Limit to KP")
        self.k0 = _dsp(0.0, -1e5, 1e5, 0.1, 3, " km")
        self.k1 = _dsp(1.0, -1e5, 1e5, 0.1, 3, " km")
        h = QHBoxLayout()
        for x in (self.lim, self.k0, QLabel("–"), self.k1):
            h.addWidget(x)
        f.addRow(h)
        v.addLayout(f)
        self.rb_batch.toggled.connect(lambda b: (self.batch_box.setVisible(b), self.cmb_cable.setEnabled(not b),
                                                 self.lim.setEnabled(not b)))
        row = QHBoxLayout()
        row.addWidget(_section("Years"))
        row.addStretch()
        b = QPushButton("Import yearly files…")
        b.setToolTip("Several seabed files at once; the year is taken from each file name (e.g. seabed_2031.tif)")
        b.clicked.connect(cb.multiyear_import)
        row.addWidget(b)
        b2 = QPushButton("Link files (read when run)…")
        b2.setToolTip("Yearly seabed rasters (GeoTIFF / ASC …) that are NOT loaded into the project: each is read "
                      "only along the cable when the assessment runs — for many years of model results "
                      "without filling the memory")
        b2.clicked.connect(cb.multiyear_link_files)
        row.addWidget(b2)
        v.addLayout(row)
        self.years = YearTable(cb.multiyear_set)
        yr0 = QHBoxLayout()
        yr0.addWidget(check_bar(self.years, "Use:"), 1)
        self.y0 = QSpinBox(minimum=1899, maximum=2200, value=1899)
        self.y0.setSpecialValueText("first")
        self.y1 = QSpinBox(minimum=1899, maximum=2200, value=1899)
        self.y1.setSpecialValueText("last")
        self.y0.setToolTip("Only ticked years in this range are assessed")
        for lab_, w_ in (("Years from", self.y0), ("to", self.y1)):
            yr0.addWidget(_muted(lab_))
            yr0.addWidget(w_)
        v.addLayout(yr0)
        v.addWidget(self.years)
        dr = QHBoxLayout()
        self.cmb_dsbl = QComboBox()
        self.cmb_dsbl.setToolTip("Optional: the deepest predicted seabed (e.g. a DSBL / LSBL surface). Drawn in the "
                                 "report figures (DSBL line, maximum lowering); it does not change the assessment.")
        self.cmb_isbl = QComboBox()
        self.cmb_isbl.setToolTip("Optional: the installation seabed (ISBL). Used in the report figures and — if chosen "
                                 "below — to derive the initial cable level (ISBL − required burial)")
        dr.addWidget(_muted("Installation seabed (ISBL, optional)"))
        dr.addWidget(self.cmb_isbl, 1)
        dr.addWidget(_muted("Deepest seabed (DSBL, optional)"))
        dr.addWidget(self.cmb_dsbl, 1)
        bf = QPushButton("…")
        bf.setFixedWidth(30)
        bf.setToolTip("Link an ISBL / DSBL raster file (read when run)")
        bf.clicked.connect(lambda: cb.multiyear_link_files(single=True))
        dr.addWidget(bf)
        v.addLayout(dr)
        cl = QFormLayout()
        self.cmb_level = QComboBox()
        self.cmb_level.addItem("Cable-top Z of the route (as laid)", "route")
        self.cmb_level.addItem("ISBL − required burial (sand-wave rule, peak shaving)", "isbl")
        self.cmb_level.setToolTip("Initial cable level. From the ISBL: peak shaving (cut-only, slope limit), then the "
                                  "required burial is subtracted — larger in sand-wave areas (steep ISBL, or ISBL far "
                                  "above the DSBL). The route then needs no cable-top Z.")
        cl.addRow("Initial cable level", self.cmb_level)
        self.sw_box = QWidget()
        swv = QVBoxLayout(self.sw_box)
        swv.setContentsMargins(0, 0, 0, 0)
        sh = QHBoxLayout()
        sh2 = QHBoxLayout()
        swv.addLayout(sh)
        swv.addLayout(sh2)
        self.sw_req = _dsp(1.5, 0.0, 20.0, 0.1, 2)
        self.ot_req = _dsp(0.5, 0.0, 20.0, 0.1, 2)
        self.sw_slope = _dsp(2.5, 0.0, 45.0, 0.5, 1)
        self.sw_dd = _dsp(7.5, 0.0, 100.0, 0.5, 1)
        self.peak = _dsp(15.0, 0.0, 89.0, 1.0, 1)
        self.peak.setSpecialValueText("off")
        for k_, (lab_, w_) in enumerate((("sand wave", self.sw_req), ("other", self.ot_req), ("slope >", self.sw_slope),
                                         ("ISBL−DSBL >", self.sw_dd), ("peak shaving ≤", self.peak))):
            (sh if k_ < 2 else sh2).addWidget(_muted(lab_))
            (sh if k_ < 2 else sh2).addWidget(w_)
        sh.addStretch(1)
        sh2.addStretch(1)
        cl.addRow("Required burial", self.sw_box)
        v.addLayout(cl)
        self.cmb_level.currentIndexChanged.connect(lambda _i: self.sw_box.setEnabled(self.cmb_level.currentData() == "isbl"))
        self.sw_box.setEnabled(False)
        v.addWidget(_section("Criteria"))
        f2 = QFormLayout()
        self.diam = _dsp(0.2, 0.01, 5.0, 0.01, 3)
        self.gap = _dsp(0.1, 0.0, 10.0, 0.05, 2)
        self.slen = _dsp(10.0, 0.0, 1000.0, 1.0, 1)
        self.target = _dsp(1.5, 0.0, 20.0, 0.1, 2)
        f2.addRow("Cable diameter", self.diam)
        f2.addRow("Free span: minimum gap", self.gap)
        f2.addRow("Free span: reporting length", self.slen)
        f2.addRow("Target depth of burial", self.target)
        v.addLayout(f2)
        v.addWidget(_section("Re-burial (jetting trencher)"))
        f3 = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("Cumulative — re-bury when needed, later years start from the lowered cable", "cumulative")
        self.mode.addItem("Independent — every year against the original cable level", "independent")
        self.mode.setToolTip("Cumulative: in a year that needs it the cable is re-buried, and the following years use "
                             "the new cable level (repeat campaigns, totals).\nIndependent: what each year would need "
                             "if nothing had been done before.")
        f3.addRow("Assessment", self.mode)
        self.req = _dsp(1.0, 0.0, 20.0, 0.1, 2)
        self.req.setToolTip("Re-burial is triggered where the cover is less than this")
        self.to = _dsp(1.5, 0.0, 20.0, 0.1, 2)
        self.to.setToolTip("Cumulative: the trencher lowers the cable to this cover (≥ the trigger)")
        self.maxc = _dsp(0.0, 0.0, 20.0, 0.1, 2)
        self.maxc.setSpecialValueText("— (no limit)")
        self.maxc.setToolTip("Deepest cover the trencher can achieve; stretches needing more are flagged")
        self.minl = _dsp(0.0, 0.0, 1000.0, 1.0, 1)
        self.minl.setToolTip("Shorter stretches are not re-buried (not worth a trencher run)")
        self.merge = _dsp(20.0, 0.0, 1000.0, 5.0, 0)
        self.merge.setToolTip("Stretches closer than this are trenched in one run")
        self.rate = _dsp(0.0, 0.0, 10000.0, 10.0, 0, " m/h")
        self.rate.setSpecialValueText("— (no time estimate)")
        self.step = _dsp(1.0, 0.1, 50.0, 0.5, 2)
        self.trig = QComboBox()
        self.trig.addItem("Cover less than the trigger below", "cover")
        self.trig.addItem("Exposed (cable above the seabed)", "exposed")
        self.trig.setToolTip("What starts a re-burial: low cover, or exposure only (as the original script)")
        self.minrun = _dsp(7.0, 0.0, 1000.0, 1.0, 1)
        self.minrun.setSpecialValueText("— (any length)")
        self.minrun.setToolTip("A triggering stretch must be at least this long, continuously; shorter ones are "
                               "not re-buried")
        self.settle = QCheckBox("Short exposures: the cable settles onto the seabed (cumulative)")
        self.settle.setChecked(True)
        self.settle.setToolTip("Where the cable is exposed over less than the minimum continuous length (and not "
                               "re-buried), its level becomes the seabed level for the following years")
        self.act = QComboBox()
        self.act.addItem("Re-bury to the cover below", "to_cover")
        self.act.addItem("Lower the cable by", "lower_by")
        self.lowby = _dsp(1.5, 0.0, 20.0, 0.1, 2)
        ar = QHBoxLayout()
        ar.addWidget(self.act, 1)
        ar.addWidget(_muted("by"))
        ar.addWidget(self.lowby)
        self.act.currentIndexChanged.connect(lambda _i: self.lowby.setEnabled(self.act.currentData() == "lower_by"))
        self.trig.currentIndexChanged.connect(lambda _i: self.req.setEnabled(self.trig.currentData() == "cover"))
        self.lowby.setEnabled(False)
        self.cost = _dsp(0.0, 0.0, 1e9, 100.0, 2)
        self.cost.setSpecialValueText("— (no cost)")
        self.disc = _dsp(0.0, 0.0, 50.0, 0.5, 2)
        self.disc.setSuffix(" %")
        self.basey = QSpinBox(minimum=1899, maximum=2200, value=1899)
        self.basey.setSpecialValueText("first year")
        cr = QHBoxLayout()
        for lab_, w_ in (("per m", self.cost), ("discount", self.disc), ("to", self.basey)):
            cr.addWidget(_muted(lab_))
            cr.addWidget(w_)
        f3.addRow("Re-burial trigger", self.trig)
        f3.addRow("Re-bury where cover is less than", self.req)
        f3.addRow("Only continuous stretches of at least", self.minrun)
        f3.addRow(self.settle)
        f3.addRow("Re-burial action", ar)
        f3.addRow("Re-bury to cover", self.to)
        f3.addRow("Re-burial cost", cr)
        f3.addRow("Trencher max. burial (cover)", self.maxc)
        f3.addRow("After joining, ignore stretches shorter than", self.minl)
        f3.addRow("Join stretches closer than", self.merge)
        f3.addRow("Trencher progress", self.rate)
        f3.addRow("Step along the cable", self.step)
        v.addLayout(f3)
        row = QHBoxLayout()
        self.lbl_msg = QLabel(" ")
        self.lbl_msg.setWordWrap(True)
        row.addWidget(self.lbl_msg, 1)
        b = QPushButton("Run assessment")
        b.setProperty("primary", True)
        b.clicked.connect(self._run)
        self.btn_run = b
        v.addWidget(_section("Result"))
        self.lbl_sum = QLabel("Run the assessment to see the result.")
        self.lbl_sum.setWordWrap(True)
        self.lbl_sum.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_sum)
        self.trend = KPChart()
        self.trend.setVisible(False)
        v.addWidget(self.trend)
        yr = QHBoxLayout()
        yr.addWidget(QLabel("Year along the cable / in 3D"))
        self.cmb_year = QComboBox()
        self.cmb_year.currentIndexChanged.connect(self._year_view)
        yr.addWidget(self.cmb_year, 1)
        v.addLayout(yr)
        self.prof = KPChart()
        self.prof.setVisible(False)
        v.addWidget(self.prof)
        self.batch = None
        self.cmb_table = QComboBox()
        self._table_options()
        self.cmb_table.currentIndexChanged.connect(self._fill_table)
        v.addWidget(self.cmb_table)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(240)
        self.table.cellDoubleClicked.connect(self._row_clicked)
        v.addWidget(self.table)
        from ui.chart_theme import selector
        crow = QHBoxLayout()
        crow.addStretch()
        crow.addWidget(selector(self))
        v.addLayout(crow)
        self.btns = []
        for label, fn in (("Export Excel…", cb.multiyear_export_excel), ("Export figures…", cb.multiyear_export_figures),
                          ("Export CSV…", cb.multiyear_export_csv), ("Export GIS…", cb.multiyear_export_gis),
                          ("Save charts…", cb.multiyear_save_charts), ("Clear 3D", cb.cable_clear_overlay)):
            bb = QPushButton(label)
            bb.clicked.connect(fn)
            bb.setEnabled(False)
            self.btns.append(bb)
        # run + export buttons in a footer that is always visible; the dialog fits its content and the screen
        from ui.dialog_fit import fit_dialog, make_footer
        make_footer(outer, [(self.lbl_msg, 1), (self.btn_run, 0)], self.btns)
        fit_dialog(self, body, scroll, (780, 1000))

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _table_options(self, select=None):
        cur = select or self.cmb_table.currentData()
        self.cmb_table.blockSignals(True)
        self.cmb_table.clear()
        if self.batch is not None:
            self.cmb_table.addItem(f"Batch summary ({len(self.batch['items'])} item(s)) — double-click a row to open it",
                                   "batch")
        if self.res is not None:
            name = self.res["route"]
            for lab, k in (("Per year", "table"), ("Re-burial interventions (cumulative)", "interventions"),
                           ("Re-burial stretches (first year needed)", "first"), ("All segments", "segments")):
                self.cmb_table.addItem(f"{name}: {lab}" if self.batch is not None else lab, k)
        j = self.cmb_table.findData(cur)
        self.cmb_table.setCurrentIndex(j if j >= 0 else 0)
        self.cmb_table.blockSignals(False)

    def show_batch(self, batch, depth):
        self.batch = batch
        self.res = None
        self.depth = depth
        items = batch["items"]
        rows_ = [r["row"] for r in items]
        need = [r for r in rows_ if r.get("first_reburial")]
        first = min((r["first_reburial"] for r in need), default=None)
        txt = [("Assessed", f"{len(items)} item(s) — "
                + ", ".join(f"{sum(1 for r in rows_ if r['type'] == t)} {t}(s)" for t in ("cable", "string")
                            if any(r["type"] == t for r in rows_))),
               ("Need re-burial", f"<span style='color:{RED}'>{len(need)}</span> item(s)"
                + (f", the first in {first}" if first else ""))]
        if batch["params"].get("mode") == "cumulative":
            tot = sum(r.get("total_reburial_m", 0) or 0 for r in rows_ if r["type"] == "cable")
            hrs = sum(r.get("total_hours", 0) or 0 for r in rows_ if r["type"] == "cable")
            txt.append(("Total re-burial (cables)", f"{tot:,.0f} m" + (f" · ≈ {hrs:,.1f} h" if hrs else "")))
        if batch["errors"]:
            txt.append(("Not assessed", f"<span style='color:{RED}'>{len(batch['errors'])}</span>: "
                        + "; ".join(f"{a} ({b[:60]})" for a, b in batch["errors"][:4])))
        self.lbl_sum.setText("<b>Batch</b><br>" + "<br>".join(
            f"<span style='color:{C['muted']}'>{a}:</span> {b}" for a, b in txt))
        self.trend.setVisible(False)
        self.prof.setVisible(False)
        self.cmb_year.clear()
        self._table_options("batch")
        self._fill_table()
        for b in self.btns:
            b.setEnabled(True)

    def set_context(self, cables, seabeds, depth, fields=(), extra=()):
        """cables: [(id, name, kp_range)], seabeds: [(id, name, year, simulated)], fields: route attributes"""
        self.depth = depth
        un = {self.lst_cables.item(k).data(Qt.UserRole) for k in range(self.lst_cables.count())
              if self.lst_cables.item(k).checkState() != Qt.Checked}
        self.lst_cables.blockSignals(True)
        self.lst_cables.clear()
        for i, n, _k in cables:
            it = QListWidgetItem(n)
            it.setData(Qt.UserRole, i)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if i in un else Qt.Checked)
            self.lst_cables.addItem(it)
        self.lst_cables.blockSignals(False)
        if self.lst_cables.count():
            self.lst_cables.itemChanged.emit(self.lst_cables.item(0))       # refresh the counter
        cur = self.cmb_string.currentData()
        self.cmb_string.clear()
        self.cmb_string.addItem("(none — cables only)", None)
        for fld in fields:
            self.cmb_string.addItem(str(fld), fld)
        j = self.cmb_string.findData(cur) if cur is not None else -1
        if j < 0:                                    # nothing chosen yet: suggest a field named like "…string…"
            j = next((k for k in range(1, self.cmb_string.count())
                      if "string" in str(self.cmb_string.itemData(k)).lower()), 0)
        self.cmb_string.setCurrentIndex(j)
        self._kp = {i: k for i, _n, k in cables}
        cur = self.cmb_cable.currentData()
        self.cmb_cable.blockSignals(True)
        self.cmb_cable.clear()
        for i, n, _k in cables:
            self.cmb_cable.addItem(n, i)
        j = self.cmb_cable.findData(cur)
        self.cmb_cable.setCurrentIndex(j if j >= 0 else 0)
        self.cmb_cable.blockSignals(False)
        k = self._kp.get(self.cmb_cable.currentData())
        if k and not self.lim.isChecked():
            self.k0.setValue(min(k))
            self.k1.setValue(max(k))
        self.years.fill(sorted(seabeds, key=lambda s: (s[2] is None, s[2] or 0, s[1])))
        for cmb, hints in ((self.cmb_dsbl, ("DSBL", "LSBL")), (self.cmb_isbl, ("ISBL",))):
            cur = cmb.currentData()
            cmb.clear()
            cmb.addItem("(none)", None)
            for i, n, _y, _sim in list(seabeds) + list(extra):
                cmb.addItem(n, i)
            j = cmb.findData(cur) if cur is not None else -1
            if j < 0:                                # suggest a dataset named like DSBL / LSBL / ISBL
                j = next((k for k in range(1, cmb.count()) if any(t in cmb.itemText(k).upper() for t in hints)), 0)
            cmb.setCurrentIndex(j)

    def show_error(self, m):
        self.lbl_msg.setText(f"<span style='color:{C['danger']}'>●  {m}</span>")

    def _run(self):
        self.lbl_msg.setText(" ")
        batch = self.rb_batch.isChecked()
        chosen_cables = [self.lst_cables.item(k).data(Qt.UserRole) for k in range(self.lst_cables.count())
                         if self.lst_cables.item(k).checkState() == Qt.Checked]
        if batch and not chosen_cables:
            return self.show_error("Tick at least one cable")
        if batch and self.cmb_what.currentData() != "cables" and self.cmb_string.currentData() is None:
            return self.show_error("Choose the string field to join cables into strings")
        if not batch and self.cmb_cable.currentData() is None:
            return self.show_error("Import the cable route first")
        if not batch and self.cmb_level.currentData() == "route" and "(no cable Z)" in self.cmb_cable.currentText():
            return self.show_error("This route has no cable-top Z — choose 'ISBL − required burial' as the cable level")
        ys = self.years.chosen()
        a, b = self.y0.value(), self.y1.value()
        ys = [t for t in ys if t[1] is None or ((a < 1900 or t[1] >= a) and (b < 1900 or t[1] <= b))]
        if not ys:
            return self.show_error("Tick at least one year")
        missing = [i for i, y, _s in ys if y is None]
        if missing:
            return self.show_error("Set the year of every ticked dataset")
        rng = (self.k0.value(), self.k1.value()) if (self.lim.isChecked() and not batch) else None
        k = self._kp.get(self.cmb_cable.currentData())
        if rng and k and (max(rng) < min(k) or min(rng) > max(k) or abs(rng[1] - rng[0]) < 1e-9):
            return self.show_error(f"KP range outside the cable (KP {min(k):.3f} – {max(k):.3f})")
        for cmb, lab in ((self.cmb_dsbl, "DSBL"), (self.cmb_isbl, "ISBL")):
            if cmb.currentData() is not None and any(i == cmb.currentData() for i, _y, _s in ys):
                return self.show_error(f"The {lab} dataset is also ticked as a year — untick it in the year list")
        if self.cmb_level.currentData() == "isbl" and self.cmb_isbl.currentData() is None:
            return self.show_error("Choose the ISBL to derive the cable level from it")
        self.cb.multiyear_run({"cable": self.cmb_cable.currentData(), "years": ys, "kp_range": rng,
                               "dsbl": self.cmb_dsbl.currentData(), "isbl": self.cmb_isbl.currentData(),
                               "rules": dict(trigger=self.trig.currentData(), min_run=self.minrun.value(),
                                             settle_short=self.settle.isChecked(), action=self.act.currentData(),
                                             lower_by=self.lowby.value(), cost_per_m=self.cost.value(),
                                             discount_rate=self.disc.value() / 100.0,
                                             base_year=self.basey.value() if self.basey.value() >= 1900 else None,
                                             cable_level=self.cmb_level.currentData(),
                                             req_sandwave=self.sw_req.value(), req_other=self.ot_req.value(),
                                             sw_slope_deg=self.sw_slope.value(), sw_isbl_dsbl=self.sw_dd.value(),
                                             peak_deg=self.peak.value()),
                               "batch": {"cables": chosen_cables, "field": self.cmb_string.currentData(),
                                         "what": self.cmb_what.currentData()} if batch else None,
                               "diameter": self.diam.value(), "gap_min": self.gap.value(),
                               "len_min": self.slen.value(), "target": self.target.value(),
                               "required": self.req.value(), "merge_gap": self.merge.value(),
                               "rate": self.rate.value(), "step": self.step.value(), "mode": self.mode.currentData(),
                               "reburial_to": max(self.to.value(), self.req.value()), "max_cover": self.maxc.value(),
                               "min_len": self.minl.value()})

    # ---------------------------------------------------------------- results
    def show_result(self, res, depth):
        self.res = res
        self.depth = depth
        s, p = res["summary"], res["params"]
        tab = res["table"]
        sim = [r["year"] for r in tab if r["data"] == "simulated"]

        def yr(v):
            return str(v) if v else "not within the years assessed"
        rows = [("Years", f"{s['n_years']} ({min(s['years'])}–{max(s['years'])})"
                 + (f" · simulated: {', '.join(map(str, sim))}" if sim else "")),
                ("Re-burial first needed", f"<span style='color:{RED}'>{yr(s['first_reburial_year'])}</span> "
                                           f"(cover < {p['required']:g} m)"),
                ("First exposure", yr(s["first_exposed_year"])),
                (f"First free span (≥ {p['len_min']:g} m)", yr(s["first_span_year"])),
                ("Most re-burial", f"{s['worst_year']}: {s['worst_reburial_m']:,.0f} m"
                 + (f" · ≈ {s['worst_reburial_m'] / p['rate']:.1f} h trenching" if p["rate"] else "")),
                ("Ever below the required cover", f"{s['ever_reburial_m']:,.0f} m of {s['length']:,.0f} m")]
        if s["mode"] == "cumulative":
            rows.insert(1, ("Assessment", f"cumulative — re-buried to {p['reburial_to']:g} m cover when below "
                                          f"{p['required']:g} m; later years use the lowered cable"))
            rows.insert(2, ("Re-burial campaigns", f"<span style='color:{RED}'>{len(s['campaign_years'])}</span> "
                                                   f"({', '.join(map(str, s['campaign_years'])) or 'none'}) · "
                                                   f"{s['interventions']} stretch(es), {s['total_reburial_m']:,.0f} m"
                                                   + (f", ≈ {s['total_hours']:.1f} h" if s['total_hours'] else "")))
            if s["beyond_trencher"]:
                rows.insert(3, ("Beyond the trencher", f"<span style='color:{RED}'>{s['beyond_trencher']} stretch(es) "
                                                       f"need more than {p['max_cover']:g} m cover — other remedy "
                                                       "(rock / mattress)</span>"))
            rows = [r_ for r_ in rows if not r_[0].startswith("Most re-burial")]
        else:
            rows.insert(1, ("Assessment", "independent — every year against the original cable level"))
        self.lbl_sum.setText(f"<b>{res['route']}</b><br>" + "<br>".join(
            f"<span style='color:{C['muted']}'>{a}:</span> {b}" for a, b in rows))
        yrs, panels = trend_panels(res)
        self.trend.setVisible(True)
        self.trend.setMinimumHeight(170 * len(panels) + 30)
        self.trend.set_data(yrs, panels, xlabel="Year", xdec=0)
        self.cmb_year.blockSignals(True)
        self.cmb_year.clear()
        for r in tab:
            self.cmb_year.addItem(f"{r['year']}  ({r['data']}: {r['dataset']})", r["year"])
        self.cmb_year.setCurrentIndex(self.cmb_year.count() - 1)
        self.cmb_year.blockSignals(False)
        self._year_view()
        self._table_options("table")
        self._fill_table()
        for b in self.btns:
            b.setEnabled(True)

    def year(self):
        return self.cmb_year.currentData()

    def _year_view(self, *_):
        r = self.res
        if r is None or self.year() is None:
            return
        y = self.year()
        se = r["series"][y]
        p = r["params"]
        kp = r["st"]["kp"]
        ev = (lambda a: -np.asarray(a)) if self.depth else (lambda a: np.asarray(a))
        bands = []
        for sg in r["segments"]:
            if sg["year"] == y:
                bands.append((sg["kp_from"], sg["kp_to"], {"re-burial": RED, "exposed": ORANGE}.get(sg["type"],
                                                                                                     "#b8b8ff")))
        dash = "dash" if se["simulated"] else ""
        lines = [("seabed " + str(y), "#4cc9f0", ev(se["seabed"]), dash),
                 ("cable top" + (f" {y}" if p["mode"] == "cumulative" else ""), ORANGE, ev(se["top"])),
                 (f"seabed needed for {p['required']:g} m cover", RED, ev(se["top"] + p["required"]), "dash")]
        if p["mode"] == "cumulative" and not np.allclose(np.nan_to_num(se["top"]), np.nan_to_num(se["top_after"]),
                                                         rtol=0, atol=1e-6):
            lines.insert(2, (f"cable after re-burial {y}", GREEN, ev(se["top_after"])))
        panels = [{"lines": lines,
                   "ylabel": "Depth (m)" if self.depth else "Elevation (m)", "bands": bands,
                   "invert": self.depth},                      # depths: seabed drawn above the cable
                  {"lines": [("cover " + str(y), GREEN, se["cover"], dash)], "ylabel": "cover (m)", "bands": bands,
                   "hlines": [(p["required"], RED, f"required {p['required']:g}"), (p["target"], ORANGE,
                                                                                     f"target {p['target']:g}"),
                              (0.0, GREY, "exposed below")]}]
        self.prof.setVisible(True)
        self.prof.setMinimumHeight(360)
        self.prof.set_data(kp, panels)
        self.cb.multiyear_overlay(y)

    def _rows(self):
        k = self.cmb_table.currentData()
        if k == "batch":
            return [i["row"] for i in self.batch["items"]] if self.batch else []
        if self.res is None:
            return []
        return self.res.get(k, [])

    def _fill_table(self, *_):
        self.table.clear()
        rows = self._rows()
        cols = [c for c in dict.fromkeys(c for r in rows for c in r) if c not in ("i0", "i1")]
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels([c.replace("_", " ") for c in cols])
        for i, r in enumerate(rows):
            for j, c in enumerate(cols):
                v = r.get(c, "")
                if v is None:
                    txt = "—"
                elif isinstance(v, float):
                    txt = "—" if not np.isfinite(v) else (f"{v:.0f}" if c.startswith("first") else
                                                         _fmt(v, 3 if c.startswith("kp") else 2))
                else:
                    txt = str(v)
                it = QTableWidgetItem(txt)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if r.get("data") == "simulated":
                    it.setForeground(Qt.gray)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()

    def _row_clicked(self, i, _j):
        rows = self._rows()
        if self.cmb_table.currentData() == "batch":
            if 0 <= i < len(rows):
                self.cb.multiyear_open_item(rows[i]["item"])
            return
        if 0 <= i < len(rows):
            r = rows[i]
            if "year" in r and r["year"] != self.year():
                k = self.cmb_year.findData(r["year"])
                if k >= 0:
                    self.cmb_year.setCurrentIndex(k)
            if "kp_from" in r:
                self.cb.cable_goto(r["kp_from"], r["kp_to"])


class MultiYearMixin:
    def _init_multiyear_state(self):
        self.multiyear_dialog = None
        self._multiyear_result = None
        self._multiyear_batch = None
        self._my_items, self._my_years, self._my_params = {}, [], {}
        self._my_files = {}                  # linked rasters read only when run: path → {"year", "sim"}

    def open_multiyear_dialog(self):
        if self.multiyear_dialog is None:
            self.multiyear_dialog = MultiYearDialog(self, self)
        self._multiyear_context()
        self.multiyear_dialog.show()
        self.multiyear_dialog.raise_()
        self.multiyear_dialog.activateWindow()

    def _multiyear_context(self):
        if getattr(self, "multiyear_dialog", None) is None:
            return
        P = self.project
        # routes without a cable-top Z can be assessed when the cable level comes from the ISBL
        cables = [(d.id, d.name + ("" if d.obj.z is not None else "  (no cable Z)"), d.obj.kp_range())
                  for d in P.of_kind("route")]
        seabeds = []
        for d in P.datasets:
            if d.kind in ("grid", "tin", "points") and getattr(d.obj, "cvals", None) is None:
                y = int(d.survey_date[:4]) if d.survey_date and d.survey_date[:4].isdigit() else None
                seabeds.append((d.id, d.name + ("  (points)" if d.kind == "points" else ""), y, d.simulated))
        from core.multiyear_batch import attribute_fields
        fields = attribute_fields([d.obj for d in P.of_kind("route")])
        files = getattr(self, "_my_files", {})
        for path, f in files.items():
            if f.get("year") is not None:
                seabeds.append(("file:" + path, os.path.basename(path) + "  (linked)", f["year"], f["sim"]))
        extra = [("file:" + p_, os.path.basename(p_) + "  (linked)", None, False) for p_, f in files.items()
                 if f.get("year") is None]
        self.multiyear_dialog.set_context(cables, seabeds, self.depth_mode, fields, extra)

    def multiyear_set(self, ds_id, year=..., simulated=None):
        if isinstance(ds_id, str) and ds_id.startswith("file:"):
            f = self._my_files.get(ds_id[5:])
            if f is not None:
                if year is not ...:
                    f["year"] = year
                if simulated is not None:
                    f["sim"] = simulated
            return
        ds = self.project.get(ds_id)
        if ds is None:
            return
        if year is not ...:
            old = ds.survey_date
            ds.survey_date = None if year is None else (old if old and old[:4] == str(year) else f"{year}-01-01")
        if simulated is not None:
            ds.simulated = simulated
        self._set_dirty(True)
        self._refresh_data_card()

    def multiyear_link_files(self, single=False):
        """Link raster files without loading them: they are read along the cable only when the assessment runs."""
        flt = "Rasters (*.tif *.tiff *.asc *.grd *.TIF *.TIFF *.ASC *.GRD);;All files (*)"
        if single:
            p, _ = QFileDialog.getOpenFileName(self, "ISBL / DSBL raster", "", flt)
            paths = [p] if p else []
        else:
            paths, _ = QFileDialog.getOpenFileNames(self, "Seabed rasters, one per year (read when run)", "", flt)
        if not paths:
            return
        sim = None
        for p in paths:
            if single:
                self._my_files[p] = {"year": None, "sim": False}
                continue
            m = YEAR_RE.findall(os.path.basename(p))
            if m:
                y = int(m[-1])
            else:
                y, ok = QInputDialog.getInt(self, "Year", f"Year of {os.path.basename(p)}:", 2025, 1900, 2200)
                if not ok:
                    return
            if sim is None:
                ans = QMessageBox.question(self, "Surveys or model results?",
                                           f"{len(paths)} file(s). Are these modelled (simulated) seabeds?\n"
                                           "Yes = simulated, No = surveys.", QMessageBox.Yes | QMessageBox.No,
                                           QMessageBox.Yes)
                sim = ans == QMessageBox.Yes
            self._my_files[p] = {"year": y, "sim": sim}
        self._multiyear_context()
        self.statusBar().showMessage(f"{len(paths)} file(s) linked — read along the cable when the assessment runs",
                                     8000)

    def _my_source(self, sid):
        """Project dataset object, or a linked raster file (read when run)."""
        if isinstance(sid, str) and sid.startswith("file:"):
            from core.raster_source import RasterSource
            path = sid[5:]
            if not os.path.isfile(path):
                return None, os.path.basename(path)
            return RasterSource(path, self.project.crs), os.path.basename(path)
        d = self.project.get(sid)
        return (d.obj, d.name) if d is not None else (None, None)

    def multiyear_import(self):
        from ui.crs_dialog import CrsDialog
        paths, _ = QFileDialog.getOpenFileNames(self, "Seabed files, one per year", "", GRID_FILTER)
        if not paths:
            return
        years = {}
        for p in paths:
            m = YEAR_RE.findall(os.path.basename(p))
            if m:
                years[p] = int(m[-1])
            else:
                y, ok = QInputDialog.getInt(self, "Year", f"Year of {os.path.basename(p)}:", 2025, 1900, 2200)
                if not ok:
                    return
                years[p] = y
        ans = QMessageBox.question(self, "Surveys or model results?",
                                   f"{len(paths)} file(s), years {', '.join(str(years[p]) for p in paths)}.\n\n"
                                   "Are these modelled (simulated) seabeds?\nYes = simulated, No = surveys.",
                                   QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
        if ans == QMessageBox.Cancel:
            return
        sim = ans == QMessageBox.Yes
        ok, c = CrsDialog.ask(self, "Coordinate system of the files",
                              "Used for every file that does not state its own coordinate system.",
                              initial=self.project.crs, allow_unknown=self.project.crs is None)
        if not ok:
            return
        self._batch_crs = c
        self._batch_xyz = {"opts": None}
        self._year_tags = {p: (f"{years[p]}-01-01", sim) for p in paths}
        # always "add": loading into an empty project would start a new project and drop the queue
        self._load_queue = list(paths[1:])
        self.load_path(paths[0], add=True)

    def multiyear_run(self, sp):
        from core import multiyear
        if self._busy(self.multiyear_dialog):
            return
        P = self.project
        r = P.get(sp["cable"]) if sp.get("batch") is None else True
        ys = []
        for i, y, sim in sp["years"]:
            obj, name = self._my_source(i)
            if obj is None or r is None:
                self._multiyear_context()
                return self.multiyear_dialog.show_error(f"{name or 'A dataset'} is no longer available — check the "
                                                        "choices and run again")
            ys.append({"name": name, "year": y, "simulated": sim, "obj": obj})
        dup = sorted({y["year"] for y in ys if sum(1 for z in ys if z["year"] == y["year"]) > 1})
        if dup:
            return self.multiyear_dialog.show_error(f"More than one dataset for year(s) {', '.join(map(str, dup))}")
        params = dict(diameter=sp["diameter"], gap_min=sp["gap_min"], len_min=sp["len_min"], target=sp["target"],
                      required=sp["required"], merge_gap=sp["merge_gap"], rate=sp["rate"], step=sp["step"],
                      mode=sp["mode"], reburial_to=sp["reburial_to"], max_cover=sp["max_cover"],
                      min_len=sp["min_len"])
        params.update(sp.get("rules") or {})
        names = {}
        for key in ("dsbl", "isbl"):
            if sp.get(key) is not None:
                obj, name = self._my_source(sp[key])
                if obj is not None:
                    params[key] = obj
                    names[key] = name
        self._my_names = names
        self._my_years, self._my_params = ys, params
        if sp.get("batch"):
            from core import multiyear_batch as mb
            b = sp["batch"]
            routes = [P.get(i) for i in b["cables"] if P.get(i) is not None]
            items = [] if b["what"] == "strings" else [(d.name, "cable", d.obj) for d in routes]
            if b["what"] != "cables" and b["field"]:
                items += [(st.name, "string", st) for st in mb.stitch([d.obj for d in routes], b["field"])]
            # item ids must be unique (two cables with the same name would overwrite each other)
            seen, uniq = {}, []
            for iid, kind, rt in items:
                n = seen.get(iid, 0)
                seen[iid] = n + 1
                uniq.append((iid if n == 0 else f"{iid} ({n + 1})", kind, rt))
            items = uniq
            if not items:
                return self.multiyear_dialog.show_error("Nothing to assess — no cable has a value in the string field")
            self._my_items = {iid: (kind, rt) for iid, kind, rt in items}
            self._run_task(f"Multi-year batch: {len(items)} item(s)…", mb.run_batch, (items, ys, params), {},
                           self._on_multiyear_batch_done, report_progress=True)
            return
        self._cable_ctx = {"kind": "multiyear", "route_id": r.id, "route": r.name}
        self._my_items = {r.name: ("cable", r.obj)}
        self._my_from_batch = False                    # a new single run replaces any earlier batch
        self._run_task("Multi-year assessment…", multiyear.assess, (), dict(route=r.obj, years=ys,
                                                                          kp_range=sp["kp_range"], **params),
                       self._on_multiyear_done, report_progress=True)

    @Slot(object)
    def _on_multiyear_batch_done(self, batch):
        self._close_progress()
        self._multiyear_batch = batch
        self._multiyear_result = None
        self.viewer.set_analysis_overlay()
        self.multiyear_dialog.show_batch(batch, self.depth_mode)
        self.statusBar().showMessage(f"Multi-year batch: {len(batch['items'])} assessed, {len(batch['errors'])} "
                                     "not assessed", 10000)

    def multiyear_open_item(self, item_id):
        """Full detail of one batch item (recomputed: the batch keeps only the summary arrays)."""
        from core import multiyear
        kind, rt = self._my_items.get(item_id, (None, None))
        if rt is None or self._busy(self.multiyear_dialog):
            return
        rd = next((d for d in self.project.of_kind("route") if d.obj is rt), None)
        self._cable_ctx = {"kind": "multiyear", "route_id": rd.id if rd else None, "route": item_id}
        self._my_from_batch = True                     # keep the batch (its summary stays in the table list)
        self._run_task(f"Opening {item_id}…", multiyear.assess, (), dict(route=rt, years=self._my_years,
                                                                        **self._my_params),
                       self._on_multiyear_done, report_progress=True)

    @Slot(object)
    def _on_multiyear_done(self, res):
        self._close_progress()
        if not getattr(self, "_my_from_batch", False):
            # single run: the earlier batch (other items, other settings) no longer belongs to this result
            self._multiyear_batch = None
            self.multiyear_dialog.batch = None
        self._multiyear_result = res
        self.multiyear_dialog.show_result(res, self.depth_mode)
        s = res["summary"]
        self.statusBar().showMessage(f"Multi-year: re-burial first needed {s['first_reburial_year'] or '—'}, "
                                     f"first exposure {s['first_exposed_year'] or '—'}", 10000)

    def multiyear_overlay(self, year):
        from ui.cable_mixin import RGBA
        res = self._multiyear_result
        if res is None or year not in res["series"]:
            return
        st = res["st"]
        xy = np.column_stack([st["x"], st["y"]])
        top = res["series"][year]["top"]                 # cable level in that year (lowered in cumulative mode)
        col = {"re-burial": RGBA["fail"], "exposed": RGBA["short"], "free span": (0.72, 0.72, 1.0, 1.0),
               "short span": (0.72, 0.72, 1.0, 0.6)}
        lines, labels = [], []
        for sg in res["segments"]:
            if sg["year"] != year:
                continue
            i0, i1 = sg["i0"], min(sg["i1"] + 1, len(xy) - 1)
            lines.append({"xy": xy[i0:i1 + 1], "z": top[i0:i1 + 1], "color": col.get(sg["type"], RGBA["nodata"])})
            if sg["type"] in ("re-burial", "free span"):
                v = sg["value_m"]
                labels.append((xy[i0][0], xy[i0][1], top[i0], f"{year} {sg['type']} {sg['length_m']:.0f} m"
                               + (f", lower {v:.2f} m" if sg["type"] == "re-burial" else f", gap {v:.2f} m")))
        self.viewer.set_analysis_overlay(lines, labels[:80])

    # ---------------------------------------------------------------- exports
    def _my_need_single(self, what):
        if self._multiyear_result is None:
            self.statusBar().showMessage(f"{what}: double-click an item of the batch summary to open it first "
                                         "(Excel and figures cover the whole batch)", 10000)
            return True
        return False

    def multiyear_export_csv(self):
        res = self._multiyear_result
        if self._my_need_single("CSV export"):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export (CSV)", "multiyear.csv", "CSV (*.csv)")
        if not path:
            return
        base = os.path.splitext(path)[0]
        self._write_rows(base + "_per_year.csv", res["table"])
        self._write_rows(base + "_reburial_stretches.csv", res["first"] or [{"note": "no re-burial needed"}])
        if res["summary"]["mode"] == "cumulative":
            self._write_rows(base + "_interventions.csv", res["interventions"] or [{"note": "no re-burial needed"}])
        self._write_rows(base + "_segments.csv", res["segments"] or [{"note": "no segments"}])
        st = res["st"]
        rows = []
        for i in range(len(st["ch"])):
            r = {"kp": float(st["kp"][i]), "x": float(st["x"][i]), "y": float(st["y"][i]),
                 "cable_top": float(res["cable_top"][i])}
            for y, se in res["series"].items():
                r[f"seabed_{y}"] = float(se["seabed"][i])
                r[f"cover_{y}"] = float(se["cover"][i])
            rows.append(r)
        self._write_rows(base + "_stations.csv", rows)
        with open(base + "_settings.csv", "w", encoding="utf-8", newline="\n") as fh:
            fh.write("item,value\n")
            fh.write(f"cable,\"{res['route']}\"\n")
            for k, v in res["params"].items():
                fh.write(f"{k},{v}\n")
            for k, n in getattr(self, "_my_names", {}).items():
                fh.write(f"{k},\"{n}\"\n")
            fh.write("assumption,\"" + ("cumulative: re-burial carried out in the year it is needed, later years use "
                                        "the lowered cable" if res["summary"]["mode"] == "cumulative" else
                                        "independent: every year against the original cable level")
                     + "; cable does not sag (spans are upper estimates)\"\n")
        self.statusBar().showMessage("Saved per-year, re-burial stretches, segments, stations and settings CSV", 10000)

    def multiyear_export_gis(self):
        from fileio import vector_io
        res = self._multiyear_result
        if self._my_need_single("GIS export"):
            return
        path, flt = QFileDialog.getSaveFileName(self, "Export segments (GIS)", "multiyear.gpkg", vector_io.WRITE_FILTER)
        if not path:
            return
        path = vector_io.ext_from_filter(path, flt)
        st = res["st"]
        xy = np.column_stack([st["x"], st["y"]])
        top = np.nan_to_num(res["cable_top"])

        def feat(sg, attrs):
            i0 = sg["i0"]
            i1 = min(max(sg["i1"], i0 + 1), len(xy) - 1)
            return {"type": "LineString", "parts": [np.column_stack([xy[i0:i1 + 1], top[i0:i1 + 1]])], "attrs": attrs}
        segs = [feat(s, {"YEAR": s["year"], "DATA": s["data"], "TYPE": s["type"], "KP_FROM": s["kp_from"],
                         "KP_TO": s["kp_to"], "LENGTH_M": s["length_m"], "VALUE_M": s["value_m"]})
                for s in res["segments"]]
        first = [feat(s, {"FIRST_YEAR": s["first_year"], "KP_FROM": s["kp_from"], "KP_TO": s["kp_to"],
                          "LENGTH_M": s["length_m"], "FIRST_EXP": s["first_exposed"], "FIRST_SPAN": s["first_span"]})
                 for s in res["first"]]
        groups = {"segments": segs, "reburial_stretches": first}
        if res.get("interventions"):
            groups["interventions"] = [feat(s, {"YEAR": s["year"], "KP_FROM": s["kp_from"], "KP_TO": s["kp_to"],
                                                "LENGTH_M": s["length_m"], "LOWER_MAX": s["lowering_max"],
                                                "COVER_AFT": s["cover_after"], "HOURS": s["hours"],
                                                "BEYOND": s["beyond_trencher"]}) for s in res["interventions"]]
        self._write_feature_groups(path, groups)

    def _my_settings(self):
        p = {k: v for k, v in self._my_params.items() if k not in ("dsbl", "isbl")}
        for k, n in getattr(self, "_my_names", {}).items():
            p[k] = n
        p["years"] = ", ".join(f"{y['year']}{' (sim)' if y.get('simulated') else ''}" for y in self._my_years)
        p["seabed_datasets"] = "; ".join(y["name"] for y in self._my_years)
        p["assumption"] = ("cumulative: re-burial carried out in the year it is needed, later years use the lowered "
                           "cable" if p.get("mode") == "cumulative" else "independent: every year against the "
                           "original cable level") + "; cable does not sag (spans are upper estimates)"
        from core.version import APP_NAME, __version__
        p["program"] = f"{APP_NAME} {__version__}"
        return p

    def _my_batch_or_single(self):
        from core import multiyear_batch as mb
        if self._multiyear_batch is not None:
            return self._multiyear_batch
        res = self._multiyear_result
        if res is None:
            return None
        return {"items": [mb.compact(res["route"], "cable", res)], "errors": [], "params": res["params"]}

    def multiyear_export_excel(self):
        from core import multiyear_batch as mb
        batch = self._my_batch_or_single()
        if batch is None:
            return
        if not self._has_openpyxl():
            if not getattr(sys, "frozen", False):
                from ui.deps_dialog import missing_module_box
                missing_module_box("openpyxl", self)          # offers to install it
            if not self._has_openpyxl():
                QMessageBox.information(self, "Excel export", "openpyxl is not installed — the sheets are written as "
                                        "CSV files instead (install openpyxl for one Excel workbook).")
        path, _ = QFileDialog.getSaveFileName(self, "Export workbook", "multiyear_assessment.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            files = mb.write_workbook(path, mb.workbook_sheets(batch, self._my_settings()))
        except PermissionError:
            QMessageBox.warning(self, "Excel export", "The file cannot be written — is it open in Excel?")
            return
        self.statusBar().showMessage("Saved " + ", ".join(os.path.basename(f) for f in files[:4])
                                     + (" …" if len(files) > 4 else ""), 10000)

    @staticmethod
    def _has_openpyxl():
        import importlib
        importlib.invalidate_caches()
        try:
            import openpyxl  # noqa: F401
            return True
        except ImportError:
            return False

    def multiyear_export_figures(self):
        """Five PNG figures per cable / string (as the burial-check script): trends, seabed of every year with
        the lowest envelope, seabed lowering and minimum cover, cable level after re-burial, re-burial count."""
        from PySide6.QtWidgets import QApplication, QProgressDialog
        from core import multiyear
        if self._multiyear_batch is None and self._multiyear_result is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder for the figures")
        if not folder:
            return
        safe = lambda t: re.sub(r'[\\/:*?"<>|]+', "_", str(t)).strip()      # noqa: E731
        if self._multiyear_batch is not None:
            todo = [i["item"] for i in self._multiyear_batch["items"]]
        else:
            todo = [self._multiyear_result["route"]]
        dlg = QProgressDialog("Writing figures…", "Cancel", 0, len(todo), self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        n = 0
        for k, item in enumerate(todo):
            if dlg.wasCanceled():
                break
            dlg.setLabelText(f"Writing figures — {item}")
            dlg.setValue(k)
            QApplication.processEvents()
            if self._multiyear_batch is None:
                res = self._multiyear_result
            else:
                if item not in self._my_items:
                    continue
                _kind, rt = self._my_items[item]
                res = multiyear.assess(rt, self._my_years, **self._my_params)
            for stem, title, x, panels, xl, xd in figure_set(res, self.depth_mode):
                render_figure(os.path.join(folder, f"{safe(item)}_{stem}.png"), f"{item} — {title}", x, panels,
                              xl, xd)
                n += 1
        dlg.setValue(len(todo))
        self.statusBar().showMessage(f"Saved {n} figure(s) to {folder}", 10000)

    def multiyear_save_charts(self):
        from PySide6.QtGui import QPainter, QPixmap
        d = self.multiyear_dialog
        if d is None or self._my_need_single("Save charts"):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save charts", "multiyear.png", "PNG (*.png)")
        if not path:
            return
        from PySide6.QtGui import QColor
        from ui.chart_theme import colors
        a, b = d.trend.grab(), d.prof.grab()
        out = QPixmap(max(a.width(), b.width()), a.height() + b.height())
        out.fill(QColor(colors()["surface"]))
        p = QPainter(out)
        p.drawPixmap(0, 0, a)
        p.drawPixmap(0, a.height(), b)
        p.end()
        out.save(path)
        self.statusBar().showMessage(f"Saved {os.path.basename(path)}", 6000)
