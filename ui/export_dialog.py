"""Export dialog: content, format, format options, output path."""
from __future__ import annotations

import math
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from fileio.writers import TEXT_DELIMITERS, column_choices, default_columns, pyproj
from ui.theme import C, dark_titlebar

TEXT_EXT = (".xyz", ".asc", ".txt", ".csv", ".pts")
FORMATS = [("las", "LAS  (.las)"), ("laz", "LAZ  (.laz, compressed)"), ("xyz", "Text  (.xyz / .asc / .txt / .csv)")]


def _section(text):
    lbl = QLabel(text.upper())
    lbl.setObjectName("sectionTitle")
    return lbl


def _muted(text):
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl


class ExportDialog(QDialog):
    def __init__(self, pc, counts, parent=None):
        """counts: dict(active=, selected=, trash=)"""
        super().__init__(parent)
        self.pc = pc
        self.counts = counts
        self.setWindowTitle("Export")
        self.resize(640, 740)
        src = pc.source
        self.src_is_las = src.get("las") is not None
        src_path = src.get("path", "export.las")
        self.src_path = src_path
        stem, src_ext = os.path.splitext(src_path)
        self._stem = stem + "_edited"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)
        title = QLabel("Export point cloud")
        title.setObjectName("title")
        lay.addWidget(title)
        lay.addWidget(_muted(f"Source: {os.path.basename(src_path)}"))

        # ---- content
        lay.addSpacing(6)
        lay.addWidget(_section("Content"))
        self.grp_content = QButtonGroup(self)
        self.rb = {}
        for key, label in (("active", "Active points"), ("selected", "Selected points only"),
                           ("trash", "Trash only")):
            rb = QRadioButton(f"{label}   ({counts[key]:,})")
            rb.setEnabled(counts[key] > 0)
            self.grp_content.addButton(rb)
            self.rb[key] = rb
            lay.addWidget(rb)
        self.rb["active"].setChecked(True)
        self.chk_trash = QCheckBox(f"Also save the trash ({counts['trash']:,} points) to a separate “_trash” file")
        self.chk_trash.setEnabled(counts["trash"] > 0)
        lay.addWidget(self.chk_trash)
        self.grp_content.buttonToggled.connect(self._update)
        self.chk_trash.toggled.connect(self._update)

        # ---- format
        lay.addSpacing(6)
        lay.addWidget(_section("Format"))
        self.cmb_fmt = QComboBox()
        for key, label in FORMATS:
            self.cmb_fmt.addItem(label, key)
        lay.addWidget(self.cmb_fmt)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_las_page())
        self.pages.addWidget(self._build_text_page())
        lay.addWidget(self.pages, 1)

        # ---- output
        lay.addWidget(_section("Output"))
        row = QHBoxLayout()
        self.ed_path = QLineEdit()
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse)
        row.addWidget(self.ed_path, 1)
        row.addWidget(btn_browse)
        lay.addLayout(row)
        self.lbl_summary = QLabel()
        self.lbl_summary.setWordWrap(True)
        lay.addWidget(self.lbl_summary)

        foot = QHBoxLayout()
        foot.addStretch()
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton("Export")
        self.btn_ok.setProperty("primary", True)
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._accept)
        foot.addWidget(b_cancel)
        foot.addWidget(self.btn_ok)
        lay.addLayout(foot)

        # defaults
        if self.src_is_las:
            fmt, ext = ("laz" if src.get("compressed") else "las"), src_ext.lower()
        else:
            fmt, ext = "xyz", (src_ext.lower() if src_ext.lower() in TEXT_EXT else ".xyz")
        self.ed_path.setText(self._stem + ext)
        self.cmb_fmt.setCurrentIndex(self.cmb_fmt.findData(fmt))
        self.cmb_fmt.currentIndexChanged.connect(self._on_format)
        self.ed_path.textChanged.connect(self._update)
        self._on_format()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    # ---------- pages ----------
    def _build_las_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        if self.src_is_las:
            s = self.pc.source
            v.addWidget(_muted(
                f"Everything is carried over from the source file: LAS {s['version']}, point format "
                f"{s['point_format']}, scale / offset, all dimensions and {len(s['vlrs'])} VLR(s) "
                f"(including any coordinate system). Only the removed points are left out."))
            self.cmb_ver = self.cmb_scale = self.ed_epsg = self.chk_extra = None
        else:
            form = QFormLayout()
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(10)
            self.cmb_ver = QComboBox()
            self.cmb_ver.addItems(["1.2", "1.4"])
            self.cmb_scale = QComboBox()
            self.cmb_scale.setEditable(True)
            self.cmb_scale.addItems(["0.001", "0.01", "0.0001"])
            self.ed_epsg = QLineEdit()
            self.ed_epsg.setPlaceholderText("optional, e.g. 3826 = TWD97 / TM2 zone 121")
            if pyproj is None:
                self.ed_epsg.setEnabled(False)
                self.ed_epsg.setPlaceholderText("install 'pyproj' to embed a coordinate system")
            custom = [k for k in self.pc.attrs if k not in ("Intensity", "R", "G", "B", "Classification")]
            self.chk_extra = QCheckBox(f"Write custom columns as extra dimensions ({', '.join(custom) or 'none'})")
            self.chk_extra.setChecked(bool(custom))
            self.chk_extra.setEnabled(bool(custom))
            form.addRow(_muted("LAS version"), self.cmb_ver)
            form.addRow(_muted("Coordinate scale"), self.cmb_scale)
            form.addRow(_muted("EPSG code"), self.ed_epsg)
            v.addLayout(form)
            v.addWidget(self.chk_extra)
            v.addWidget(_muted("Point format is chosen automatically (with RGB if available). "
                               "Intensity, Classification and RGB map to standard LAS fields; "
                               "0–255 colors are scaled to 16-bit. Offset = rounded minimum coordinate."))
        v.addStretch()
        return w

    def _build_text_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.addWidget(_muted("Columns — tick to include, drag to reorder"))
        self.lst_cols = QListWidget()
        self.lst_cols.setDragDropMode(QAbstractItemView.InternalMove)
        self.lst_cols.setStyleSheet(f"QListWidget {{ background: {C['surface']}; border: 1px solid {C['border']}; "
                                    f"border-radius: 8px; padding: 4px; }}")
        defaults = default_columns(self.pc)
        choices = column_choices(self.pc)
        for name in defaults + [c for c in choices if c not in defaults]:
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setCheckState(Qt.Checked if name in defaults else Qt.Unchecked)
            self.lst_cols.addItem(it)
        self.lst_cols.itemChanged.connect(self._update)
        v.addWidget(self.lst_cols, 1)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        self.cmb_delim = QComboBox()
        for key, (label, _) in TEXT_DELIMITERS.items():
            self.cmb_delim.addItem(label, key)
        src_delim = {"whitespace": "space", "tab": "tab", "comma": "comma",
                     "semicolon": "semicolon"}.get(self.pc.source.get("delimiter"), "space")
        self.cmb_delim.setCurrentIndex(self.cmb_delim.findData(src_delim))
        self.spin_dec = QSpinBox(minimum=0, maximum=9)
        scales = self.pc.source.get("scales")
        self.spin_dec.setValue(min(9, max(0, round(-math.log10(scales[0])))) if scales else 3)
        self.spin_dec.setSuffix(" decimals")
        self.chk_header = QCheckBox("Write a header line with column names")
        self.chk_header.setChecked(True)
        form.addRow(_muted("Delimiter"), self.cmb_delim)
        form.addRow(_muted("X / Y / Z"), self.spin_dec)
        v.addLayout(form)
        v.addWidget(self.chk_header)
        return w

    # ---------- behaviour ----------
    def fmt(self):
        return self.cmb_fmt.currentData()

    def content(self):
        return next(k for k, rb in self.rb.items() if rb.isChecked())

    def _on_format(self):
        fmt = self.fmt()
        self.pages.setCurrentIndex(1 if fmt == "xyz" else 0)
        base, ext = os.path.splitext(self.ed_path.text().strip() or self._stem)
        if fmt in ("las", "laz"):
            ext = "." + fmt
        elif ext.lower() not in TEXT_EXT:
            ext = ".csv" if self.cmb_delim.currentData() == "comma" else ".xyz"
        self.ed_path.setText(base + ext)
        self._update()

    def _browse(self):
        fmt = self.fmt()
        flt = {"las": "LAS (*.las)", "laz": "LAZ (*.laz)",
               "xyz": "Text (*.xyz *.asc *.txt *.csv *.pts)"}[fmt]
        path, _ = QFileDialog.getSaveFileName(self, "Export as", self.ed_path.text(), flt)
        if path:
            self.ed_path.setText(path)

    def trash_path(self):
        base, ext = os.path.splitext(self.ed_path.text().strip())
        return base + "_trash" + ext

    def columns(self):
        return [self.lst_cols.item(i).text() for i in range(self.lst_cols.count())
                if self.lst_cols.item(i).checkState() == Qt.Checked]

    def _also_trash(self):
        return self.chk_trash.isChecked() and self.content() != "trash"

    def _update(self, *_):
        self.chk_trash.setEnabled(self.counts["trash"] > 0 and self.content() != "trash")
        path = self.ed_path.text().strip()
        n = self.counts[self.content()]
        err = ""
        if not path:
            err = "Choose an output file"
        elif self.fmt() == "xyz" and not self.columns():
            err = "Select at least one column"
        if err:
            self.lbl_summary.setText(f"<span style='color:{C['danger']}'>●  {err}</span>")
        else:
            txt = f"<span style='color:{C['success']}'>●</span>  {n:,} points → <b>{os.path.basename(path)}</b>"
            if self._also_trash():
                txt += (f"<br><span style='color:{C['danger']}'>●</span>  {self.counts['trash']:,} trash "
                        f"points → <b>{os.path.basename(self.trash_path())}</b>")
            self.lbl_summary.setText(txt)
        self.btn_ok.setEnabled(not err)

    def _accept(self):
        if not self.src_is_las and self.fmt() != "xyz":
            try:
                float(self.cmb_scale.currentText())
                epsg = self.ed_epsg.text().strip()
                if epsg:
                    pyproj.CRS.from_epsg(int(epsg))
            except Exception as e:
                self.lbl_summary.setText(f"<span style='color:{C['danger']}'>●  Invalid scale or EPSG code ({e})</span>")
                return
        self.accept()

    # ---------- result ----------
    def job(self):
        fmt = self.fmt()
        if fmt == "xyz":
            opts = dict(columns=self.columns(), delimiter=TEXT_DELIMITERS[self.cmb_delim.currentData()][1],
                        decimals=self.spin_dec.value(), header=self.chk_header.isChecked())
        elif self.src_is_las:
            opts = {}
        else:
            epsg = self.ed_epsg.text().strip()
            opts = dict(version=self.cmb_ver.currentText(), scale=float(self.cmb_scale.currentText()),
                        epsg=int(epsg) if epsg else None, extra_dims=self.chk_extra.isChecked())
        return dict(content=self.content(), fmt=fmt, path=self.ed_path.text().strip(), opts=opts,
                    trash_path=self.trash_path() if self._also_trash() else None)
