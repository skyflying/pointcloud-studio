"""Analysis → IHO S-44 Check: compliance of soundings with an IHO S-44 order (a-priori TPU, a-posteriori
residuals, crosslines, coverage) — report, failing-sounding selection, map layers."""
from __future__ import annotations

import csv
import os

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.s44 import EDITION, ORDER_LABEL, ORDERS
from ui.theme import dark_titlebar


def _muted(t):
    l = QLabel(t)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


class S44Dialog(QDialog):
    def __init__(self, clouds, surfaces, bound_id, has_sel, attrs_of, spacing, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IHO S-44 check")
        self.resize(620, 780)
        self.attrs_of = attrs_of
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        t = QLabel("IHO S-44 check")
        t.setObjectName("title")
        outer.addWidget(t)
        outer.addWidget(_muted(f"{EDITION}. TVU = √(a² + (b·d)²), THU = c + p·d (95 %). The results support the "
                               "surveyor's assessment; they do not certify a survey."))
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)
        sc.setWidget(body)
        outer.addWidget(sc, 1)
        g = QGroupBox("Soundings")
        f = QFormLayout(g)
        self.cmb_src = QComboBox()
        for cid, name in clouds:
            self.cmb_src.addItem(name, cid)
        i = self.cmb_src.findData(bound_id)
        self.cmb_src.setCurrentIndex(max(i, 0))
        f.addRow("Point cloud", self.cmb_src)
        self.cmb_scope = QComboBox()
        self.cmb_scope.addItem("All active points", "all")
        if has_sel:
            self.cmb_scope.addItem("Selected points (edit target)", "selected")
        f.addRow("Scope", self.cmb_scope)
        self.cmb_order = QComboBox()
        for k, t_ in ORDER_LABEL.items():
            self.cmb_order.addItem(t_, k)
        self.cmb_order.setCurrentIndex(2)
        f.addRow("Target order", self.cmb_order)
        v.addWidget(g)
        g = QGroupBox("A-priori: the soundings' own TPU")
        f = QFormLayout(g)
        self.chk_tpu = QCheckBox("Check TVU / THU against the limits")
        self.chk_tpu.setChecked(True)
        f.addRow(self.chk_tpu)
        from ui.uncertainty_widget import UncertaintyWidget
        self.unc = UncertaintyWidget(thu=True)
        f.addRow(self.unc)
        v.addWidget(g)
        g = QGroupBox("A-posteriori: residuals to a reference")
        f = QFormLayout(g)
        self.cmb_ref = QComboBox()
        self.cmb_ref.addItem("Local median surface of the soundings", None)
        for sid, name in surfaces:
            self.cmb_ref.addItem(f"Surface: {name}", sid)
        f.addRow("Reference", self.cmb_ref)
        self.sp_cell = QDoubleSpinBox(minimum=0.05, maximum=1000, decimals=2, singleStep=0.25)
        self.sp_cell.setValue(max(round(spacing * 3, 2), 0.1))
        self.sp_cell.setSuffix(" m")
        f.addRow("Median cell", self.sp_cell)
        v.addWidget(g)
        g = QGroupBox("Crosslines (independent check)")
        f = QFormLayout(g)
        self.cmb_cross = QComboBox()
        self.cmb_cross.addItem("(none)", None)
        for cid, name in clouds:
            self.cmb_cross.addItem(name, cid)
        f.addRow("Crossline cloud", self.cmb_cross)
        f.addRow(_muted("Differences crossline − main-scheme median surface (same cell size)."))
        v.addWidget(g)
        g = QGroupBox("Coverage")
        f = QFormLayout(g)
        self.sp_cov = QDoubleSpinBox(minimum=0.0, maximum=1000, decimals=2, singleStep=0.5)
        self.sp_cov.setSpecialValueText("feature size of the order")
        self.sp_cov.setSuffix(" m")
        self.sp_covn = QSpinBox(minimum=1, maximum=100, value=1)
        f.addRow("Grid cell", self.sp_cov)
        f.addRow("Soundings per cell ≥", self.sp_covn)
        v.addWidget(g)
        g = QGroupBox("Outputs")
        f = QFormLayout(g)
        self.chk_layer = QCheckBox("Add a compliance map (% of soundings within TVU per cell)")
        self.chk_layer.setChecked(True)
        self.chk_hol = QCheckBox("Add a coverage map (holidays)")
        self.chk_hol.setChecked(True)
        f.addRow(self.chk_layer)
        f.addRow(self.chk_hol)
        v.addWidget(g)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Run Check")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)
        self.cmb_src.currentIndexChanged.connect(self._src_changed)
        self._src_changed()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _src_changed(self):
        self.unc.set_attributes(self.attrs_of(self.cmb_src.currentData()))

    def _accept(self):
        if self.chk_tpu.isChecked():
            err = self.unc.problem()
            if err:
                QMessageBox.warning(self, "IHO S-44", err)
                return
        if self.cmb_cross.currentData() is not None and self.cmb_cross.currentData() == self.cmb_src.currentData():
            QMessageBox.warning(self, "IHO S-44", "The crossline cloud must be a different dataset.")
            return
        self.accept()

    def settings(self):
        return dict(src=self.cmb_src.currentData(), scope=self.cmb_scope.currentData(),
                    order=self.cmb_order.currentData(), use_tpu=self.chk_tpu.isChecked(), unc=self.unc.spec(),
                    ref=self.cmb_ref.currentData(), cell=self.sp_cell.value(), cross=self.cmb_cross.currentData(),
                    cov_cell=self.sp_cov.value() or None, cov_min=self.sp_covn.value(),
                    layer=self.chk_layer.isChecked(), holidays=self.chk_hol.isChecked())


