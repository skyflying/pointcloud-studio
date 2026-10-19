"""Mesh / model dialogs: point cloud → mesh, point cloud → geometric models, mesh information."""
from __future__ import annotations

import csv

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.mesh import METHODS, open3d_available
from ui.theme import dark_titlebar


def _muted(t):
    l = QLabel(t)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


def _dsp(v, lo, hi, step, dec=2, suffix=" m", special=None):
    s = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=dec, singleStep=step)
    s.setValue(v)
    if suffix:
        s.setSuffix(suffix)
    if special:
        s.setSpecialValueText(special)
    return s


def _isp(v, lo, hi, suffix=""):
    s = QSpinBox(minimum=lo, maximum=hi)
    s.setValue(v)
    if suffix:
        s.setSuffix(suffix)
    return s


class _SourceMixin:
    def _source_box(self, counts):
        g = QGroupBox("Source points")
        f = QFormLayout(g)
        self.cmb_src = QComboBox()
        self.cmb_src.addItem(f"All active points ({counts.get('active', 0):,})", "all")
        if counts.get("selected"):
            self.cmb_src.addItem(f"Selected points ({counts['selected']:,})", "selected")
            self.cmb_src.setCurrentIndex(1)
        f.addRow("Points", self.cmb_src)
        self.ed_cls = QLineEdit()
        self.ed_cls.setPlaceholderText("all classes — or e.g. 2  /  6  /  1, 2")
        if not counts.get("classes"):
            self.ed_cls.setEnabled(False)
            self.ed_cls.setPlaceholderText("no classification in this cloud")
        else:
            self.ed_cls.setToolTip("Classes present: " + ", ".join(f"{k} ({v:,})" for k, v in counts["classes"].items()))
        f.addRow("Classes", self.ed_cls)
        return g

    def classes(self):
        t = self.ed_cls.text().strip() if self.ed_cls.isEnabled() else ""
        return [int(x) for x in t.replace(";", ",").split(",") if x.strip()] if t else []

    def source(self):
        return self.cmb_src.currentData()


