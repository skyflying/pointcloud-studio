"""Cable & Foundations → cable analyses (non-modal): depth of burial, free spans, rock berm as-built.

The dialog only collects parameters and shows results; the work is done by the main window
(cb = CableMixin): cable_run(kind, spec), cable_export_csv(), cable_export_shp(), cable_save_chart(),
cable_add_layers(), cable_add_sections(), cable_clear_overlay().
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QScrollArea, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from core.route import dist_label
from ui.theme import C, dark_titlebar

RED, GREEN, ORANGE, GREY = "#ef476f", "#06d6a0", "#ff9f43", "#5a606b"
SEABED, CABLE, DESIGN, ASBUILT, PRE = "#4cc9f0", "#ff9f43", "#b8b8ff", "#ffd166", "#8b919c"


def _section(t):
    l = QLabel(t.upper())
    l.setObjectName("sectionTitle")
    return l


def _muted(t):
    l = QLabel(t)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


def _dsp(v, lo, hi, step=0.1, dec=2, suffix=" m"):
    s = QDoubleSpinBox(minimum=lo, maximum=hi, value=v, decimals=dec, singleStep=step)
    if suffix:
        s.setSuffix(suffix)
    return s


def _fmt(v, d=2):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:,.{d}f}"


# ================================================================ KP chart
class KPChart(QWidget):
    """Stacked panels sharing the KP axis.
    panels: [{"lines": [(label, color, values)], "hlines": [(value, color, label)],
              "bands": [(kp_from, kp_to, color)], "ylabel": str, "invert": bool}]"""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(300)
        self.kp = None
        self.panels = []

    def set_data(self, kp, panels, xlabel=None, xdec=3):
        self.kp = np.asarray(kp, float) if kp is not None else None
        self.panels = panels
        self.xlabel = xlabel
        self.xdec = xdec
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(C["surface"]))
        if self.kp is None or not self.panels or len(self.kp) < 2:
            p.end()
            return
        f = p.font()
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1.5))
        p.setFont(f)
        W, H = self.width(), self.height()
        left, right, top, bottom = 58, 10, 8, 22
        n = len(self.panels)
        gap = 10
        ph = (H - top - bottom - gap * (n - 1)) / n
        k0, k1 = float(np.nanmin(self.kp)), float(np.nanmax(self.kp))
        if k1 - k0 < 1e-9:
            k1 = k0 + 1e-3

        def X(k):
            return left + (k - k0) / (k1 - k0) * (W - left - right)

        for i, pan in enumerate(self.panels):
            y0 = top + i * (ph + gap)
            rect = QRectF(left, y0, W - left - right, ph)
            p.fillRect(rect, QColor(C["bg"]))
            vals = [np.asarray(v, float) for _l, _c, v in pan["lines"]]
            vals += [np.array([h[0]], float) for h in pan.get("hlines", [])]
            allv = np.concatenate([v[np.isfinite(v)] for v in vals]) if vals else np.empty(0)
            if len(allv) == 0:
                continue
            lo, hi = float(allv.min()), float(allv.max())
            pad = max((hi - lo) * 0.08, 0.05)
            lo, hi = lo - pad, hi + pad
            inv = pan.get("invert", False)

            def Y(v, lo=lo, hi=hi, y0=y0, inv=inv):
                t = (v - lo) / (hi - lo)
                return y0 + (t if inv else 1 - t) * ph

            for a, b, col in pan.get("bands", []):
                xa, xb = sorted((X(a), X(b)))
                c = QColor(col)
                c.setAlpha(55)
                p.fillRect(QRectF(xa, y0, max(xb - xa, 1.5), ph), c)
            p.setPen(QPen(QColor(C["border"]), 1))
            p.drawRect(rect)
            for t in np.linspace(lo, hi, 4):
                yy = Y(t)
                p.setPen(QPen(QColor(C["muted"]), 1))
                p.drawText(QRectF(0, yy - 8, left - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{t:.2f}")
            for v, col, lab in pan.get("hlines", []):
                pen = QPen(QColor(col), 1, Qt.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(left, Y(v)), QPointF(W - right, Y(v)))
                p.drawText(QRectF(left + 4, Y(v) - 15, 220, 14), Qt.AlignLeft, lab)
            for (lab, col, v), vv in zip(pan["lines"], vals):
                path = QPainterPath()
                pen_down = False
                xs = pan.get("x", self.kp)
                for k, z in zip(xs, vv):
                    if not np.isfinite(z):
                        pen_down = False
                        continue
                    pt = QPointF(X(k), Y(z))
                    if pen_down:
                        path.lineTo(pt)
                    else:
                        path.moveTo(pt)
                        pen_down = True
                p.setPen(QPen(QColor(col), 1.4))
                p.drawPath(path)
            # legend + y label
            lx = left + 6
            for lab, col, _v in pan["lines"]:
                p.setPen(QPen(QColor(col), 1))
                p.drawText(QRectF(lx, y0 + 2, 200, 14), Qt.AlignLeft, "■ " + lab)
                lx += p.fontMetrics().horizontalAdvance("■ " + lab) + 14
            p.setPen(QPen(QColor(C["muted"]), 1))
            p.drawText(QRectF(W - right - 200, y0 + 2, 196, 14), Qt.AlignRight, pan.get("ylabel", ""))
        p.setPen(QPen(QColor(C["muted"]), 1))
        for t in np.linspace(k0, k1, 6):
            p.drawText(QRectF(X(t) - 40, H - bottom + 4, 80, 14), Qt.AlignHCenter, f"{t:.{getattr(self, 'xdec', 3)}f}")
        p.drawText(QRectF(0, H - bottom + 4, left - 6, 14), Qt.AlignRight, getattr(self, "xlabel", None) or dist_label())
        p.end()


# ================================================================ dialog
class CableDialog(QDialog):
    TABS = ("dob", "span", "berm")

    def __init__(self, cb, parent=None):
        super().__init__(parent)
        self.cb = cb
        self.depth = False
        self.res = None
        self.setWindowTitle("Cable analysis")
        self.setModal(False)
        self.resize(700, 960)
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
        t = QLabel("Cable analysis")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Along a cable or pipeline route — subsea or on land — every <i>step</i> metres. A "
                           "<b>cable profile</b> is a route imported with a cable / pipe top column (File → Import Route). "
                           "Elevations are positive up; depth of burial (cover) = ground / seabed − cable top."))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_dob(), "Depth of burial / cover")
        self.tabs.addTab(self._tab_span(), "Free span")
        self.tabs.addTab(self._tab_berm(), "Rock berm as-built")
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

        # ---------------- results
        v.addWidget(_section("Result"))
        self.lbl_sum = QLabel("Run an analysis to see the result.")
        self.lbl_sum.setWordWrap(True)
        self.lbl_sum.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_sum)
        self.chart = KPChart()
        v.addWidget(self.chart)
        trow = QHBoxLayout()
        self.cmb_table = QComboBox()
        self.cmb_table.addItem("Segments", "segments")
        self.cmb_table.addItem("KP bins", "bins")
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
        self.btn_layers = QPushButton("Add layers")
        self.btn_layers.setToolTip("Rock berm: add the design surface (template) and the as-built − design layer")
        self.btn_secs = QPushButton("Add check sections")
        self.btn_secs.setToolTip("Rock berm: cross-sections at the chosen interval, shown in the profile panel")
        self.btn_clear = QPushButton("Clear 3D")
        for b, fn in ((self.btn_csv, cb.cable_export_csv), (self.btn_shp, cb.cable_export_shp),
                      (self.btn_png, cb.cable_save_chart), (self.btn_layers, cb.cable_add_layers),
                      (self.btn_secs, cb.cable_add_sections), (self.btn_clear, cb.cable_clear_overlay)):
            b.clicked.connect(fn)
            b.setEnabled(False)
            brow.addWidget(b)
        v.addLayout(brow)
        v.addWidget(_muted("Double-click a row to go to it in the 3D view."))

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    # ---------------------------------------------------------------- tabs
    def _common(self, form, bin_default=100.0):
        step = _dsp(1.0, 0.05, 100.0, 0.5, 2)
        step.setToolTip("Station spacing along the route")
        binm = _dsp(bin_default, 1.0, 10000.0, 25.0, 0)
        binm.setToolTip("Length of the KP intervals in the summary table")
        form.addRow("Step", step)
        form.addRow("KP bin", binm)
        lim = QCheckBox("Limit to KP")
        k0 = _dsp(0.0, -1e5, 1e5, 0.1, 3, " km")
        k1 = _dsp(1.0, -1e5, 1e5, 0.1, 3, " km")
        h = QHBoxLayout()
        h.addWidget(lim)
        h.addWidget(k0)
        h.addWidget(QLabel("–"))
        h.addWidget(k1)
        lim.toggled.connect(k0.setEnabled)
        lim.toggled.connect(k1.setEnabled)
        k0.setEnabled(False)
        k1.setEnabled(False)
        form.addRow("Range", h)
        return {"step": step, "bin": binm, "lim": lim, "k0": k0, "k1": k1}

    def _tab_dob(self):
        w = QWidget()
        f = QFormLayout(w)
        self.d_cable = QComboBox()
        self.d_seabed = QComboBox()
        self.d_target = _dsp(1.5, 0.0, 50.0, 0.1, 2)
        self.d_width = _dsp(0.5, 0.05, 50.0, 0.1, 2)
        self.d_width.setToolTip("Point cloud as seabed: median of the points within ± this distance of the route")
        f.addRow("Cable profile", self.d_cable)
        f.addRow("Ground / seabed (surface or points)", self.d_seabed)
        f.addRow("Target DoB", self.d_target)
        f.addRow("Corridor ± (points)", self.d_width)
        self.d_c = self._common(f)
        return w

    def _tab_span(self):
        w = QWidget()
        f = QFormLayout(w)
        self.s_grp = QButtonGroup(self)
        self.s_prof = QRadioButton("A — cable profile + seabed")
        self.s_pts = QRadioButton("B — point cloud only (cable visible in the data)")
        self.s_prof.setChecked(True)
        for rb in (self.s_prof, self.s_pts):
            self.s_grp.addButton(rb)
            f.addRow(rb)
        self.s_cable = QComboBox()
        self.s_seabed = QComboBox()
        self.s_route = QComboBox()
        self.s_pc = QComboBox()
        f.addRow("A: cable profile", self.s_cable)
        f.addRow("A: ground / seabed", self.s_seabed)
        f.addRow("B: route", self.s_route)
        f.addRow("B: point cloud", self.s_pc)
        self.s_diam = _dsp(0.2, 0.01, 5.0, 0.01, 3)
        self.s_gap = _dsp(0.1, 0.0, 10.0, 0.05, 2)
        self.s_len = _dsp(10.0, 0.0, 1000.0, 1.0, 1)
        self.s_search = _dsp(1.5, 0.1, 50.0, 0.5, 1)
        self.s_search.setToolTip("B: the cable is searched within ± this distance of the route")
        self.s_side = _dsp(3.0, 0.5, 50.0, 0.5, 1)
        self.s_side.setToolTip("B: width of the seabed bands beside the search corridor (each side)")
        f.addRow("Cable diameter", self.s_diam)
        f.addRow("Minimum gap", self.s_gap)
        f.addRow("Span length to report", self.s_len)
        f.addRow("B: search ±", self.s_search)
        f.addRow("B: seabed band", self.s_side)
        self.s_c = self._common(f)
        f.addRow(_muted("Gap = cable bottom (top − diameter) − seabed. Spans shorter than the reporting length are "
                        "listed as 'short'. Method B estimates the seabed under the cable from both sides — check "
                        "detected spans in the profile view."))

        def sync():
            a = self.s_prof.isChecked()
            for wd in (self.s_cable, self.s_seabed):
                wd.setEnabled(a)
            for wd in (self.s_route, self.s_pc, self.s_search, self.s_side):
                wd.setEnabled(not a)
        self.s_prof.toggled.connect(sync)
        sync()
        return w

    def _tab_berm(self):
        w = QWidget()
        f = QFormLayout(w)
        self.b_route = QComboBox()
        self.b_k0 = _dsp(0.0, -1e5, 1e5, 0.01, 3, " km")
        self.b_k1 = _dsp(0.1, -1e5, 1e5, 0.01, 3, " km")
        h = QHBoxLayout()
        h.addWidget(self.b_k0)
        h.addWidget(QLabel("–"))
        h.addWidget(self.b_k1)
        f.addRow("Route / cable", self.b_route)
        f.addRow("Berm KP", h)
        self.b_asb = QComboBox()
        self.b_pre = QComboBox()
        f.addRow("As-built surface", self.b_asb)
        f.addRow("Pre-lay surface", self.b_pre)
        self.b_grp = QButtonGroup(self)
        self.b_tpl = QRadioButton("Design from template (on the pre-lay surface)")
        self.b_srf = QRadioButton("Design surface")
        self.b_tpl.setChecked(True)
        for rb in (self.b_tpl, self.b_srf):
            self.b_grp.addButton(rb)
        f.addRow(self.b_tpl)
        self.b_cw = _dsp(2.0, 0.0, 100.0, 0.5, 2)
        self.b_h = _dsp(1.0, 0.05, 50.0, 0.1, 2)
        self.b_n = _dsp(3.0, 0.1, 50.0, 0.5, 2, "")
        self.b_n.setPrefix("1 : ")
        self.b_en = _dsp(3.0, 0.1, 50.0, 0.5, 2, "")
        self.b_en.setPrefix("1 : ")
        self.b_cell = _dsp(0.5, 0.05, 20.0, 0.1, 2)
        f.addRow("  Crest width", self.b_cw)
        f.addRow("  Height above seabed", self.b_h)
        f.addRow("  Side slopes", self.b_n)
        f.addRow("  End slopes", self.b_en)
        f.addRow("  Grid cell", self.b_cell)
        f.addRow(self.b_srf)
        self.b_design = QComboBox()
        self.b_hw = _dsp(10.0, 0.5, 500.0, 1.0, 1)
        self.b_hw.setToolTip("Analysed corridor ± this distance of the route. Footprint = where the design is above "
                             "the pre-lay surface; without a pre-lay surface the whole corridor")
        f.addRow("  Design surface", self.b_design)
        f.addRow("  Corridor ±", self.b_hw)
        self.b_tol = _dsp(0.1, 0.0, 5.0, 0.05, 2)
        self.b_tol.setToolTip("As-built may be this much below design before it counts as under-filled")
        self.b_cover_on = QCheckBox("Minimum cover over cable")
        self.b_cover = _dsp(1.0, 0.0, 20.0, 0.1, 2)
        hc = QHBoxLayout()
        hc.addWidget(self.b_cover_on)
        hc.addWidget(self.b_cover)
        self.b_bin = _dsp(25.0, 1.0, 10000.0, 5.0, 0)
        self.b_step = _dsp(0.5, 0.05, 50.0, 0.25, 2)
        self.b_every = _dsp(25.0, 0.0, 10000.0, 5.0, 0)
        self.b_every.setToolTip("Interval of the check cross-sections (0 = none)")
        f.addRow("Tolerance (under-fill)", self.b_tol)
        f.addRow(hc)
        f.addRow("KP bin", self.b_bin)
        f.addRow("Step (centre line)", self.b_step)
        f.addRow("Check sections every", self.b_every)

        def sync():
            t = self.b_tpl.isChecked()
            for wd in (self.b_cw, self.b_h, self.b_n, self.b_en, self.b_cell):
                wd.setEnabled(t)
            for wd in (self.b_design, self.b_hw):
                wd.setEnabled(not t)
        self.b_tpl.toggled.connect(sync)
        sync()
        return w

    # ---------------------------------------------------------------- context
    def set_context(self, routes, surfaces, clouds, depth):
        """routes: [(id, name, has_z, kp_range)], surfaces: [(id, name)], clouds: [(id, name)]"""
        self.depth = depth
        cables = [(i, n) for i, n, z, _k in routes if z]
        allr = [(i, n) for i, n, _z, _k in routes]
        self._kp = {i: k for i, _n, _z, k in routes}
        seab = surfaces + [(i, f"{n}  (points)") for i, n in clouds]

        def fill(cmb, items, none=False):
            cur = cmb.currentData()
            cmb.blockSignals(True)
            cmb.clear()
            if none:
                cmb.addItem("(none)", None)
            for i, n in items:
                cmb.addItem(n, i)
            j = cmb.findData(cur)
            cmb.setCurrentIndex(j if j >= 0 else 0)
            cmb.blockSignals(False)

        fill(self.d_cable, cables)
        fill(self.d_seabed, seab)
        fill(self.s_cable, cables)
        for cmb, c in ((self.d_cable, self.d_c), (self.s_cable, self.s_c), (self.s_route, self.s_c)):
            if not getattr(cmb, "_kp_hooked", False):          # range follows the chosen route
                cmb.currentIndexChanged.connect(lambda _i, cmb=cmb, c=c: self._route_kp(cmb, c))
                cmb._kp_hooked = True
        fill(self.s_seabed, seab)
        fill(self.s_route, allr)
        fill(self.s_pc, clouds)
        old = self.b_route.currentData()
        fill(self.b_route, cables + [r for r in allr if r not in cables])
        if self.b_route.currentData() != old and self.b_route.currentData() is not None:
            k = self._kp.get(self.b_route.currentData())
            if k:
                self.b_k0.setValue(k[0])
                self.b_k1.setValue(min(k[1], k[0] + 0.1))
        fill(self.b_asb, surfaces)
        fill(self.b_pre, surfaces, none=True)
        fill(self.b_design, surfaces)
        # first time only: suggest a pre-lay surface other than the as-built one ("(none)" is a valid choice later)
        if not getattr(self, "_pre_suggested", False) and self.b_pre.count() > 2:
            other = next((k for k in range(1, self.b_pre.count())
                          if self.b_pre.itemData(k) != self.b_asb.currentData()), 1)
            self.b_pre.setCurrentIndex(other)
            self._pre_suggested = True
        for c, cmb in ((self.d_c, self.d_cable), (self.s_c, self.s_cable)):
            if not c["lim"].isChecked():
                self._route_kp(cmb, c)

    def _route_kp(self, cmb, c):
        k = self._kp.get(cmb.currentData()) if hasattr(self, "_kp") else None
        if k and not c["lim"].isChecked():
            c["k0"].setValue(min(k))
            c["k1"].setValue(max(k))

    # ---------------------------------------------------------------- run
    def show_error(self, msg):
        self.lbl_msg.setText(f"<span style='color:{C['danger']}'>●  {msg}</span>")

    def _kp_range(self, c):
        return (c["k0"].value(), c["k1"].value()) if c["lim"].isChecked() else None

    def _kp_error(self, route_id, rng):
        """Inline check that a KP range overlaps the route (None = OK)."""
        k = self._kp.get(route_id) if hasattr(self, "_kp") else None
        if rng is None or not k:
            return None
        lo, hi = sorted(rng)
        if hi - lo < 1e-9:
            return "KP from and to are the same"
        if hi < min(k) - 1e-9 or lo > max(k) + 1e-9:
            return f"KP {lo:g} – {hi:g} is outside the route (KP {min(k):.3f} – {max(k):.3f})"
        return None

    def _run(self):
        self.lbl_msg.setText(" ")
        kind = self.TABS[self.tabs.currentIndex()]
        if kind == "dob":
            if self.d_cable.currentData() is None:
                return self.show_error("Import a cable profile first (a route with a cable-top column)")
            if self.d_seabed.currentData() is None:
                return self.show_error("No seabed surface or point cloud")
            c = self.d_c
            spec = {"cable": self.d_cable.currentData(), "seabed": self.d_seabed.currentData(),
                    "target": self.d_target.value(), "width": self.d_width.value(), "step": c["step"].value(),
                    "bin_m": c["bin"].value(), "kp_range": self._kp_range(c)}
        elif kind == "span":
            c = self.s_c
            spec = {"method": "profile" if self.s_prof.isChecked() else "points", "diameter": self.s_diam.value(),
                    "gap_min": self.s_gap.value(), "len_min": self.s_len.value(), "step": c["step"].value(),
                    "bin_m": c["bin"].value(), "kp_range": self._kp_range(c)}
            if spec["method"] == "profile":
                if self.s_cable.currentData() is None or self.s_seabed.currentData() is None:
                    return self.show_error("Method A needs a cable profile and a seabed")
                spec.update(cable=self.s_cable.currentData(), seabed=self.s_seabed.currentData())
            else:
                if self.s_route.currentData() is None or self.s_pc.currentData() is None:
                    return self.show_error("Method B needs a route and a point cloud")
                spec.update(route=self.s_route.currentData(), pc=self.s_pc.currentData(),
                            search=self.s_search.value(), side=self.s_side.value())
        else:
            if self.b_route.currentData() is None or self.b_asb.currentData() is None:
                return self.show_error("Choose the route and the as-built surface")
            tpl = self.b_tpl.isChecked()
            if tpl and self.b_pre.currentData() is None:
                return self.show_error("The template needs the pre-lay surface")
            if not tpl and self.b_design.currentData() is None:
                return self.show_error("Choose the design surface")
            if abs(self.b_k1.value() - self.b_k0.value()) < 1e-6:
                return self.show_error("Berm KP from and to are the same")
            spec = {"route": self.b_route.currentData(), "kp_from": self.b_k0.value(), "kp_to": self.b_k1.value(),
                    "asbuilt": self.b_asb.currentData(), "pre": self.b_pre.currentData(), "template": tpl,
                    "crest_width": self.b_cw.value(), "height": self.b_h.value(), "slope_n": self.b_n.value(),
                    "end_n": self.b_en.value(), "cell": self.b_cell.value(), "design": self.b_design.currentData(),
                    "half_width": self.b_hw.value(), "tol": self.b_tol.value(),
                    "min_cover": self.b_cover.value() if self.b_cover_on.isChecked() else None,
                    "bin_m": self.b_bin.value(), "step": self.b_step.value(), "every": self.b_every.value()}
        rid = spec.get("cable", spec.get("route"))
        err = self._kp_error(rid, spec.get("kp_range") or ((spec["kp_from"], spec["kp_to"]) if kind == "berm" else None))
        if err:
            return self.show_error(err)
        self.cb.cable_run(kind, spec)

    # ---------------------------------------------------------------- results
    def show_result(self, res, depth):
        self.res = res
        self.depth = depth
        s = res["summary"]
        k = res["kind"]
        if k == "dob":
            p = res["params"]
            rows = [("Analysed", f"{s['length']:,.1f} m · coverage {s['coverage_pct']:.1f}%"),
                    ("Compliant (DoB ≥ {:.2f} m)".format(p["target"]),
                     f"{s['compliant']:,.1f} m  ({s['compliant_pct']:.1f}% of the covered length)"),
                    ("Below target", f"<span style='color:{RED}'>{s['non_compliant']:,.1f} m in "
                                     f"{s['segments']} segment(s)</span>"),
                    ("Exposed (DoB < 0)", f"{s['exposed']:,.1f} m"),
                    ("Minimum DoB", f"{_fmt(s['dob_min'])} m at {dist_label()} {_fmt(s['kp_at_min'], 3)}"),
                    ("Mean DoB", f"{_fmt(s['dob_mean'])} m")]
            if "vs_reported_mean" in s:
                rows.append(("vs reported DoB", f"mean {s['vs_reported_mean']:+.2f} m, max |Δ| "
                                                f"{_fmt(s['vs_reported_max_abs'])} m"))
        elif k == "span":
            p = res["params"]
            rows = [("Method", "A — cable profile + seabed" if p["method"] == "profile" else
                     "B — point cloud (seabed under the cable interpolated from both sides)"),
                    ("Analysed", f"{s['length']:,.1f} m · gap known on {s['covered']:,.1f} m "
                                 f"({s['coverage_pct']:.1f}%)"),
                    (f"Spans ≥ {p['len_min']:g} m", f"<span style='color:{RED}'>{s['spans']}</span> · total "
                                                    f"{s['span_length_total']:,.1f} m"),
                    ("Short spans", str(s["short_spans"])),
                    ("Longest span", f"{s['longest']:,.1f} m"),
                    ("Largest gap", f"{_fmt(s['gap_max'])} m")]
            if "visible" in s:
                rows.append(("Cable visible in the data", f"{s['visible']:,.1f} m"))
        else:
            rows = [("Footprint", f"{s['footprint_area']:,.1f} m² · measured {s['measured_pct']:.1f}%"),
                    (f"Coverage (≥ design − {s['tol']:g} m)", f"{_fmt(s['coverage_pct'], 1)}%"),
                    ("Under-filled", f"<span style='color:{RED}'>{s['under_area']:,.1f} m² · "
                                     f"{s['under_volume']:,.2f} m³</span> · {s['under_segments']} centre-line "
                                     "segment(s)"),
                    ("Over-filled", f"{s['over_area']:,.1f} m² · {s['over_volume']:,.2f} m³"),
                    ("As-built − design", f"mean {_fmt(s['error_mean'])} m · min {_fmt(s['error_min'])} m")]
            if "design_volume" in s:
                rows.append(("Design / placed volume", f"{s['design_volume']:,.1f} / {s['placed_volume']:,.1f} m³"))
            if "cover_min" in s:
                rows.append(("Minimum cover over cable", f"{s['cover_min']:.2f} m at {dist_label()} "
                                                         f"{s['kp_at_cover_min']:.3f}"))
            if "cover_below_min_length" in s:
                rows.append(("Cover below minimum", f"{s['cover_below_min_length']:,.1f} m"))
        self.lbl_sum.setText(f"<b>{res['route']}</b><br>" + "<br>".join(
            f"<span style='color:{C['muted']}'>{a}:</span> {b}" for a, b in rows))
        self._chart()
        self._fill_table()
        for b in (self.btn_csv, self.btn_shp, self.btn_png, self.btn_clear):
            b.setEnabled(True)
        self.btn_layers.setEnabled(k == "berm")
        self.btn_secs.setEnabled(k == "berm" and bool(res.get("sections")))

    def _elev(self, v):
        return -np.asarray(v, float) if self.depth else np.asarray(v, float)

    def _chart(self):
        r = self.res
        se = r["series"]
        kp = r["st"]["kp"]
        zl = "Depth (m)" if self.depth else "Elevation (m)"
        if r["kind"] == "dob":
            bands = [(g["kp_from"], g["kp_to"], RED) for g in r["segments"]]
            lines = [("Seabed", SEABED, self._elev(se["seabed"])), ("Cable top", CABLE, self._elev(se["cable_top"]))]
            dl = [("DoB", GREEN, se["dob"])]
            if se.get("dob_reported") is not None:
                dl.append(("Reported DoB", PRE, se["dob_reported"]))
            panels = [{"lines": lines, "bands": bands, "ylabel": zl, "invert": False},
                      {"lines": dl, "bands": bands, "ylabel": "DoB (m)",
                       "hlines": [(r["params"]["target"], RED, f"target {r['params']['target']:g} m"),
                                  (0.0, GREY, "seabed")]}]
        elif r["kind"] == "span":
            bands = [(g["kp_from"], g["kp_to"], RED if g["flag"] == "SPAN" else ORANGE) for g in r["segments"]]
            panels = [{"lines": [("Seabed", SEABED, self._elev(se["seabed"])),
                                 ("Cable top", CABLE, self._elev(se["cable_top"]))], "bands": bands, "ylabel": zl},
                      {"lines": [("Gap", ORANGE, se["gap"])], "bands": bands, "ylabel": "Gap (m)",
                       "hlines": [(r["params"]["gap_min"], RED, f"min gap {r['params']['gap_min']:g} m")]}]
        else:
            bands = [(g["kp_from"], g["kp_to"], RED) for g in r["segments"]]
            lines = [("Design", DESIGN, self._elev(se["design"])), ("As-built", ASBUILT, self._elev(se["asbuilt"]))]
            if "pre" in se:
                lines.insert(0, ("Pre-lay", PRE, self._elev(se["pre"])))
            if "cable_top" in se:
                lines.append(("Cable top", CABLE, self._elev(se["cable_top"])))
            panels = [{"lines": lines, "bands": bands, "ylabel": zl},
                      {"lines": [("As-built − design (crest)", ORANGE, se["crest_error"])], "bands": bands,
                       "ylabel": "Δ (m)", "hlines": [(-r["params"]["tol"], RED, f"−{r['params']['tol']:g} m"),
                                                     (0.0, GREY, "design")]}]
            if "cover" in se:
                h = [(r["params"]["min_cover"], RED, f"min {r['params']['min_cover']:g} m")] \
                    if r["params"].get("min_cover") is not None else []
                panels.append({"lines": [("Cover over cable", GREEN, se["cover"])], "ylabel": "Cover (m)",
                               "hlines": h, "bands": [(g["kp_from"], g["kp_to"], RED) for g in r["cover_segments"]]})
        self.chart.setMinimumHeight(150 * len(panels) + 30)
        self.chart.set_data(kp, panels)

    def _table_rows(self):
        r = self.res
        which = self.cmb_table.currentData()
        if which == "bins":
            return r["bins"]
        segs = list(r["segments"])
        if r["kind"] == "berm":
            segs = [dict(s, type="under-filled crest") for s in segs] + \
                   [dict(s, type="cover below minimum") for s in r.get("cover_segments", [])]
        return segs

    def _fill_table(self, *_):
        self.table.clear()
        if self.res is None:
            return
        rows = self._table_rows()
        hide = {"i0", "i1", "x_from", "y_from", "x_to", "y_to"}
        cols = []
        for row in rows:
            for c in row:
                if c not in hide and c not in cols:
                    cols.append(c)
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels([c.replace("kp", dist_label()).replace("_", " ") for c in cols])
        for i, row in enumerate(rows):
            for j, c in enumerate(cols):
                v = row.get(c, "")
                if isinstance(v, float):
                    txt = _fmt(v, 3 if c.startswith("kp") else 2)
                else:
                    txt = str(v)
                it = QTableWidgetItem(txt)
                if isinstance(v, float):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()

    def _row_clicked(self, i, _j):
        rows = self._table_rows()
        if 0 <= i < len(rows):
            row = rows[i]
            self.cb.cable_goto(row.get("kp_from"), row.get("kp_to"))
