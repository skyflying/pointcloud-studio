"""Point classification dialog: ground filter (4 algorithms) + optional vegetation / building classes."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from core.classify import GROUND_HELP, GROUND_METHODS
from ui.theme import dark_titlebar


def _section(t):
    l = QLabel(t.upper())
    l.setObjectName("sectionTitle")
    return l


def _muted(t):
    l = QLabel(t)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


def _dsp(v, lo, hi, step, dec=2, suffix=" m"):
    s = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=dec, singleStep=step)
    s.setValue(v)
    s.setSuffix(suffix)
    return s


class ClassifyDialog(QDialog):
    def __init__(self, counts, spacing, has_subset, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Classify points")
        self.resize(560, 760)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(7)
        t = QLabel("Classify points")
        t.setObjectName("title")
        v.addWidget(t)
        v.addWidget(_muted("Step 1 separates ground (class 2) from everything else; step 2 optionally classifies the "
                           "rest. The whole run can be undone with Ctrl+Z."))
        v.addWidget(_section("Step 1 · Ground filter"))
        self.cmb = QComboBox()
        for k, label in GROUND_METHODS.items():
            self.cmb.addItem(label, k)
        self.cmb.addItem("Keep the existing ground (class 2) — step 2 only", None)
        v.addWidget(self.cmb)
        self.help = _muted("")
        v.addWidget(self.help)
        cell = max(round(spacing * 2, 1), 0.5)
        self.pages = QStackedWidget()
        self.w = {}

        def page(rows):
            w = QWidget()
            f = QFormLayout(w)
            f.setHorizontalSpacing(14)
            for label, key, widget, tip in rows:
                widget.setToolTip(tip)
                self.w[key] = widget
                f.addRow(_muted(label), widget)
            self.pages.addWidget(w)

        page([("Cell size", "smrf_cell", _dsp(cell, 0.1, 50, 0.25), "Grid resolution (≈ 1–2 × point spacing)"),
              ("Slope", "smrf_slope", _dsp(0.15, 0.01, 2.0, 0.05, 2, ""), "Max. terrain slope (rise / run): 0.15 flat … 0.6+ steep"),
              ("Max. window", "smrf_window", _dsp(18.0, 1, 200, 2), "≥ the largest building / object width"),
              ("Height threshold", "smrf_thr", _dsp(0.5, 0.05, 10, 0.05), "Max. height above the ground model"),
              ("Slope scalar", "smrf_scalar", _dsp(1.25, 0, 10, 0.25, 2, ""), "Extra tolerance × local slope")])
        page([("Cell size", "pmf_cell", _dsp(cell, 0.1, 50, 0.25), "Grid resolution"),
              ("Max. window", "pmf_window", _dsp(33.0, 3, 300, 2), "≥ the largest object width"),
              ("Slope", "pmf_slope", _dsp(0.3, 0.01, 3.0, 0.05, 2, ""), "Terrain slope (rise / run) — increase on steep terrain"),
              ("Initial threshold", "pmf_dh0", _dsp(0.15, 0.01, 5, 0.05), "Elevation-difference threshold, smallest window"),
              ("Max. threshold", "pmf_dhmax", _dsp(2.5, 0.1, 50, 0.5), "Upper limit of the threshold")])
        page([("Cloth resolution", "csf_cloth", _dsp(max(2.0, cell * 2), 0.1, 50, 0.25),
               "Distance between cloth particles — larger (2 m+) keeps the cloth from settling on large flat roofs"),
              ("Rigidness", "csf_rig", QComboBox(), "1 soft (steep slopes) · 2 medium · 3 stiff (flat terrain)"),
              ("Iterations", "csf_it", QSpinBox(minimum=50, maximum=5000, value=500, singleStep=50), "Simulation steps"),
              ("Class threshold", "csf_thr", _dsp(0.5, 0.05, 10, 0.05), "Max. distance to the cloth for ground")])
        for i, lab in ((1, "1 — soft (steep slopes)"), (2, "2 — medium"), (3, "3 — stiff (flat)")):
            self.w["csf_rig"].addItem(lab, i)
        self.w["csf_rig"].setCurrentIndex(1)
        page([("Cell size", "gm_cell", _dsp(max(5.0, cell * 5), 0.5, 200, 1), "Must be larger than the largest object"),
              ("Height threshold", "gm_thr", _dsp(0.5, 0.05, 20, 0.05), "Max. height above the lowest-point surface")])
        self.pages.addWidget(QWidget())                                   # "keep existing ground"
        v.addWidget(self.pages)

        v.addWidget(_section("Step 2 · Above ground (optional)"))
        self.chk_veg = QCheckBox("Vegetation by height above ground → low 3 · medium 4 · high 5")
        self.chk_veg.setChecked(True)
        v.addWidget(self.chk_veg)
        row = QHBoxLayout()
        self.sp_v = [_dsp(0.5, 0, 50, 0.1), _dsp(2.0, 0, 100, 0.5), _dsp(5.0, 0, 200, 0.5)]
        for lab, sp in zip(("from", "medium ≥", "high ≥"), self.sp_v):
            row.addWidget(_muted(lab))
            row.addWidget(sp)
        v.addLayout(row)
        self.chk_bld = QCheckBox("Buildings (flat / pitched roofs + walls) → 6")
        self.chk_bld.setChecked(True)
        v.addWidget(self.chk_bld)
        row = QHBoxLayout()
        self.sp_bh = _dsp(2.5, 0.5, 100, 0.5)
        self.sp_bh.setToolTip("Minimum roof height above ground")
        self.sp_br = _dsp(0.02, 0.001, 0.3, 0.005, 3, "")
        self.sp_br.setToolTip("Max. surface roughness λ3 / Σλ of the roof (0.01 smooth roofs … 0.05 rough)")
        self.sp_ba = _dsp(25.0, 1, 1e5, 5, 0, " m²")
        self.sp_ba.setToolTip("Minimum roof area")
        for lab, sp in (("min height", self.sp_bh), ("roughness ≤", self.sp_br), ("min area", self.sp_ba)):
            row.addWidget(_muted(lab))
            row.addWidget(sp)
        v.addLayout(row)

        v.addWidget(_section("Points"))
        self.grp = QButtonGroup(self)
        self.rb = {}
        for key, label in (("all", f"All active points   ({counts.get('all', 0):,})"),
                           ("selected", f"Selected points   ({counts.get('selected', 0):,})"),
                           ("subset", f"Current subset   ({counts.get('subset', 0):,})")):
            rb = QRadioButton(label)
            self.grp.addButton(rb)
            self.rb[key] = rb
            v.addWidget(rb)
        self.rb["all"].setChecked(True)
        self.rb["selected"].setEnabled(counts.get("selected", 0) > 0)
        self.rb["subset"].setEnabled(has_subset and counts.get("subset", 0) > 0)
        self.chk_only = QCheckBox("Only change unclassified points (class 0 / 1) — keep manual classes")
        v.addWidget(self.chk_only)
        v.addStretch()
        foot = QHBoxLayout()
        foot.addStretch()
        b = QPushButton("Cancel")
        b.clicked.connect(self.reject)
        ok = QPushButton("Classify")
        ok.setProperty("primary", True)
        ok.clicked.connect(self.accept)
        foot.addWidget(b)
        foot.addWidget(ok)
        v.addLayout(foot)
        self.cmb.currentIndexChanged.connect(self._method)
        self._method()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _method(self):
        m = self.cmb.currentData()
        self.pages.setCurrentIndex(self.cmb.currentIndex())
        self.help.setText(GROUND_HELP.get(m, "Uses the points already classified as ground (2) for the "
                                             "height-above-ground of step 2."))

    def spec(self):
        m = self.cmb.currentData()
        w = self.w
        prm = {"smrf": dict(cell=w["smrf_cell"].value(), slope=w["smrf_slope"].value(), window=w["smrf_window"].value(),
                            threshold=w["smrf_thr"].value(), scalar=w["smrf_scalar"].value()),
               "pmf": dict(cell=w["pmf_cell"].value(), max_window=w["pmf_window"].value(), slope=w["pmf_slope"].value(),
                           initial_dh=w["pmf_dh0"].value(), max_dh=w["pmf_dhmax"].value()),
               "csf": dict(cloth=w["csf_cloth"].value(), rigidness=w["csf_rig"].currentData(),
                           iterations=w["csf_it"].value(), threshold=w["csf_thr"].value()),
               "gridmin": dict(cell=w["gm_cell"].value(), threshold=w["gm_thr"].value())}.get(m, {})
        veg = tuple(sp.value() for sp in self.sp_v) if self.chk_veg.isChecked() else None
        bld = (dict(min_height=self.sp_bh.value(), roughness=self.sp_br.value(), min_area=self.sp_ba.value(),
                    cell=1.0) if self.chk_bld.isChecked() else None)
        scope = next(k for k, rb in self.rb.items() if rb.isChecked())
        return dict(method=m, params=prm, veg=veg, buildings=bld, scope=scope, only_unclassified=self.chk_only.isChecked(),
                    label=self.cmb.currentText().split(" — ")[0])