class MeshDialog(QDialog, _SourceMixin):
    def __init__(self, counts, spacing, has_rgb, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mesh from point cloud")
        self.resize(560, 640)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 14, 20, 14)
        t = QLabel("Mesh from point cloud")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Works with any point cloud (LiDAR, MBES, survey, photogrammetry). 2.5D Delaunay suits "
                           "terrain and seabed; 3D methods suit objects, structures, walls and overhangs."))
        v.addWidget(self._source_box(counts))
        g = QGroupBox("Method")
        f = QFormLayout(g)
        self.cmb = QComboBox()
        o3d = open3d_available()
        for k, lab in METHODS.items():
            need = k in ("poisson", "ballpivot")
            self.cmb.addItem(lab + ("" if o3d or not need else "  — needs Open3D"), k)
        f.addRow("Method", self.cmb)
        self.stack = QStackedWidget()
        sp = max(spacing, 1e-3)
        # 2.5D
        w = QWidget()
        ff = QFormLayout(w)
        ff.setContentsMargins(0, 0, 0, 0)
        self.d_edge = _dsp(round(sp * 6, 3), 0, 1e5, 0.1, 3, " m", "no limit")
        self.d_thin = _dsp(0, 0, 1e4, 0.05, 3, " m", "off")
        ff.addRow("Max. edge length", self.d_edge)
        ff.addRow("Thin to one point per", self.d_thin)
        self.stack.addWidget(w)
        # alpha
        w = QWidget()
        ff = QFormLayout(w)
        ff.setContentsMargins(0, 0, 0, 0)
        self.a_alpha = _dsp(0, 0, 1e5, 0.05, 3, " m", "automatic")
        self.a_cell = _dsp(0, 0, 1e4, 0.01, 3, " m", "automatic")
        self.a_fill = QCheckBox("Fill cavities (one closed outer surface)", checked=True)
        ff.addRow("Alpha radius", self.a_alpha)
        ff.addRow("Voxel thinning", self.a_cell)
        ff.addRow(self.a_fill)
        ff.addRow(_muted("For objects and structures. Open terrain / seabed gives a thin slab here — use 2.5D "
                         "Delaunay for those."))
        self.stack.addWidget(w)
        # poisson
        w = QWidget()
        ff = QFormLayout(w)
        ff.setContentsMargins(0, 0, 0, 0)
        self.p_depth = _isp(9, 5, 13)
        self.p_trim = _dsp(5, 0, 50, 1, 1, " %")
        self.p_cell = _dsp(0, 0, 1e4, 0.01, 3, " m", "off")
        self.p_norm = QComboBox()
        for k, lab in (("up", "Upward (terrain, seabed)"), ("out", "Outward from the centre (objects)"),
                       ("consistent", "Consistent propagation (slower)")):
            self.p_norm.addItem(lab, k)
        ff.addRow("Octree depth", self.p_depth)
        ff.addRow("Trim low-density", self.p_trim)
        ff.addRow("Voxel thinning", self.p_cell)
        ff.addRow("Normal direction", self.p_norm)
        self.stack.addWidget(w)
        # ball pivot
        w = QWidget()
        ff = QFormLayout(w)
        ff.setContentsMargins(0, 0, 0, 0)
        self.b_cell = _dsp(0, 0, 1e4, 0.01, 3, " m", "off")
        ff.addRow("Voxel thinning", self.b_cell)
        ff.addRow(_muted("Radii are 1.5×, 3× and 6× the point spacing."))
        self.stack.addWidget(w)
        f.addRow(self.stack)
        self.cmb.currentIndexChanged.connect(self.stack.setCurrentIndex)
        v.addWidget(g)
        g = QGroupBox("Post-processing")
        f = QFormLayout(g)
        self.sp_smooth = _isp(0, 0, 50, " iterations")
        self.sp_small = _dsp(0, 0, 1e7, 1, 2, " m²", "keep all")
        self.sp_dec = _dsp(0, 0, 1e4, 0.05, 3, " m", "off")
        self.chk_rgb = QCheckBox("Colours from the points (RGB)", checked=has_rgb)
        self.chk_rgb.setEnabled(has_rgb)
        f.addRow("Smoothing (Taubin)", self.sp_smooth)
        f.addRow("Remove pieces smaller than", self.sp_small)
        f.addRow("Simplify to cells of", self.sp_dec)
        f.addRow(self.chk_rgb)
        v.addWidget(g)
        v.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Build Mesh")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _accept(self):
        if self.cmb.currentData() in ("poisson", "ballpivot") and not open3d_available():
            from ui.deps_dialog import missing_module_box
            missing_module_box("open3d", self)
            return
        try:
            self.classes()
        except ValueError:
            QMessageBox.warning(self, "Mesh", "Classes must be numbers separated by commas.")
            return
        self.accept()

    def params(self):
        m = self.cmb.currentData()
        kw = {}
        if m == "delaunay25d":
            kw = dict(max_edge=self.d_edge.value(), thin_cell=self.d_thin.value())
        elif m == "alpha3d":
            kw = dict(alpha=self.a_alpha.value(), cell=self.a_cell.value(), fill=self.a_fill.isChecked())
        elif m == "poisson":
            kw = dict(depth=self.p_depth.value(), trim=self.p_trim.value() / 100, cell=self.p_cell.value(),
                      normals=self.p_norm.currentData())
        else:
            kw = dict(cell=self.b_cell.value())
        return dict(method=m, smooth=self.sp_smooth.value(), min_component=self.sp_small.value(),
                    decimate=self.sp_dec.value(), use_rgb=self.chk_rgb.isChecked(), **kw)


MODEL_KINDS = {"planes": "Planes (walls, roofs, slabs, rock faces, revetments)",
               "cylinders": "Cylinders (piles, monopiles, poles, columns, pipes, trunks)",
               "buildings": "Buildings (LOD1 blocks / LOD2 roof shapes) from classified points"}