def _f(v, d=1):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{d}f}"


def report_rows(rep):
    """[(section, item, value)] for display and export."""
    rows = [("General", "Standard", rep["edition"]), ("General", "Target order", ORDER_LABEL[rep["order"]]),
            ("General", "Soundings", f"{rep['points']:,}"),
            ("General", "Depth range (m)", f"{_f(rep['depth_min'], 2)} – {_f(rep['depth_max'], 2)} "
                                           f"(median {_f(rep['depth_median'], 2)})"),
            ("General", "Mean sounding spacing (m)", _f(rep.get("spacing"), 3))]
    for note in rep.get("unc_notes", []):
        rows.append(("General", "Uncertainty source", note))
    o = rep["order"]
    lim = rep["limits"][o]
    rows += [("Limits at median depth", "TVU (95 %) m", _f(lim["tvu_at_median"], 3)),
             ("Limits at median depth", "THU (95 %) m", _f(lim["thu_at_median"], 2))]
    for k in ORDERS:
        a = rep.get("apriori", {}).get(k, {})
        rows.append((f"Per order — {ORDER_LABEL[k]}", "a-priori TVU within limit %", _f(a.get("tvu_pct"))))
        rows.append((f"Per order — {ORDER_LABEL[k]}", "a-priori THU within limit %", _f(a.get("thu_pct"))))
        rows.append((f"Per order — {ORDER_LABEL[k]}", "a-posteriori residual within TVU %",
                     _f(rep["aposteriori"][k]["tvu_pct"])))
        if "crosslines" in rep:
            rows.append((f"Per order — {ORDER_LABEL[k]}", "crosslines within TVU %",
                         _f(rep["crosslines"]["by_order"][k]["tvu_pct"])))
    r = rep["residual"]
    if r.get("n"):
        rows += [("Residuals", "mean / std (m)", f"{_f(r['mean'], 3)} / {_f(r['std'], 3)}"),
                 ("Residuals", "95 % of |residual| ≤ (m)", _f(r["p95_abs"], 3)),
                 ("Residuals", "soundings over the TVU limit", f"{rep['fail_residual']:,}")]
    if "apriori" in rep:
        rows.append(("A-priori", "soundings whose TPU exceeds the TVU limit", f"{rep['fail_tpu']:,}"))
    if "crosslines" in rep:
        c = rep["crosslines"]
        rows += [("Crosslines", "compared soundings", f"{c.get('n', 0):,}"),
                 ("Crosslines", "mean / std of differences (m)", f"{_f(c.get('mean'), 3)} / {_f(c.get('std'), 3)}"),
                 ("Crosslines", "95 % of |difference| ≤ (m)", _f(c.get("p95_abs"), 3))]
    cv = rep["coverage"]
    rows += [("Coverage", "grid cell (m)", _f(cv["cell"], 2)),
             ("Coverage", "covered % (required)", f"{_f(cv['pct'])} ({_f(cv['required_pct'], 0)})"),
             ("Coverage", "holidays (gaps)", f"{cv['holidays']:,} ({cv['holiday_cells']:,} cells)")]
    fe = rep["feature"]
    if fe["feature_m"] is not None:
        rows.append(("Feature detection", f"{_f(fe['feature_m'], 1)} m features — density sufficient?",
                     ("yes" if fe["ok"] else "no") + f" (spacing {_f(fe['spacing_m'], 2)} m; ≥ 3 soundings across "
                     "the feature) — a statistical indication only, not a detection test"))
    rows.append(("Summary", "best order met (a-posteriori, ≥ 95 %)",
                 ORDER_LABEL.get(rep.get("best_order_aposteriori"), "none")))
    if rep.get("best_order_apriori") is not None or "apriori" in rep:
        rows.append(("Summary", "best order met (a-priori TVU, ≥ 95 %)",
                     ORDER_LABEL.get(rep.get("best_order_apriori"), "none")))
    return rows


