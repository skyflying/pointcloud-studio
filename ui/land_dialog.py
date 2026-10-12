"""Land menu → terrain, change detection, stockpiles, road corridor, hydrology, clearance (non-modal).

cb = LandMixin: land_run(kind, spec), land_add_layers(), land_add_sections(), land_export_csv(),
land_export_gis(), land_export_tifs(), land_save_chart(), land_clear_overlay().
Results are generic: summary rows, tables, layers, features, optional chart (see core/terrain.py).
"""
from __future__ import annotations

import re

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QRadioButton, QScrollArea, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ui.cable_dialog import KPChart, _dsp, _fmt, _muted, _section
from ui.check_helpers import check_bar
from ui.seabed_dialog import EpochTable
from ui.theme import C, dark_titlebar


def _cmb():
    c = QComboBox()
    c.setMinimumWidth(240)
    return c


class LandDialog(QDialog):
    TABS = ("terrain", "change", "stockpile", "road", "hydro", "clearance")

    def __init__(self, cb, parent=None):
        super().__init__(parent)
        self.cb = cb
        self.res = None
        self.setWindowTitle("Land analysis")
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
        t = QLabel("Land analysis")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Elevations positive up. Areas (polygons) and centre lines can come from imported vector "
                           "layers (File → Import Vector Layer…) or routes."))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_terrain(), "Terrain")
        self.tabs.addTab(self._tab_change(), "Change")
        self.tabs.addTab(self._tab_stock(), "Stockpiles")
        self.tabs.addTab(self._tab_road(), "Road corridor")
        self.tabs.addTab(self._tab_hydro(), "Hydrology")
        self.tabs.addTab(self._tab_clear(), "Clearance")
        v.addWidget(self.tabs)
        row = QHBoxLayout()
        self.lbl_msg = QLabel(" ")
        self.lbl_msg.setWordWrap(True)
        row.addWidget(self.lbl_msg, 1)
        b = QPushButton("Run analysis")
        b.setProperty("primary", True)
        b.clicked.connect(self._run)
        row.addWidget(b)
        v.addLayout(row)
        v.addWidget(_section("Result"))
        self.lbl_sum = QLabel("Run an analysis to see the result.")
        self.lbl_sum.setWordWrap(True)
        self.lbl_sum.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_sum)
        self.chart = KPChart()
        self.chart.setVisible(False)
        v.addWidget(self.chart)
        self.cmb_table = QComboBox()
        self.cmb_table.currentIndexChanged.connect(self._fill_table)
        v.addWidget(self.cmb_table)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(220)
        v.addWidget(self.table)
        brow = QHBoxLayout()
        self.btns = {}
        for key, label, fn in (("layers", "Add layers", cb.land_add_layers), ("sections", "Add sections",
                                                                               cb.land_add_sections),
                               ("csv", "Export CSV…", cb.land_export_csv), ("gis", "Export GIS…", cb.land_export_gis),
                               ("tif", "GeoTIFFs…", cb.land_export_tifs), ("png", "Save chart…", cb.land_save_chart),
                               ("clear", "Clear 3D", cb.land_clear_overlay)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            b.setEnabled(False)
            self.btns[key] = b
            brow.addWidget(b)
        v.addLayout(brow)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    # ---------------------------------------------------------------- tabs
    def _tab_terrain(self):
        w = QWidget()
        f = QFormLayout(w)
        g = QButtonGroup(self)
        self.t_prod = QRadioButton("Terrain products from a surface")
        self.t_mod = QRadioButton("Surface models from a classified point cloud (DSM / DTM / nDSM / CHM)")
        self.t_prod.setChecked(True)
        for rb in (self.t_prod, self.t_mod):
            g.addButton(rb)
        f.addRow(self.t_prod)
        self.t_surf = _cmb()
        f.addRow("  Surface", self.t_surf)
        h = QHBoxLayout()
        self.t_chk = {}
        for k, lab in (("slope", "Slope"), ("aspect", "Aspect"), ("curvature", "Curvature"),
                       ("roughness", "Roughness")):
            c = QCheckBox(lab)
            c.setChecked(True)
            self.t_chk[k] = c
            h.addWidget(c)
        f.addRow("  Products", h)
        self.t_cell = _dsp(0.0, 0.0, 100.0, 0.5, 2)
        self.t_cell.setSpecialValueText("surface grid")
        f.addRow("  Cell", self.t_cell)
        f.addRow(self.t_mod)
        self.t_pc = _cmb()
        self.t_mcell = _dsp(1.0, 0.1, 50.0, 0.5, 2)
        self.t_canopy = _dsp(2.0, 0.0, 50.0, 0.5, 1)
        f.addRow("  Point cloud", self.t_pc)
        f.addRow("  Cell", self.t_mcell)
        f.addRow("  Canopy from", self.t_canopy)
        f.addRow(_muted("Needs ground (2), vegetation (3–5) and building (6) classes — Edit → Classify."))
        return w

    def _tab_change(self):
        w = QWidget()
        f = QFormLayout(w)
        g = QButtonGroup(self)
        self.c_dod = QRadioButton("DEM of difference with a level of detection (surfaces)")
        self.c_m3 = QRadioButton("M3C2 cloud-to-cloud distance (steep slopes, cliffs)")
        self.c_dod.setChecked(True)
        for rb in (self.c_dod, self.c_m3):
            g.addButton(rb)
        f.addRow(self.c_dod)
        self.c_a, self.c_b = _cmb(), _cmb()
        f.addRow("  Earlier surface (A)", self.c_a)
        f.addRow("  Later surface (B)", self.c_b)
        self.c_sa = _dsp(0.05, 0.0, 10.0, 0.01, 3)
        self.c_sb = _dsp(0.05, 0.0, 10.0, 0.01, 3)
        self.c_conf = QComboBox()
        for lab, t in (("68 % (t = 1)", 1.0), ("95 % (t = 1.96)", 1.96), ("99 % (t = 2.58)", 2.58)):
            self.c_conf.addItem(lab, t)
        self.c_conf.setCurrentIndex(1)
        self.c_cell = _dsp(0.0, 0.0, 100.0, 0.5, 2)
        self.c_cell.setSpecialValueText("coarser of the two")
        self.c_poly = _cmb()
        f.addRow("  Vertical accuracy A (σ)", self.c_sa)
        f.addRow("  Vertical accuracy B (σ)", self.c_sb)
        f.addRow("  Confidence", self.c_conf)
        f.addRow("  Cell", self.c_cell)
        f.addRow("  Areas (polygons)", self.c_poly)
        f.addRow(self.c_m3)
        self.c_p1, self.c_p2 = _cmb(), _cmb()
        f.addRow("  Earlier cloud", self.c_p1)
        f.addRow("  Later cloud", self.c_p2)
        self.c_sp = _dsp(1.0, 0.05, 50.0, 0.5, 2)
        self.c_ns = _dsp(2.0, 0.1, 50.0, 0.5, 2)
        self.c_cd = _dsp(1.0, 0.05, 50.0, 0.5, 2)
        self.c_md = _dsp(5.0, 0.1, 100.0, 1.0, 1)
        self.c_reg = _dsp(0.0, 0.0, 5.0, 0.01, 3)
        f.addRow("  Core point spacing", self.c_sp)
        f.addRow("  Normal scale", self.c_ns)
        f.addRow("  Cylinder diameter", self.c_cd)
        f.addRow("  Max. depth", self.c_md)
        f.addRow("  Registration error", self.c_reg)
        return w

    def _tab_stock(self):
        w = QWidget()
        f = QFormLayout(w)
        self.s_epochs = EpochTable(self.cb.seabed_set_date)
        f.addRow(_section("Surveys (one or more)"))
        f.addRow(check_bar(self.s_epochs, "Use:"))
        f.addRow(self.s_epochs)
        self.s_poly = _cmb()
        f.addRow("Stockpile boundaries", self.s_poly)
        self.s_base = QComboBox()
        for lab, k in (("Best-fit plane through the boundary", "plane"), ("Boundary triangulated", "tin"),
                       ("Lowest boundary point", "lowest"), ("Fixed level", "level")):
            self.s_base.addItem(lab, k)
        self.s_level = _dsp(0.0, -12000, 9000, 0.5, 3)
        self.s_cell = _dsp(0.0, 0.0, 20.0, 0.1, 2)
        self.s_cell.setSpecialValueText("surface grid")
        self.s_den = _dsp(0.0, 0.0, 10.0, 0.1, 2, " t/m³")
        self.s_den.setSpecialValueText("— (no tonnage)")
        f.addRow("Base", self.s_base)
        f.addRow("Fixed level", self.s_level)
        f.addRow("Cell", self.s_cell)
        f.addRow("Bulk density", self.s_den)
        return w

    def _tab_road(self):
        w = QWidget()
        f = QFormLayout(w)
        self.r_route, self.r_surf = _cmb(), _cmb()
        f.addRow("Centre line (route)", self.r_route)
        f.addRow("Existing ground", self.r_surf)
        self.r_lim = QCheckBox("Limit to KP")
        self.r_k0 = _dsp(0.0, -1e5, 1e5, 0.01, 3, " km")
        self.r_k1 = _dsp(0.1, -1e5, 1e5, 0.01, 3, " km")
        h = QHBoxLayout()
        for x in (self.r_lim, self.r_k0, QLabel("–"), self.r_k1):
            h.addWidget(x)
        f.addRow(h)
        g = QButtonGroup(self)
        self.r_g = QRadioButton("Grade: existing ground smoothed")
        self.r_p = QRadioButton("Grade: PVI points (KP : level; …)")
        self.r_g.setChecked(True)
        for rb in (self.r_g, self.r_p):
            g.addButton(rb)
        f.addRow(self.r_g)
        self.r_smooth = _dsp(50.0, 1.0, 5000.0, 10.0, 0)
        self.r_off = _dsp(0.0, -100.0, 100.0, 0.1, 2)
        f.addRow("  Smoothing length", self.r_smooth)
        f.addRow("  Offset", self.r_off)
        f.addRow(self.r_p)
        self.r_pvi = QLineEdit()
        self.r_pvi.setPlaceholderText("e.g.  0.000 : 12.50;  0.150 : 14.20;  0.400 : 13.80")
        f.addRow("  PVI", self.r_pvi)
        self.r_w = _dsp(4.0, 0.5, 100.0, 0.5, 2)
        self.r_cf = _dsp(2.5, -10.0, 10.0, 0.5, 2, " %")
        self.r_cut = _dsp(1.5, 0.1, 20.0, 0.5, 2, "")
        self.r_cut.setPrefix("1 : ")
        self.r_fill = _dsp(2.0, 0.1, 20.0, 0.5, 2, "")
        self.r_fill.setPrefix("1 : ")
        self.r_reach = _dsp(40.0, 1.0, 500.0, 5.0, 0)
        self.r_cell = _dsp(0.5, 0.05, 10.0, 0.25, 2)
        self.r_bin = _dsp(20.0, 1.0, 1000.0, 5.0, 0)
        self.r_cf2 = _dsp(0.9, 0.1, 2.0, 0.05, 2, "")
        self.r_sec = _dsp(20.0, 0.0, 1000.0, 5.0, 0)
        for lab, wd in (("Formation half-width", self.r_w), ("Crossfall (from centre)", self.r_cf),
                        ("Cut slope", self.r_cut), ("Fill slope", self.r_fill), ("Corridor beyond formation", self.r_reach),
                        ("Grid cell", self.r_cell), ("Volume interval", self.r_bin),
                        ("Cut → fill factor", self.r_cf2), ("Cross-sections every", self.r_sec)):
            f.addRow(lab, wd)
        return w

    def _tab_hydro(self):
        w = QWidget()
        f = QFormLayout(w)
        self.h_surf = _cmb()
        self.h_cell = _dsp(0.0, 0.0, 100.0, 0.5, 2)
        self.h_cell.setSpecialValueText("surface grid (≤ 4 M cells)")
        self.h_area = _dsp(10000.0, 1.0, 1e9, 1000.0, 0, " m²")
        f.addRow("Surface (DTM)", self.h_surf)
        f.addRow("Cell", self.h_cell)
        f.addRow("Stream from contributing area", self.h_area)
        self.h_ws = QCheckBox("Watershed above an outlet")
        self.h_ox, self.h_oy = QLineEdit(), QLineEdit()
        self.h_ox.setPlaceholderText("Easting")
        self.h_oy.setPlaceholderText("Northing")
        self.h_opts = _cmb()
        self.h_opts.setToolTip("Or take the outlet from a point layer")
        self.h_snap = _dsp(20.0, 0.0, 1000.0, 5.0, 0)
        h = QHBoxLayout()
        h.addWidget(self.h_ox)
        h.addWidget(self.h_oy)
        f.addRow(self.h_ws)
        f.addRow("  Outlet X / Y", h)
        f.addRow("  or point", self.h_opts)
        f.addRow("  Snap to flow within", self.h_snap)
        self.h_in = QCheckBox("Inundation below a water level")
        self.h_lev = _dsp(0.0, -12000, 9000, 0.1, 2)
        self.h_sx, self.h_sy = QLineEdit(), QLineEdit()
        self.h_sx.setPlaceholderText("seed Easting (optional)")
        self.h_sy.setPlaceholderText("seed Northing")
        h2 = QHBoxLayout()
        h2.addWidget(self.h_sx)
        h2.addWidget(self.h_sy)
        f.addRow(self.h_in)
        f.addRow("  Water level", self.h_lev)
        f.addRow("  Connected to", h2)
        f.addRow(_muted("Depressions are filled first so every cell drains to the edge of the data (D8 flow)."))
        return w

    def _tab_clear(self):
        w = QWidget()
        f = QFormLayout(w)
        self.k_pc = _cmb()
        self.k_cls = QLineEdit("3, 4, 5, 6")
        self.k_cls.setToolTip("LAS classes to check (vegetation 3–5, buildings 6); empty = all points")
        self.k_line = _cmb()
        self.k_line.setToolTip("3D line: route with cable-top Z, or a 3D line of a vector layer")
        self.k_hag = QCheckBox("2D line: height above ground")
        self.k_h = _dsp(10.0, 0.0, 500.0, 1.0, 2)
        self.k_surf = _cmb()
        self.k_lim = _dsp(5.0, 0.1, 200.0, 0.5, 2)
        self.k_bin = _dsp(5.0, 0.5, 500.0, 1.0, 1)
        f.addRow("Point cloud", self.k_pc)
        f.addRow("Classes", self.k_cls)
        f.addRow("Line", self.k_line)
        f.addRow(self.k_hag)
        f.addRow("  Height", self.k_h)
        f.addRow("  Ground surface", self.k_surf)
        f.addRow("Minimum clearance", self.k_lim)
        f.addRow("Grouping interval", self.k_bin)
        return w

    # ---------------------------------------------------------------- context
    def set_context(self, ctx):
        """ctx: surfaces/clouds/routes/polys/points/lines3d: [(id, name)], epochs: [(id, name, date)]"""
        self._ctx = ctx

        def fill(cmb, items, none=None):
            cur = cmb.currentData()
            cmb.blockSignals(True)
            cmb.clear()
            if none:
                cmb.addItem(none, None)
            for i, n in items:
                cmb.addItem(n, i)
            j = cmb.findData(cur)
            cmb.setCurrentIndex(j if j >= 0 else 0)
            cmb.blockSignals(False)

        S, P = ctx["surfaces"], ctx["clouds"]
        for cmb in (self.t_surf, self.c_a, self.r_surf, self.h_surf):
            fill(cmb, S)
        fill(self.c_b, S)
        if self.c_b.count() > 1 and self.c_b.currentIndex() == self.c_a.currentIndex():
            self.c_b.setCurrentIndex(1)
        for cmb in (self.t_pc, self.c_p1, self.k_pc):
            fill(cmb, P)
        fill(self.c_p2, P)
        if self.c_p2.count() > 1 and self.c_p2.currentIndex() == self.c_p1.currentIndex():
            self.c_p2.setCurrentIndex(1)
        fill(self.c_poly, ctx["polys"], "(whole overlap only)")
        fill(self.s_poly, ctx["polys"])
        fill(self.r_route, ctx["routes"])
        fill(self.h_opts, ctx["points"], "(coordinates)")
        fill(self.k_line, ctx["lines"])
        fill(self.k_surf, S)
        self.s_epochs.fill(ctx["epochs"])

    # ---------------------------------------------------------------- run
    def show_error(self, m):
        self.lbl_msg.setText(f"<span style='color:{C['danger']}'>●  {m}</span>")

    @staticmethod
    def _xy(a, b):
        try:
            return float(a.text().replace(",", "")), float(b.text().replace(",", ""))
        except ValueError:
            return None

    def _run(self):
        self.lbl_msg.setText(" ")
        kind = self.TABS[self.tabs.currentIndex()]
        if kind == "terrain":
            if self.t_prod.isChecked():
                if self.t_surf.currentData() is None:
                    return self.show_error("No surface — import or build a DEM / TIN")
                prods = [k for k, c in self.t_chk.items() if c.isChecked()]
                if not prods:
                    return self.show_error("Tick at least one product")
                spec = {"mode": "products", "surface": self.t_surf.currentData(), "cell": self.t_cell.value() or None,
                        "products": prods}
            else:
                if self.t_pc.currentData() is None:
                    return self.show_error("No point cloud")
                spec = {"mode": "models", "pc": self.t_pc.currentData(), "cell": self.t_mcell.value(),
                        "canopy": self.t_canopy.value()}
        elif kind == "change":
            if self.c_dod.isChecked():
                a, b = self.c_a.currentData(), self.c_b.currentData()
                if a is None or b is None or a == b:
                    return self.show_error("Choose two different surfaces")
                spec = {"mode": "dod", "a": a, "b": b, "sa": self.c_sa.value(), "sb": self.c_sb.value(),
                        "t": self.c_conf.currentData(), "cell": self.c_cell.value() or None,
                        "polys": self.c_poly.currentData()}
            else:
                a, b = self.c_p1.currentData(), self.c_p2.currentData()
                if a is None or b is None or a == b:
                    return self.show_error("Choose two different point clouds")
                spec = {"mode": "m3c2", "a": a, "b": b, "spacing": self.c_sp.value(), "normal": self.c_ns.value(),
                        "diam": self.c_cd.value(), "depth": self.c_md.value(), "reg": self.c_reg.value()}
        elif kind == "stockpile":
            eps = self.s_epochs.chosen()
            if not eps:
                return self.show_error("Tick at least one survey")
            if self.s_poly.currentData() is None:
                return self.show_error("Import the stockpile boundaries as a vector layer (polygons)")
            spec = {"epochs": eps, "polys": self.s_poly.currentData(), "base": self.s_base.currentData(),
                    "level": self.s_level.value(), "cell": self.s_cell.value() or None,
                    "density": self.s_den.value() or None}
        elif kind == "road":
            if self.r_route.currentData() is None or self.r_surf.currentData() is None:
                return self.show_error("Choose the centre line and the ground surface")
            pvi = []
            if self.r_p.isChecked():
                for part in re.split(r"[;\n]+", self.r_pvi.text()):
                    if part.strip():
                        try:
                            k, z = (float(x) for x in part.split(":"))
                        except ValueError:
                            return self.show_error("PVI: pairs of  KP : level  separated by ';'")
                        pvi.append((k, z))
                if len(pvi) < 2:
                    return self.show_error("Give at least two PVI points")
            spec = {"route": self.r_route.currentData(), "surface": self.r_surf.currentData(),
                    "kp": (self.r_k0.value(), self.r_k1.value()) if self.r_lim.isChecked() else None,
                    "grade": "pvi" if self.r_p.isChecked() else "ground", "pvi": pvi, "smooth": self.r_smooth.value(),
                    "offset": self.r_off.value(), "w": self.r_w.value(), "cf": self.r_cf.value(),
                    "cut": self.r_cut.value(), "fill": self.r_fill.value(), "reach": self.r_reach.value(),
                    "cell": self.r_cell.value(), "bin": self.r_bin.value(), "c2f": self.r_cf2.value(),
                    "sec": self.r_sec.value()}
        elif kind == "hydro":
            if self.h_surf.currentData() is None:
                return self.show_error("No surface")
            outlet = None
            if self.h_ws.isChecked():
                outlet = self._xy(self.h_ox, self.h_oy) if self.h_opts.currentData() is None \
                    else ("point", self.h_opts.currentData())
                if outlet is None:
                    return self.show_error("Outlet: enter Easting and Northing, or choose a point")
            seed = self._xy(self.h_sx, self.h_sy) if self.h_in.isChecked() and self.h_sx.text().strip() else None
            spec = {"surface": self.h_surf.currentData(), "cell": self.h_cell.value() or None,
                    "area": self.h_area.value(), "outlet": outlet, "snap": self.h_snap.value(),
                    "level": self.h_lev.value() if self.h_in.isChecked() else None, "seed": seed}
        else:
            if self.k_pc.currentData() is None or self.k_line.currentData() is None:
                return self.show_error("Choose the point cloud and the line")
            try:
                cls = tuple(int(x) for x in re.split(r"[,\s;]+", self.k_cls.text().strip()) if x)
            except ValueError:
                return self.show_error("Classes: numbers separated by commas")
            if self.k_hag.isChecked() and self.k_surf.currentData() is None:
                return self.show_error("Height above ground needs the ground surface")
            spec = {"pc": self.k_pc.currentData(), "classes": cls, "line": self.k_line.currentData(),
                    "hag": self.k_h.value() if self.k_hag.isChecked() else None, "surface": self.k_surf.currentData(),
                    "limit": self.k_lim.value(), "bin": self.k_bin.value()}
        self.cb.land_run(kind, spec)

    # ---------------------------------------------------------------- result
    def show_result(self, res):
        self.res = res
        self.lbl_sum.setText(f"<b>{res['title']}</b><br>" + "<br>".join(
            f"<span style='color:{C['muted']}'>{a}:</span> {b}" for a, b in res["summary"]))
        ch = res.get("chart")
        self.chart.setVisible(bool(ch))
        if ch:
            self.chart.setMinimumHeight(170 * len(ch["panels"]) + 30)
            self.chart.set_data(ch["x"], ch["panels"], xlabel=ch.get("xlabel"))
        self.cmb_table.blockSignals(True)
        self.cmb_table.clear()
        for k, rows in res["tables"].items():
            if rows:
                self.cmb_table.addItem(f"{k} ({len(rows):,})", k)
        self.cmb_table.blockSignals(False)
        self._fill_table()
        self.btns["layers"].setEnabled(bool(res["layers"]))
        self.btns["tif"].setEnabled(bool(res["layers"]))
        self.btns["sections"].setEnabled(bool(res.get("sections")))
        self.btns["gis"].setEnabled(bool(res["features"]))
        self.btns["png"].setEnabled(bool(ch))
        self.btns["csv"].setEnabled(any(res["tables"].values()))
        self.btns["clear"].setEnabled(True)

    def _fill_table(self, *_):
        self.table.clear()
        if self.res is None or self.cmb_table.currentData() is None:
            return
        rows = self.res["tables"][self.cmb_table.currentData()][:5000]
        cols = list(dict.fromkeys(c for r in rows for c in r))
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels([c.replace("_", " ") for c in cols])
        for i, r in enumerate(rows):
            for j, c in enumerate(cols):
                v = r.get(c, "")
                txt = ("yes" if v else "no") if isinstance(v, (bool, np.bool_)) else \
                    (_fmt(v, 3 if c.startswith(("kp", "x", "y", "z")) else 2) if isinstance(v, float) else str(v))
                it = QTableWidgetItem(txt)
                if isinstance(v, float):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