class ModelDialog(QDialog, _SourceMixin):
    def __init__(self, counts, spacing, parent=None, kind="planes"):
        super().__init__(parent)
        self.setWindowTitle("Model point cloud")
        self.resize(560, 620)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 14, 20, 14)
        t = QLabel("Model the point cloud")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Fits geometric models to the points and reports their parameters. Select or crop the "
                           "object first for faster, cleaner results."))
        v.addWidget(self._source_box(counts))
        g = QGroupBox("Model")
        f = QFormLayout(g)
        self.cmb = QComboBox()
        for k, lab in MODEL_KINDS.items():
            self.cmb.addItem(lab, k)
        f.addRow("Type", self.cmb)
        self.stack = QStackedWidget()
        sp = max(spacing, 1e-3)
        w = QWidget()
        ff = QFormLayout(w)
        ff.setContentsMargins(0, 0, 0, 0)
        self.pl_thr = _dsp(max(round(sp * 1.5, 3), 0.01), 0.0005, 100, 0.005, 3)
        self.pl_min = _isp(200, 10, 10_000_000, " pts")
        self.pl_max = _isp(30, 1, 500)
        self.pl_gap = _dsp(max(round(sp * 4, 2), 0.05), 0.001, 1000, 0.05, 3)
        self.pl_area = _dsp(0, 0, 1e7, 0.5, 1, " m²", "any")
        self.pl_ori = QComboBox()
        for k, lab in (("any", "Any"), ("horizontal", "Horizontal (≤ 15°)"), ("vertical", "Vertical (≥ 75°)"),
                       ("inclined", "Inclined (15°–75°)")):
            self.pl_ori.addItem(lab, k)
        ff.addRow("Distance tolerance", self.pl_thr)
        ff.addRow("Min. points per plane", self.pl_min)
        ff.addRow("Max. planes", self.pl_max)
        ff.addRow("Split segments at gaps >", self.pl_gap)
        ff.addRow("Min. area", self.pl_area)
        ff.addRow("Orientation", self.pl_ori)
        self.stack.addWidget(w)
        w = QWidget()
        ff = QFormLayout(w)
        ff.setContentsMargins(0, 0, 0, 0)
        self.cy_axis = QComboBox()
        self.cy_axis.addItem("Near vertical (piles, poles, columns)", "vertical")
        self.cy_axis.addItem("Any direction (pipes, inclined members)", "any")
        self.cy_thr = _dsp(max(round(sp * 1.5, 3), 0.01), 0.0005, 100, 0.005, 3)
        self.cy_rmin = _dsp(0.05, 0.001, 1000, 0.05, 3)
        self.cy_rmax = _dsp(5.0, 0.01, 1000, 0.5, 3)
        self.cy_min = _isp(150, 10, 10_000_000, " pts")
        self.cy_max = _isp(20, 1, 500)
        self.cy_tilt = _dsp(10, 0, 89, 1, 1, " °")
        ff.addRow("Axis", self.cy_axis)
        ff.addRow("Distance tolerance", self.cy_thr)
        ff.addRow("Radius from", self.cy_rmin)
        ff.addRow("Radius to", self.cy_rmax)
        ff.addRow("Min. points", self.cy_min)
        ff.addRow("Max. cylinders", self.cy_max)
        ff.addRow("Max. tilt (vertical)", self.cy_tilt)
        self.stack.addWidget(w)
        w = QWidget()
        ff = QFormLayout(w)
        ff.setContentsMargins(0, 0, 0, 0)
        self.bd_lod = QComboBox()
        self.bd_lod.addItem("LOD2 — roof shape from roof planes", 2)
        self.bd_lod.addItem("LOD1 — block with one roof height", 1)
        self.bd_bcls = _isp(6, 0, 255)
        self.bd_gcls = _isp(2, 0, 255)
        self.bd_cell = _dsp(0.5, 0.05, 20, 0.1, 2)
        self.bd_area = _dsp(20, 0, 1e6, 5, 1, " m²")
        self.bd_simp = _dsp(0.75, 0, 50, 0.25, 2)
        self.bd_roof = QComboBox()
        for k, lab in (("median", "Median"), ("p90", "90th percentile"), ("max", "Maximum")):
            self.bd_roof.addItem(lab, k)
        ff.addRow("Model", self.bd_lod)
        ff.addRow("Building class", self.bd_bcls)
        ff.addRow("Ground class", self.bd_gcls)
        ff.addRow("Footprint grid", self.bd_cell)
        ff.addRow("Min. footprint", self.bd_area)
        ff.addRow("Outline simplification", self.bd_simp)
        ff.addRow("LOD1 roof height", self.bd_roof)
        ff.addRow(_muted("Needs classified points — Edit → Classify Points gives ground (2) and buildings (6). "
                         "Footprints are also added as a polygon layer."))
        self.stack.addWidget(w)
        f.addRow(self.stack)
        self.cmb.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.cmb.setCurrentIndex(list(MODEL_KINDS).index(kind) if kind in MODEL_KINDS else 0)
        v.addWidget(g)
        v.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Build Models")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _accept(self):
        try:
            self.classes()
        except ValueError:
            QMessageBox.warning(self, "Model", "Classes must be numbers separated by commas.")
            return
        if self.cy_rmin.value() >= self.cy_rmax.value() and self.cmb.currentData() == "cylinders":
            QMessageBox.warning(self, "Model", "The radius range is empty.")
            return
        self.accept()

    def params(self):
        k = self.cmb.currentData()
        if k == "planes":
            return k, dict(thr=self.pl_thr.value(), min_points=self.pl_min.value(), max_planes=self.pl_max.value(),
                           spacing=self.pl_gap.value(), min_area=self.pl_area.value(),
                           orientation=self.pl_ori.currentData())
        if k == "cylinders":
            return k, dict(thr=self.cy_thr.value(), r_min=self.cy_rmin.value(), r_max=self.cy_rmax.value(),
                           min_points=self.cy_min.value(), max_count=self.cy_max.value(),
                           axis=self.cy_axis.currentData(), max_tilt=self.cy_tilt.value())
        return k, dict(lod=self.bd_lod.currentData(), building_class=self.bd_bcls.value(),
                       ground_class=self.bd_gcls.value(), cell=self.bd_cell.value(), min_area=self.bd_area.value(),
                       simplify=self.bd_simp.value(), roof_stat=self.bd_roof.currentData())


