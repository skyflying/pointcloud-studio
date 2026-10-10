"""Earthworks design dialog (non-modal): cut / fill to a level with side slopes · trench along a route.
Works for marine (dredging, seabed) and land (excavation, grading, embankments) data alike: every level is
entered either as an elevation (Z, up) or as a depth (positive down), chosen per field."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QScrollArea, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ui.theme import C, dark_titlebar
from ui.volume_dialog import Histogram


def _section(t):
    l = QLabel(t.upper())
    l.setObjectName("sectionTitle")
    return l


def _muted(t):
    l = QLabel(t)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


def earthwork_quantities(s, spec):
    """Loose cut, material needed for the fill and the cut/fill balance (all from in-place volumes)."""
    b, k = float(spec.get("bulk", 1.0) or 1.0), float(spec.get("shrink", 1.0) or 1.0)
    cut, fill = s["cut_volume"], s["fill_volume"]
    need = fill / k
    return {"bulk": b, "shrink": k, "cut_loose": cut * b, "fill_material_in_place": need,
            "balance_in_place": cut - need,
            "cut_loose_overcut": s["cut_volume_overdredge"] * b if "cut_volume_overdredge" in s else None}


def earthwork_rows(s, spec):
    q = earthwork_quantities(s, spec)
    rows = []
    if abs(q["bulk"] - 1) > 1e-9:
        txt = f"{q['cut_loose']:,.2f} m³  (× {q['bulk']:g})"
        if q["cut_loose_overcut"] is not None:
            txt += f"  ·  incl. over-cut {q['cut_loose_overcut']:,.2f} m³"
        rows.append(("Cut, loose", txt))
    if abs(q["shrink"] - 1) > 1e-9:
        rows.append(("Material needed for fill", f"{q['fill_material_in_place']:,.2f} m³ in place  (fill ÷ {q['shrink']:g})"))
    if s["cut_volume"] > 0 and s["fill_volume"] > 0:
        bal = q["balance_in_place"]
        rows.append(("Cut / fill balance", f"{'surplus' if bal >= 0 else 'shortfall'} {abs(bal):,.2f} m³ in place"
                     + ("  (cut reused as fill)" if bal >= 0 else "  (import needed)")))
    return rows


class LevelInput(QWidget):
    """Level entry with its own reference: Elevation (Z, up) or Depth (positive down)."""

    def __init__(self, value=0.0, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.cmb = QComboBox()
        self.cmb.addItem("Elevation (Z)", "elev")
        self.cmb.addItem("Depth (+ down)", "depth")
        self.cmb.setToolTip("How the value is given: elevation (Z, positive up — land, chart datum) or "
                            "depth (positive down — bathymetry). Independent of the display's depth mode.")
        self.sp = _dsp(value, -1e5, 1e5, 0.5, 2)
        h.addWidget(self.cmb)
        h.addWidget(self.sp, 1)
        self._mode = "elev"
        self.cmb.currentIndexChanged.connect(self._switch)

    def _switch(self):
        new = self.cmb.currentData()
        if new != self._mode:                      # keep the same physical level
            self.sp.setValue(-self.sp.value())
            self._mode = new

    def set_mode(self, mode):
        self.cmb.setCurrentIndex(self.cmb.findData(mode))

    def mode(self):
        return self.cmb.currentData()

    def z(self):
        """Level as elevation (Z, up)."""
        return -self.sp.value() if self.mode() == "depth" else self.sp.value()


def _dsp(v, lo, hi, step=0.5, dec=2, suffix=" m", prefix=""):
    s = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=dec, singleStep=step)
    s.setValue(v)
    s.setSuffix(suffix)
    s.setPrefix(prefix)
    return s


class DesignDialog(QDialog):
    """cb: design_compute(kind, spec) volume_draw_polygon() volume_load_polygon() design_add_layers()
    design_export_csv() design_export_tif(which)"""

    def __init__(self, cb, parent=None):
        super().__init__(parent)
        self.cb = cb
        self.depth = False
        self.setWindowTitle("Earthworks design")
        self.setModal(False)
        self.resize(600, 940)
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
        t = QLabel("Earthworks design")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Builds a design surface from the existing surface — seabed or ground — and reports "
                           "cut (dredge / excavation) and fill volumes. Side slopes are 1 : n (vertical : horizontal). "
                           "Levels can be entered as elevation (Z) or as depth."))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._dredge_tab(), "Cut / fill")
        self.tabs.addTab(self._trench_tab(), "Trench")
        v.addWidget(self.tabs)
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
        self.kp_table = QTableWidget(0, 3)
        from core.route import dist_label
        self.kp_table.setHorizontalHeaderLabels([f"{dist_label()} from", f"{dist_label()} to", "Excavation (m³)"])
        self.kp_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.kp_table.verticalHeader().setVisible(False)
        self.kp_table.setMinimumHeight(170)
        self.kp_table.hide()
        v.addWidget(self.kp_table)
        self.lbl_msg = QLabel(" ")
        self.lbl_msg.setWordWrap(True)
        v.addWidget(self.lbl_msg)
        row = QHBoxLayout()
        self.btn_layers = QPushButton("Add layers")
        self.btn_layers.setToolTip("Add the design surface and the cut / fill (Δ) layer to the project")
        self.btn_layers.clicked.connect(cb.design_add_layers)
        self.btn_csv = QPushButton("Report CSV…")
        self.btn_csv.clicked.connect(cb.design_export_csv)
        self.btn_tif = QPushButton("Design GeoTIFF…")
        self.btn_tif.clicked.connect(lambda: cb.design_export_tif("design"))
        self.btn_dtif = QPushButton("Δ GeoTIFF…")
        self.btn_dtif.clicked.connect(lambda: cb.design_export_tif("delta"))
        for b in (self.btn_layers, self.btn_csv, self.btn_tif, self.btn_dtif):
            b.setEnabled(False)
            row.addWidget(b)
        v.addLayout(row)

    # ---------- tabs ----------
    def _dredge_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        f.setHorizontalSpacing(14)
        f.setVerticalSpacing(8)
        self.d_surface = QComboBox()
        self.d_surface.setToolTip("DEM / TIN of the existing seabed or ground")
        self.d_surface.currentIndexChanged.connect(self._update_hints)
        f.addRow(_muted("Existing surface"), self.d_surface)
        self.grp = QButtonGroup(self)
        self.rb = {}
        box = QVBoxLayout()
        r1 = QHBoxLayout()
        self.rb["polygon"] = QRadioButton("Polygon")
        b1 = QPushButton("Draw…")
        b1.clicked.connect(self.cb.volume_draw_polygon)
        b2 = QPushButton("Load SHP…")
        b2.clicked.connect(self.cb.volume_load_polygon)
        self.lbl_poly = _muted("none")
        for x in (self.rb["polygon"], b1, b2, self.lbl_poly):
            r1.addWidget(x)
        box.addLayout(r1)
        r2 = QHBoxLayout()
        self.rb["route"] = QRadioButton("Route corridor")
        self.d_route = QComboBox()
        self.d_half = _dsp(10.0, 0.1, 1e4, 1, 1, " m", "± ")
        for x in (self.rb["route"], self.d_route, self.d_half):
            r2.addWidget(x)
        box.addLayout(r2)
        self.rb["subset"] = QRadioButton("Current subset box")
        box.addWidget(self.rb["subset"])
        for k, rb in self.rb.items():
            self.grp.addButton(rb)
        self.rb["polygon"].setChecked(True)
        f.addRow(_muted("Footprint (bottom)"), box)
        self.d_mode = QComboBox()
        self.d_mode.addItem("Cut and fill to the level (level / grade)", "level")
        self.d_mode.addItem("Cut only (dredge / excavate)", "cut")
        self.d_mode.addItem("Fill only (fill / embankment / reclamation)", "fill")
        f.addRow(_muted("Operation"), self.d_mode)
        self.d_level = LevelInput(0.0)
        self.d_level.cmb.currentIndexChanged.connect(self._update_hints)
        f.addRow(_muted("Design level"), self.d_level)
        self.d_hint = _muted("")
        f.addRow(QLabel(""), self.d_hint)
        self.d_over = _dsp(0.0, 0.0, 100, 0.1, 2)
        self.d_over.setToolTip("Allowed / paid over-dredge or over-excavation below the design level "
                               "(reported separately)")
        f.addRow(_muted("Over-cut allowance"), self.d_over)
        self.d_cut_n = _dsp(3.0, 0.0, 100, 0.5, 2, "", "1 : ")
        self.d_cut_n.setToolTip("Cut side slope (dredged / excavated): 1 m vertical per n m horizontal · 0 = vertical wall")
        self.d_fill_n = _dsp(3.0, 0.0, 100, 0.5, 2, "", "1 : ")
        self.d_fill_n.setToolTip("Fill side slope: 1 m vertical per n m horizontal")
        f.addRow(_muted("Cut slope"), self.d_cut_n)
        f.addRow(_muted("Fill slope"), self.d_fill_n)
        self.d_bulk = _dsp(1.0, 0.5, 3.0, 0.05, 2, "")
        self.d_bulk.setToolTip("Bulking (swell) factor: cut in place → loose volume (trucks, barges, hopper).\n"
                               "e.g. 1.25 for soil, 1.5–1.7 for rock · 1.00 = not used")
        self.d_shrink = _dsp(1.0, 0.5, 1.5, 0.05, 2, "")
        self.d_shrink.setToolTip("Shrinkage factor: in place → compacted fill volume (e.g. 0.90).\n"
                                 "Material needed for the fill = fill ÷ factor · 1.00 = not used")
        f.addRow(_muted("Bulking factor"), self.d_bulk)
        f.addRow(_muted("Shrinkage factor"), self.d_shrink)
        self.d_cell = _dsp(1.0, 0.05, 1e3, 0.25, 2)
        f.addRow(_muted("Grid cell"), self.d_cell)
        return w

    def _trench_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        f.setHorizontalSpacing(14)
        f.setVerticalSpacing(8)
        self.t_surface = QComboBox()
        self.t_surface.setToolTip("DEM / TIN of the existing seabed or ground")
        self.t_surface.currentIndexChanged.connect(self._update_hints)
        f.addRow(_muted("Existing surface"), self.t_surface)
        self.t_route = QComboBox()
        f.addRow(_muted("Route"), self.t_route)
        self.t_width = _dsp(1.0, 0.0, 1e3, 0.1, 2)
        f.addRow(_muted("Bottom width"), self.t_width)
        self.t_slope = _dsp(1.0, 0.0, 100, 0.25, 2, "", "1 : ")
        self.t_slope.setToolTip("Trench side slope: 1 m vertical per n m horizontal · 0 = vertical walls")
        f.addRow(_muted("Side slope"), self.t_slope)
        self.t_ref = QComboBox()
        self.t_ref.addItem("Depth below the existing surface (seabed / ground)", "seabed")
        self.t_ref.addItem("Bottom at a design level", "level")
        self.t_ref.currentIndexChanged.connect(self._ref_changed)
        f.addRow(_muted("Trench bottom"), self.t_ref)
        self.t_dol = _dsp(1.5, 0.0, 100, 0.1, 2)
        self.lbl_dol = _muted("Depth below surface")
        self.t_dol.setToolTip("Trench bottom below the (smoothed) existing surface — depth of lowering / cover")
        f.addRow(self.lbl_dol, self.t_dol)
        self.t_smooth = _dsp(20.0, 0.0, 1e4, 5, 1)
        self.t_smooth.setToolTip("The existing surface on the centre line is averaged over this length, so the "
                                 "trench bottom does not follow every ripple, sand wave or local bump")
        self.lbl_smooth = _muted("Surface smoothing")
        f.addRow(self.lbl_smooth, self.t_smooth)
        self.t_level = LevelInput(0.0)
        self.t_level.cmb.currentIndexChanged.connect(self._update_hints)
        self.lbl_tlevel = _muted("Bottom level")
        f.addRow(self.lbl_tlevel, self.t_level)
        self.t_hint = _muted("")
        self.lbl_thint = QLabel("")
        f.addRow(self.lbl_thint, self.t_hint)
        self.t_bulk = _dsp(1.0, 0.5, 3.0, 0.05, 2, "")
        self.t_bulk.setToolTip("Bulking (swell) factor: excavated in place → loose volume · 1.00 = not used")
        f.addRow(_muted("Bulking factor"), self.t_bulk)
        self.t_kp = _dsp(100.0, 1.0, 1e5, 10, 1)
        f.addRow(_muted("Volume table every"), self.t_kp)
        self.t_cell = _dsp(0.25, 0.02, 100, 0.05, 2)
        self.t_cell.setToolTip("Use a cell clearly smaller than the bottom width")
        f.addRow(_muted("Grid cell"), self.t_cell)
        self._ref_changed()
        return w

    def _ref_changed(self):
        seabed = self.t_ref.currentData() == "seabed"
        for x in (self.t_dol, self.lbl_dol, self.t_smooth, self.lbl_smooth):
            x.setVisible(seabed)
        for x in (self.t_level, self.lbl_tlevel, self.t_hint, self.lbl_thint):
            x.setVisible(not seabed)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    # ---------- context / spec ----------
    def set_context(self, surfaces, routes, has_subset, depth, polygon_text, zranges=None):
        self.depth = depth
        self.zranges = zranges or {}
        if not getattr(self, "_ref_initialised", False):      # default: depth for bathymetry, else elevation
            for li in (self.d_level, self.t_level):
                li.set_mode("depth" if depth else "elev")
            self._ref_initialised = True
        for cmb in (self.d_surface, self.t_surface):
            cur = cmb.currentData()
            cmb.clear()
            for sid, name, cell in surfaces:
                cmb.addItem(name, (sid, cell))
            i = next((k for k in range(cmb.count()) if cmb.itemData(k) == cur), 0)
            cmb.setCurrentIndex(i)
        for cmb in (self.d_route, self.t_route):
            cur = cmb.currentData()
            cmb.clear()
            for rid, name in routes:
                cmb.addItem(name, rid)
            i = cmb.findData(cur)
            cmb.setCurrentIndex(max(i, 0))
        self.rb["route"].setEnabled(bool(routes))
        self.rb["subset"].setEnabled(has_subset)
        self.lbl_poly.setText(polygon_text)
        self._update_hints()

    def _hint(self, cmb, li):
        d = cmb.currentData()
        zr = self.zranges.get(d[0]) if d else None
        if not zr:
            return ""
        lo, hi = zr
        if li.mode() == "depth":
            return f"existing surface: depth {-hi:,.2f} … {-lo:,.2f} m"
        return f"existing surface: Z {lo:,.2f} … {hi:,.2f} m"

    def _update_hints(self, *_):
        if hasattr(self, "d_hint"):
            self.d_hint.setText(self._hint(self.d_surface, self.d_level))
        if hasattr(self, "t_hint"):
            self.t_hint.setText(self._hint(self.t_surface, self.t_level))

    def _compute(self):
        if self.tabs.currentIndex() == 0:
            d = self.d_surface.currentData()
            if d is None:
                return self.show_error("Create or load a DEM / TIN of the existing surface first")
            region = next(k for k, rb in self.rb.items() if rb.isChecked())
            spec = {"surface": d[0], "region": region, "route": self.d_route.currentData(),
                    "half": self.d_half.value(), "mode": self.d_mode.currentData(), "level": self.d_level.z(),
                    "level_ref": self.d_level.mode(),
                    "overdredge": self.d_over.value(), "cut_n": self.d_cut_n.value(), "fill_n": self.d_fill_n.value(),
                    "bulk": self.d_bulk.value(), "shrink": self.d_shrink.value(),
                    "cell": self.d_cell.value()}
            kind = "dredge"
        else:
            d = self.t_surface.currentData()
            if d is None:
                return self.show_error("Create or load a DEM / TIN of the existing surface first")
            if self.t_route.currentData() is None:
                return self.show_error("Import a route first (File → Import Route…)")
            spec = {"surface": d[0], "route": self.t_route.currentData(), "bottom_width": self.t_width.value(),
                    "slope_n": self.t_slope.value(), "ref": self.t_ref.currentData(), "depth": self.t_dol.value(),
                    "smooth": self.t_smooth.value(), "level": self.t_level.z(), "level_ref": self.t_level.mode(),
                    "kp_step": self.t_kp.value() / 1000.0, "cell": self.t_cell.value(), "bulk": self.t_bulk.value(),
                    "shrink": 1.0}
            kind = "trench"
        self.lbl_msg.setText(f"<span style='color:{C['muted']}'>Computing…</span>")
        self._last_spec = spec
        self.cb.design_compute(kind, spec)

    def show_error(self, msg):
        self.lbl_msg.setText(f"<span style='color:{C['danger']}'>●  {msg}</span>")

    def show_result(self, kind, res, title):
        while self.res.count():
            w = self.res.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
        s = res["stats"]
        rows = []
        if kind == "dredge":
            rows.append(("Footprint area", f"{s['footprint_area']:,.1f} m²"))
            rows.append(("Cut (dredge / excavation)", f"{s['cut_volume']:,.2f} m³  over {s['cut_area']:,.1f} m²"))
            if "cut_volume_overdredge" in s:
                rows.append(("Cut incl. over-cut", f"{s['cut_volume_overdredge']:,.2f} m³  "
                                                      f"(+{s['cut_volume_overdredge'] - s['cut_volume']:,.2f})"))
            rows.append(("Fill", f"{s['fill_volume']:,.2f} m³  over {s['fill_area']:,.1f} m²"))
            rows.append(("Net (fill − cut)", f"{s['net_volume']:+,.2f} m³"))
            rows += earthwork_rows(s, getattr(self, "_last_spec", {}))
            rows.append(("Affected area (incl. slopes)", f"{s['affected_area']:,.1f} m²"))
            rows.append(("Max cut / max fill", f"{s['max_cut']:.2f} m / {s['max_fill']:.2f} m"))
        else:
            rows.append(("Route length", f"{s['length']:,.1f} m"))
            rows.append(("Excavation", f"{s['cut_volume']:,.2f} m³"))
            rows.append(("Mean cross-section", f"{s['mean_section']:.3f} m²  (nominal on a flat surface "
                                                f"{s['nominal_section']:.3f} m²)"))
            rows.append(("Max excavation depth", f"{s['max_cut']:.2f} m"))
            b = getattr(self, "_last_spec", {}).get("bulk", 1.0)
            if abs(b - 1.0) > 1e-9:
                rows.append(("Excavation, loose", f"{s['cut_volume'] * b:,.2f} m³  (× {b:g})"))
            rows.append(("Disturbed width area", f"{s['affected_area']:,.1f} m²"))
        if s.get("missing_in_footprint", 0) > 0:
            rows.append(("No surface data in footprint", f"{s['missing_in_footprint']:,.1f} m² — not included"))
        for r, (k, val) in enumerate(rows):
            kl, vl = _muted(k), QLabel(val)
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if k.startswith(("Cut", "Excavation")):
                vl.setStyleSheet("color:#6c9ce8; font-weight:600")
            elif k.startswith("Fill"):
                vl.setStyleSheet("color:#ef8a6b; font-weight:600")
            elif k.startswith("No surface"):
                vl.setStyleSheet("color:#ff9f43; font-weight:600")
            for lab in (kl, vl):
                lab.setMinimumHeight(lab.fontMetrics().height() + 4)
            self.res.addWidget(kl, r, 0)
            self.res.addWidget(vl, r, 1)
        self.hist.set_data(res["dz"])
        if kind == "trench":
            tab = res["table"]
            self.kp_table.setRowCount(len(tab))
            for i, (a, b, vol) in enumerate(tab):
                for j, txt in enumerate((f"{a:.3f}", f"{b:.3f}", f"{vol:,.1f}")):
                    it = QTableWidgetItem(txt)
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.kp_table.setItem(i, j, it)
            self.kp_table.resizeColumnsToContents()
            self.kp_table.show()
        else:
            self.kp_table.hide()
        self.lbl_msg.setText(f"<span style='color:{C['success']}'>●</span>  {title}")
        for b in (self.btn_layers, self.btn_csv, self.btn_tif, self.btn_dtif):
            b.setEnabled(True)