class S44ResultDialog(QDialog):
    def __init__(self, rep, on_select, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IHO S-44 check — result")
        self.resize(760, 640)
        self.rep = rep
        v = QVBoxLayout(self)
        t = QLabel(f"IHO S-44 — {ORDER_LABEL[rep['order']]}")
        t.setObjectName("title")
        v.addWidget(t)
        rows = report_rows(rep)
        self.rows = rows
        tbl = QTableWidget(len(rows), 3)
        tbl.setHorizontalHeaderLabels(["Section", "Item", "Value"])
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                tbl.setItem(r, c, QTableWidgetItem(str(val)))
        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setStretchLastSection(True)
        v.addWidget(tbl, 1)
        row = QHBoxLayout()
        for label, key in (("Select over-limit residuals", "residual"), ("Select over-limit TPU", "tpu")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: on_select(k))
            b.setEnabled(key == "residual" or "apriori" in rep)
            row.addWidget(b)
        row.addStretch(1)
        b = QPushButton("Export Report…")
        b.clicked.connect(self._export)
        row.addWidget(b)
        b = QPushButton("Close")
        b.clicked.connect(self.accept)
        row.addWidget(b)
        v.addLayout(row)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _export(self):
        p, flt = QFileDialog.getSaveFileName(self, "Export S-44 report", f"S44_{self.rep['order']}.xlsx",
                                             "Excel (*.xlsx);;CSV (*.csv)")
        if not p:
            return
        try:
            write_report(p, self.rows)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export", f"{type(e).__name__}: {e}")


def write_report(path, rows):
    if path.lower().endswith(".xlsx"):
        try:
            import openpyxl
            from openpyxl.styles import Font
        except ImportError:
            path = os.path.splitext(path)[0] + ".csv"
        else:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "S-44"
            ws.append(["Section", "Item", "Value"])
            for c in ws[1]:
                c.font = Font(bold=True)
            for r in rows:
                ws.append(list(r))
            for col, wdt in zip("ABC", (34, 48, 60)):
                ws.column_dimensions[col].width = wdt
            wb.save(path)
            return path
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Section", "Item", "Value"])
        w.writerows(rows)
    return path


class S44Mixin:
    def open_s44_dialog(self):
        from core.surface import mean_spacing
        from ui.reconstruct_dialogs import JobDialog
        P = self.project
        clouds = [(d.id, d.name) for d in P.points]
        if not clouds:
            QMessageBox.information(self, "IHO S-44", "Load a point cloud (soundings) first.")
            return
        surfaces = [(d.id, d.name) for d in P.datasets if d.kind in ("grid", "tin")
                    and getattr(d.obj, "cvals", None) is None]
        src0 = P.get(self._bound_id) if self._bound_id is not None else P.get(clouds[0][0])
        spacing = mean_spacing(src0.obj.xyz, src0.obj.active_indices()[::max(1, src0.obj.n_active // 200000)])
        dlg = S44Dialog(clouds, surfaces, self._bound_id, self.sel is not None and self.sel.count > 0,
                        lambda cid: list(P.get(cid).obj.attrs) if P.get(cid) is not None else [], spacing, self)
        if dlg.exec() != QDialog.Accepted:
            return
        s = dlg.settings()
        src = P.get(s["src"])
        pc = src.obj
        if s["scope"] == "selected" and src.id == self._bound_id:
            idx = np.flatnonzero(self.sel.mask & pc.active_mask())
        else:
            idx = pc.active_indices()
        ref_ds = P.get(s["ref"]) if s["ref"] is not None else None
        cross_ds = P.get(s["cross"]) if s["cross"] is not None else None
        crs = P.crs

        def job(log, progress, cancel):
            from core import s44
            from core import uncertainty as un
            depth = np.abs(pc.xyz[idx, 2])
            tvu95 = thu95 = None
            notes = []
            if s["use_tpu"]:
                u = un.resolve(s["unc"], pc, idx, crs, depth, fill=None)
                tvu95 = u["tvu"] * un.Z95
                thu95 = None if u["thu"] is None else u["thu"] * un.Z95
                notes = u["notes"]
                miss = u.get("tvu_missing", 0)
                if miss:
                    notes.append(f"{miss:,} soundings without TVU are counted as outside the limits")
                    tvu95 = np.where(np.isfinite(tvu95), tvu95, np.inf)
                log("; ".join(notes))
            ref = None
            if ref_ds is not None:
                from core.filters import surface_z
                ref = surface_z(ref_ds.obj, pc.xyz[idx, 0], pc.xyz[idx, 1])
                log(f"Reference: {ref_ds.name}")
            cross = None
            if cross_ds is not None:
                cross = (cross_ds.obj.xyz, cross_ds.obj.active_indices())
            log(f"Assessing {len(idx):,} soundings against {ORDER_LABEL[s['order']]}…")
            rep, det = s44.assess(pc.xyz, idx, s["order"], True, tvu95, thu95, ref, s["cell"], cross,
                                  s["cov_cell"], s["cov_min"], progress)
            rep["unc_notes"] = notes
            rep["dataset"] = src.name
            for sec, item, val in report_rows(rep):
                log(f"  {sec} · {item}: {val}")
            return rep, det

        out = {}
        JobDialog("IHO S-44 check", job, lambda r: out.update(r=r), self).exec()
        if "r" not in out:
            return
        rep, det = out["r"]
        self._s44_ctx = (src.id, idx, det)
        self._s44_layers(src, idx, rep, det, s)

        def select(kind):
            if src.id != self._bound_id:
                QMessageBox.information(self, "IHO S-44", "Make this cloud the edit target (Edit) to select its "
                                        "soundings.")
                return
            m = det["fail_residual"] if kind == "residual" else det["fail_tpu"]
            mask = np.zeros(len(pc), bool)
            mask[idx[m]] = True
            self.sel.mask = mask & pc.active_mask()
            self._sel_methods = [f"IHO S-44 {ORDER_LABEL[rep['order']]} ({kind})"]
            self._selection_changed()
            self._refresh_subset_if_open()
            self.statusBar().showMessage(f"{int(m.sum()):,} soundings selected", 8000)

        S44ResultDialog(rep, select, self).exec()

    def _s44_layers(self, src, idx, rep, det, s):
        from core import s44
        from core.project import Dataset
        from core.surface import Dem
        added = False
        pc = src.obj
        cell = rep["coverage"]["cell"]
        if s["layer"]:
            mc = s44.map_cell(pc.xyz, idx, max(s["cell"], 0.05))
            pct, x0, ytop = s44.compliance_grid(pc.xyz, idx, det["residual"], det["limit"], mc)
            zr = self._median_grid(pc.xyz, idx, x0, ytop, mc, pct.shape)
            dem = Dem(zr.astype(np.float32), x0, ytop, mc, "s44",
                      cvals=pct.astype(np.float32), crange=(80.0, 100.0), cpalette="Turbo",
                      meta={"title": f"% within TVU {ORDER_LABEL[rep['order']]}"})
            self.project.add(Dataset(self.project.unique_name(f"S-44 compliance {rep['order']} · {src.name}"), "grid",
                                     dem, crs=self.project.crs, source_crs=self.project.crs, derived_from=src.id,
                                     edit_version=src.edit_version))
            added = True
        if s["holidays"] and "count" not in det["coverage"]:
            self.statusBar().showMessage("Coverage map skipped: the survey extent is too large for a map at this "
                                         "cell size (the coverage figures are in the report)", 10000)
        elif s["holidays"]:
            cov = det["coverage"]
            cnt = cov["count"][::-1].astype(np.float32)
            area = cov["area"][::-1]
            val = np.where(area, np.where(cnt >= s["cov_min"], 1.0, 0.0), np.nan).astype(np.float32)
            zr = self._median_grid(pc.xyz, idx, cov["x0"], cov["ytop"], cell, val.shape)
            zr = np.where(np.isfinite(val), np.where(np.isfinite(zr), zr, np.nanmedian(zr)), np.nan)
            dem = Dem(zr.astype(np.float32), cov["x0"], cov["ytop"], cell, "s44", cvals=val, crange=(0.0, 1.0),
                      cpalette="Diverging", meta={"title": "coverage (0 = holiday)"})
            self.project.add(Dataset(self.project.unique_name(f"S-44 coverage · {src.name}"), "grid", dem,
                                     crs=self.project.crs, source_crs=self.project.crs, derived_from=src.id,
                                     edit_version=src.edit_version))
            added = True
        if added:
            self._project_changed()
            self._set_dirty(True)

    @staticmethod
    def _median_grid(xyz, idx, x0, ytop, cell, shape):
        ny, nx = shape
        p = xyz[idx]
        c = np.floor((p[:, 0] - x0) / cell).astype(np.int64)
        r = np.floor((ytop - p[:, 1]) / cell).astype(np.int64)
        ok = (c >= 0) & (c < nx) & (r >= 0) & (r < ny)
        key = r[ok] * nx + c[ok]
        z = p[ok, 2]
        s = np.zeros(ny * nx)
        n = np.zeros(ny * nx)
        np.add.at(s, key, z)
        np.add.at(n, key, 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            g = np.where(n > 0, s / n, np.nan)
        return g.reshape(ny, nx)