TABLE_COLUMNS = {
    "planes": ["id", "points", "area_m2", "dip_deg", "dip_dir_deg", "rms", "z_min", "z_max", "centre", "normal"],
    "cylinders": ["id", "points", "diameter", "radius", "length", "tilt_deg", "rms", "base", "top", "axis"],
    "buildings": ["id", "model", "footprint_m2", "ground", "eave", "top", "height", "roof_planes", "volume_m3",
                  "points", "centroid"],
}


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.3f}"
    if isinstance(v, (list, tuple)):
        return ", ".join(f"{x:.3f}" if isinstance(x, float) else str(x) for x in v)
    return str(v)


def write_table_csv(path, rows, cols):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        head = []
        for c in cols:
            if rows and isinstance(rows[0].get(c), (list, tuple)):
                n = len(rows[0][c])
                head += [f"{c}_{'xyz'[i] if n == 3 else i}" for i in range(n)]
            else:
                head.append(c)
        w.writerow(head)
        for r in rows:
            out = []
            for c in cols:
                v = r.get(c)
                if isinstance(v, (list, tuple)):
                    out += [f"{x:.4f}" if isinstance(x, float) else x for x in v]
                else:
                    out.append(f"{v:.4f}" if isinstance(v, float) else v)
            w.writerow(out)


class MeshInfoDialog(QDialog):
    def __init__(self, name, mesh, on_export, on_zoom=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(name)
        self.resize(820, 520)
        self.mesh = mesh
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        t = QLabel(name)
        t.setObjectName("title")
        v.addWidget(t)
        st = mesh.stats()
        meta = mesh.meta
        lines = [f"{st['vertices']:,} vertices · {st['triangles']:,} triangles · area {st['area_m2']:,.2f} m²"
                 + (f" · closed, volume {st['volume_m3']:,.2f} m³" if st["closed"] else " · open surface")]
        lo, hi = st["bbox_min"], st["bbox_max"]
        lines.append(f"Extent X {lo[0]:,.3f} – {hi[0]:,.3f} · Y {lo[1]:,.3f} – {hi[1]:,.3f} · "
                     f"Z {lo[2]:,.3f} – {hi[2]:,.3f}")
        info = {k: v_ for k, v_ in meta.items() if k not in ("table", "kind") and not isinstance(v_, (list, dict))}
        if info:
            lines.append(" · ".join(f"{k}: {_fmt(v_)}" for k, v_ in info.items()))
        v.addWidget(_muted("\n".join(lines)))
        self.rows = meta.get("table") or []
        self.kind = meta.get("kind")
        self.cols = TABLE_COLUMNS.get(self.kind, sorted(self.rows[0]) if self.rows else [])
        if self.rows:
            tbl = QTableWidget(len(self.rows), len(self.cols))
            tbl.setHorizontalHeaderLabels(self.cols)
            for r, row in enumerate(self.rows):
                for c, k in enumerate(self.cols):
                    tbl.setItem(r, c, QTableWidgetItem(_fmt(row.get(k, ""))))
            tbl.resizeColumnsToContents()
            if on_zoom is not None:
                tbl.cellDoubleClicked.connect(lambda r, _c: on_zoom(self.rows[r]))
            v.addWidget(tbl, 1)
            if on_zoom is not None:
                v.addWidget(_muted("Double-click a row to zoom to it."))
        else:
            v.addStretch(1)
        row = QHBoxLayout()
        if self.rows:
            b = QPushButton("Export Table (CSV)…")
            b.clicked.connect(self._export_csv)
            row.addWidget(b)
        b = QPushButton("Export Mesh…")
        b.clicked.connect(on_export)
        row.addWidget(b)
        row.addStretch(1)
        b = QPushButton("Close")
        b.clicked.connect(self.accept)
        row.addWidget(b)
        v.addLayout(row)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _export_csv(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export table", f"{self.kind or 'models'}.csv", "CSV (*.csv)")
        if p:
            write_table_csv(p, self.rows, self.cols)
