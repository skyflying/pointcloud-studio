"""XYZ / ASC import dialog: raw preview, start row, delimiter, column mapping."""
from __future__ import annotations

import os

from PySide6.QtGui import QColor, QFont, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from fileio.xyz_reader import (
    DELIMITERS, ROLES, detect_delimiter, detect_start_row, guess_roles,
    read_preview, split_line, validate_roles,
)
from ui.theme import C, dark_titlebar

N_TABLE_ROWS = 20


def _section(text):
    lbl = QLabel(text.upper())
    lbl.setObjectName("sectionTitle")
    return lbl


class XyzImportDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Import — {os.path.basename(path)}")
        self.resize(1040, 800)
        self.path = path
        self.lines, self.encoding = read_preview(path)
        self.combos = []
        self._roles_ncols = -1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QLabel(os.path.basename(path))
        title.setObjectName("title")
        sub = QLabel(f"{path}   ·   Encoding: {self.encoding}")
        sub.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addSpacing(6)

        # raw preview
        layout.addWidget(_section(f"Raw preview · first {len(self.lines)} lines"))
        self.raw = QPlainTextEdit(readOnly=True)
        self.raw.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont()
        mono.setFamilies(["Cascadia Mono", "Consolas", "JetBrains Mono", "Menlo", "DejaVu Sans Mono"])
        mono.setStyleHint(QFont.Monospace)
        self.raw.setFont(mono)
        self.raw.setPlainText("\n".join(f"{i + 1:>5}  {l}" for i, l in enumerate(self.lines)))
        layout.addWidget(self.raw, 2)

        legend = QLabel(f"<span style='color:{C['muted']}'>■</span> skipped    "
                        f"<span style='color:{C['success']}'>■</span> first data row")
        legend.setObjectName("muted")
        layout.addWidget(legend)

        # parsing options
        layout.addSpacing(4)
        layout.addWidget(_section("Parsing"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.spin_start = QSpinBox(minimum=1, maximum=max(1, len(self.lines)))
        self.spin_start.setToolTip("First line that contains data; lines above are treated as header")
        self.spin_start.setMinimumWidth(90)
        self.cmb_delim = QComboBox()
        self.cmb_delim.setMinimumWidth(160)
        for key, (label, _) in DELIMITERS.items():
            self.cmb_delim.addItem(label, key)
        for text, w in (("Start at line", self.spin_start), ("Delimiter", self.cmb_delim)):
            lbl = QLabel(text)
            lbl.setObjectName("muted")
            row.addWidget(lbl)
            row.addWidget(w)
            row.addSpacing(18)
        row.addStretch()
        layout.addLayout(row)

        # column mapping
        layout.addSpacing(4)
        layout.addWidget(_section("Column mapping"))
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(30)
        layout.addWidget(self.table, 3)

        # footer
        foot = QHBoxLayout()
        self.lbl_msg = QLabel()
        foot.addWidget(self.lbl_msg)
        foot.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Import")
        self.btn_ok.setProperty("primary", True)
        self.btn_ok.setDefault(True)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_ok)
        layout.addLayout(foot)

        # auto-detect
        delim = detect_delimiter(self.lines)
        self.cmb_delim.setCurrentIndex(self.cmb_delim.findData(delim))
        self.spin_start.setValue(detect_start_row(self.lines, delim) + 1)

        self.spin_start.valueChanged.connect(self._rebuild)
        self.cmb_delim.currentIndexChanged.connect(self._on_delim_changed)
        self._rebuild()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    # ---------- events ----------
    def _on_delim_changed(self):
        self._roles_ncols = -1  # re-guess roles when the delimiter changes
        self._rebuild()

    def _rebuild(self):
        delim = self.cmb_delim.currentData()
        start = self.spin_start.value() - 1
        rows = [split_line(l, delim) for l in self.lines[start:start + N_TABLE_ROWS]]
        rows = [r for r in rows if r]
        ncols = max((len(r) for r in rows), default=0)

        if ncols != self._roles_ncols:
            roles = guess_roles(rows)
            self._roles_ncols = ncols
        else:
            roles = [c.currentText() for c in self.combos]

        self.table.clear()
        self.table.setColumnCount(ncols)
        self.table.setRowCount(len(rows) + 1)
        self.table.setHorizontalHeaderLabels([f"Column {i + 1}" for i in range(ncols)])
        self.table.setVerticalHeaderLabels(["Role"] + [str(start + 1 + i) for i in range(len(rows))])
        self.table.setRowHeight(0, 40)
        self.combos = []
        for c in range(ncols):
            cmb = QComboBox()
            cmb.addItems(ROLES)
            cmb.setCurrentText(roles[c] if c < len(roles) else ROLES[-2])
            cmb.currentIndexChanged.connect(self._validate)
            self.table.setCellWidget(0, c, cmb)
            self.combos.append(cmb)
        for r, tokens in enumerate(rows, start=1):
            for c, t in enumerate(tokens):
                self.table.setItem(r, c, QTableWidgetItem(t))
        self.table.resizeColumnsToContents()
        for c in range(ncols):
            self.table.setColumnWidth(c, max(self.table.columnWidth(c), 150))
        self._highlight_raw(start)
        self._validate()

    def _highlight_raw(self, start):
        sels = []
        doc = self.raw.document()
        for i in range(min(start + 1, doc.blockCount())):
            sel = QTextEdit.ExtraSelection()
            if i == start:
                sel.format.setBackground(QColor(C["start_bg"]))
                sel.format.setForeground(QColor(C["success"]))
            else:
                sel.format.setBackground(QColor(C["skip_bg"]))
                sel.format.setForeground(QColor(C["muted_dim"]))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.cursor = QTextCursor(doc.findBlockByNumber(i))
            sels.append(sel)
        self.raw.setExtraSelections(sels)
        self.raw.setTextCursor(QTextCursor(doc.findBlockByNumber(start)))
        self.raw.centerCursor()

    def _validate(self):
        roles = [c.currentText() for c in self.combos]
        err = validate_roles(roles) if roles else "No parsable data after the start line"
        if err:
            self.lbl_msg.setText(f"<span style='color:{C['danger']}'>●  {err}</span>")
        else:
            self.lbl_msg.setText(f"<span style='color:{C['success']}'>●  {len(roles)} columns · ready to import</span>")
        self.btn_ok.setEnabled(not err)

    # ---------- result ----------
    def settings(self):
        return dict(
            skip_rows=self.spin_start.value() - 1,
            delim_key=self.cmb_delim.currentData(),
            column_roles=[c.currentText() for c in self.combos],
            encoding=self.encoding,
        )
