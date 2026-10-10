"""Volume / difference dialog (non-modal): A vs base elevation or surface B, region, results + histogram."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QScrollArea, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from render.colormap import get_lut
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


class Histogram(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(110)
        self.counts = None
        self.edges = None

    def set_data(self, dz, bins=40):
        d = dz[np.isfinite(dz)]
        if len(d) == 0:
            self.counts = None
        else:
            m = max(abs(np.percentile(d, 1)), abs(np.percentile(d, 99)), 1e-3)
            self.counts, self.edges = np.histogram(np.clip(d, -m, m), bins=bins, range=(-m, m))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(C["surface"]))
        if self.counts is None:
            p.end()
            return
        W, H = self.width(), self.height() - 18
        n = len(self.counts)
        top = max(self.counts.max(), 1)
        lut = get_lut("Diverging")
        for i, c in enumerate(self.counts):
            mid = (self.edges[i] + self.edges[i + 1]) / 2
            t = 0.5 + mid / (2 * self.edges[-1])
            rgb = lut[int(np.clip(t, 0, 1) * 255)]
            h = (H - 6) * np.sqrt(c / top)                       # sqrt scale: tails stay visible
            p.fillRect(QRectF(i * W / n + 1, H - h, W / n - 2, h), QColor.fromRgbF(*rgb))
        p.setPen(QPen(QColor(C["muted"]), 1))
        x0 = W / 2
        p.drawLine(int(x0), 0, int(x0), H)
        f = p.font()
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1.5))
        p.setFont(f)
        p.drawText(QRectF(0, H + 2, 90, 14), Qt.AlignLeft, f"{self.edges[0]:+.2f} m")
        p.drawText(QRectF(W / 2 - 30, H + 2, 60, 14), Qt.AlignHCenter, "0")
        p.drawText(QRectF(W - 90, H + 2, 90, 14), Qt.AlignRight, f"{self.edges[-1]:+.2f} m")
        p.end()


class VolumeDialog(QDialog):
    """cb: volume_compute(spec) volume_draw_polygon() volume_load_polygon() volume_add_layer()
    volume_export_csv() volume_export_tif()"""

    def __init__(self, cb, parent=None):
        super().__init__(parent)
        self.cb = cb
        self.setWindowTitle("Volume / difference")
        self.setModal(False)
        self.resize(580, 900)
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
        t = QLabel("Volume / difference")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Δ = A − B on a common grid.  Positive (red): A above B — fill / accretion.  "
                           "Negative (blue): A below B — cut / erosion."))
        v.addWidget(_section("Surface A (e.g. the new survey)"))
        self.cmb_a = QComboBox()
        v.addWidget(self.cmb_a)
        v.addWidget(_section("Compare with B"))
        self.grp_b = QButtonGroup(self)
        self.rb_surf = QRadioButton("Surface")
        self.rb_base = QRadioButton("Base level")
        self.rb_surf.setChecked(True)
        for rb in (self.rb_surf, self.rb_base):
            self.grp_b.addButton(rb)
        row = QHBoxLayout()
        row.addWidget(self.rb_surf)
        self.cmb_b = QComboBox()
        row.addWidget(self.cmb_b, 1)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self.rb_base)
        from ui.design_dialog import LevelInput
        self.base_level = LevelInput(0.0)
        self.lbl_base = _muted("")
        row.addWidget(self.base_level, 1)
        v.addLayout(row)

        v.addWidget(_section("Region"))
        self.grp_r = QButtonGroup(self)
        self.rb_r = {}
        for key, label in (("all", "Whole overlap"), ("route", "Route corridor"), ("subset", "Current subset box"),
                           ("polygon", "Polygon")):
            rb = QRadioButton(label)
            self.grp_r.addButton(rb)
            self.rb_r[key] = rb
        self.rb_r["all"].setChecked(True)
        v.addWidget(self.rb_r["all"])
        row = QHBoxLayout()
        row.addWidget(self.rb_r["route"])
        self.cmb_route = QComboBox()
        self.sp_half = QDoubleSpinBox(minimum=0.1, maximum=1e4, value=10.0, decimals=1)
        self.sp_half.setPrefix("± ")
        self.sp_half.setSuffix(" m")
        row.addWidget(self.cmb_route, 1)
        row.addWidget(self.sp_half)
        v.addLayout(row)
        v.addWidget(self.rb_r["subset"])
        row = QHBoxLayout()
        row.addWidget(self.rb_r["polygon"])
        b1 = QPushButton("Draw…")
        b1.setToolTip("Click the polygon corners in the 3D view; double-click / Enter to finish")
        b1.clicked.connect(cb.volume_draw_polygon)
        b2 = QPushButton("Load SHP…")
        b2.setToolTip("Polygon from a Shapefile (e.g. a design footprint)")
        b2.clicked.connect(cb.volume_load_polygon)
        self.lbl_poly = _muted("none")
        row.addWidget(b1)
        row.addWidget(b2)
        row.addWidget(self.lbl_poly, 1)
        v.addLayout(row)

        form = QFormLayout()
        self.sp_cell = QDoubleSpinBox(minimum=0.01, maximum=1e4, value=1.0, decimals=2, singleStep=0.25)
        self.sp_cell.setSuffix(" m")
        self.sp_cell.setToolTip("Comparison grid cell size (A's own cell size if A is a DEM)")
        form.addRow(_muted("Grid cell"), self.sp_cell)
        v.addLayout(form)
        self.btn_go = QPushButton("Compute")
        self.btn_go.setProperty("primary", True)
        self.btn_go.clicked.connect(self._compute)
        v.addWidget(self.btn_go)

        v.addWidget(_section("Results"))
        self.res = QGridLayout()
        self.res.setHorizontalSpacing(16)
        self.res.setVerticalSpacing(4)
        v.addLayout(self.res)
        self.hist = Histogram()
        v.addWidget(self.hist)
        self.lbl_msg = QLabel(" ")
        self.lbl_msg.setWordWrap(True)
        v.addWidget(self.lbl_msg)
        row = QHBoxLayout()
        self.btn_layer = QPushButton("Add Δ layer")
        self.btn_layer.setToolTip("Add surface A coloured by Δ to the project")
        self.btn_layer.clicked.connect(cb.volume_add_layer)
        self.btn_csv = QPushButton("Report CSV…")
        self.btn_csv.clicked.connect(cb.volume_export_csv)
        self.btn_tif = QPushButton("Δ GeoTIFF…")
        self.btn_tif.clicked.connect(cb.volume_export_tif)
        for b in (self.btn_layer, self.btn_csv, self.btn_tif):
            b.setEnabled(False)
            row.addWidget(b)
        v.addLayout(row)
        self.cmb_a.currentIndexChanged.connect(self._a_changed)
        self.depth = False

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def set_context(self, surfaces, routes, has_subset, depth, polygon_text):
        """surfaces: [(id, name, cell or None)], routes: [(id, name)]"""
        self.depth = depth
        for cmb, items in ((self.cmb_a, surfaces), (self.cmb_b, surfaces)):
            cur = cmb.currentData()
            cmb.blockSignals(True)
            cmb.clear()
            for sid, name, cell in items:
                cmb.addItem(name, (sid, cell))
            i = next((k for k in range(cmb.count()) if cmb.itemData(k) == cur), -1)
            if i < 0 and cmb is self.cmb_b and cmb.count() > 1:
                i = 1
            cmb.setCurrentIndex(max(i, 0))
            cmb.blockSignals(False)
        self.cmb_route.clear()
        for rid, name in routes:
            self.cmb_route.addItem(name, rid)
        self.rb_r["route"].setEnabled(bool(routes))
        self.rb_r["subset"].setEnabled(has_subset)
        self.lbl_poly.setText(polygon_text)
        if not getattr(self, "_ref_initialised", False):      # default: depth for bathymetry, else elevation
            self.base_level.set_mode("depth" if depth else "elev")
            self._ref_initialised = True
        self.rb_surf.setEnabled(self.cmb_b.count() > 0)
        self._a_changed()

    def _a_changed(self):
        d = self.cmb_a.currentData()
        if d and d[1]:
            self.sp_cell.setValue(d[1])

    def spec(self):
        region = next(k for k, rb in self.rb_r.items() if rb.isChecked())
        return {"a": self.cmb_a.currentData()[0] if self.cmb_a.currentData() else None,
                "b": self.cmb_b.currentData()[0] if (self.rb_surf.isChecked() and self.cmb_b.currentData()) else None,
                "base": self.base_level.z() if self.rb_base.isChecked() else None,
                "base_ref": self.base_level.mode(),
                "region": region, "route": self.cmb_route.currentData(), "half": self.sp_half.value(),
                "cell": self.sp_cell.value()}

    def _compute(self):
        sp = self.spec()
        if sp["a"] is None:
            return self.show_error("Create or load a DEM / TIN first")
        if sp["b"] is None and sp["base"] is None:
            return self.show_error("Choose surface B or a base level")
        if sp["b"] == sp["a"]:
            return self.show_error("A and B are the same surface")
        self.lbl_msg.setText(f"<span style='color:{C['muted']}'>Computing…</span>")
        self.cb.volume_compute(sp)

    def show_error(self, msg):
        self.lbl_msg.setText(f"<span style='color:{C['danger']}'>●  {msg}</span>")

    def show_result(self, res, title):
        while self.res.count():
            w = self.res.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
        s = res["stats"]
        cov = s.get("coverage", 1.0)
        rows = [("Compared area", f"{s['area']:,.1f} m²  ({s['cells']:,} cells of {s['cell']:g} m)"),
                ("Coverage", f"{cov:.1%} of the region ({s.get('region_area', s['area']):,.1f} m²)"),
                ("Fill (A above B)", f"{s['fill_volume']:,.2f} m³  over {s['fill_area']:,.1f} m²"),
                ("Cut (A below B)", f"{s['cut_volume']:,.2f} m³  over {s['cut_area']:,.1f} m²"),
                ("Net (fill − cut)", f"{s['net_volume']:+,.2f} m³"),
                ("Δ mean ± std", f"{s['mean']:+.3f} ± {s['std']:.3f} m"),
                ("Δ min / max", f"{s['min']:+.3f} / {s['max']:+.3f} m"),
                ("Δ 5 % / 95 %", f"{s['p05']:+.3f} / {s['p95']:+.3f} m")]
        for r, (k, val) in enumerate(rows):
            kl = _muted(k)
            vl = QLabel(val)
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if k == "Coverage" and cov < 0.995:
                vl.setStyleSheet("color:#ff9f43; font-weight:600")
                vl.setToolTip("Cells where A or B has no data are excluded from the volumes. Fill gaps (DEM "
                              "'Fill holes', a TIN-based DEM or a larger cell) for complete volumes.")
            elif k.startswith("Fill"):
                vl.setStyleSheet("color:#ef8a6b; font-weight:600")
            elif k.startswith("Cut"):
                vl.setStyleSheet("color:#6c9ce8; font-weight:600")
            for lab in (kl, vl):
                lab.setMinimumHeight(lab.fontMetrics().height() + 4)   # rows must never overlap
            self.res.addWidget(kl, r, 0)
            self.res.addWidget(vl, r, 1)
        self.hist.set_data(res["dz"])
        msg = f"<span style='color:{C['success']}'>●</span>  {title}"
        if cov < 0.995:
            msg += (f"<br><span style='color:#ff9f43'>●  {1 - cov:.1%} of the region has no data in A or B and is "
                    "excluded — volumes are under-estimated there. Use DEM hole filling, a TIN-based DEM or a "
                    "larger cell size.</span>")
        self.lbl_msg.setText(msg)
        for b in (self.btn_layer, self.btn_csv, self.btn_tif):
            b.setEnabled(True)
