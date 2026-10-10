"""Subset editor panel (BASE-Editor style): side views of the points inside the subset box,
with box / lasso selection, reject / accept, paging and review tracking."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

from render.picking import _polygon_mask
from ui.icons import icon
from ui.profile_panel import ProfilePlot
from ui.theme import C, SELECTION_RGBA

TOOLS = ("pan", "box", "lasso")


class SubsetPlot(ProfilePlot):
    """ProfilePlot + selection tools. on_select(indices, op) is called with GLOBAL point indices."""

    def __init__(self, on_select):
        super().__init__()
        self.on_select = on_select
        self.tool = "box"
        self.idx = np.empty(0, dtype=np.int64)
        self._shape = None              # list of QPointF while drawing
        self._shape_start = None
        self.setFocusPolicy(Qt.StrongFocus)

    def set_tool(self, tool):
        self.tool = tool
        self._shape = None
        self.setCursor(Qt.ArrowCursor if tool == "pan" else Qt.CrossCursor)
        self.update()

    @staticmethod
    def _op(mods):
        if mods & Qt.ShiftModifier:
            return "add"
        if mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return "subtract"
        return "replace"

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.tool in ("box", "lasso") and not self.measure_mode:
            p = e.position()
            self._shape_start = p
            self._shape = [p]
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._shape is not None:
            p = e.position()
            if self.tool == "box":
                s = self._shape_start
                self._shape = [s, QPointF(p.x(), s.y()), p, QPointF(s.x(), p.y())]
            elif (p - self._shape[-1]).manhattanLength() >= 2:
                self._shape.append(p)
            self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._shape is not None and e.button() == Qt.LeftButton:
            shape, self._shape = self._shape, None
            op = self._op(e.modifiers())
            xs = [p.x() for p in shape]
            ys = [p.y() for p in shape]
            if len(shape) < 3 or (max(xs) - min(xs) < 3 and max(ys) - min(ys) < 3):
                if op == "replace":
                    self.on_select(np.empty(0, dtype=np.int64), "replace")
            else:
                self.on_select(self._inside(shape), op)
            self.update()
            return
        super().mouseReleaseEvent(e)

    def _inside(self, shape):
        if self.ch is None or len(self.ch) == 0:
            return np.empty(0, dtype=np.int64)
        W, H = self.width(), self.height()
        mask = _polygon_mask([(p.x(), p.y()) for p in shape], W, H)
        x, y = self.to_px(self.ch, self.z)
        xi, yi = x.astype(np.int64), y.astype(np.int64)
        ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
        hit = np.zeros(len(xi), dtype=bool)
        hit[ok] = mask[yi[ok], xi[ok]] > 0
        return self.idx[hit]

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self._shape:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            col = QColor(C["accent"])
            fill = QColor(col)
            fill.setAlpha(40)
            p.setPen(QPen(col, 1.4, Qt.DashLine))
            p.setBrush(fill)
            p.drawPolygon(QPolygonF(self._shape))
            p.end()


class SubsetPanel(QWidget):
    """cb: subset_page(dir) subset_width(v) subset_overlap(frac) subset_focus() subset_mark_done()
    subset_select(idx, op) reject_selected() accept_selected() set_show_noise(on) set_reject_mode(mode)
    subset_view(view) subset_close()"""

    def __init__(self, cb):
        super().__init__()
        self.cb = cb
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 6)
        v.setSpacing(6)

        def lab(t):
            w = QLabel(t)
            w.setObjectName("muted")
            return w

        # row 1: view, tools, paging, geometry
        r1 = QHBoxLayout()
        r1.setSpacing(6)
        self.cmb_view = QComboBox()
        self.cmb_view.addItem("Side (along)", "along")
        self.cmb_view.addItem("End (across)", "across")
        self.cmb_view.setToolTip("Side view: looking across the axis · End view: looking along the axis")
        self.cmb_view.currentIndexChanged.connect(lambda: cb.subset_view(self.cmb_view.currentData()))
        r1.addWidget(lab("View"))
        r1.addWidget(self.cmb_view)
        r1.addSpacing(6)
        self.tool_btns = {}
        grp = QButtonGroup(self)
        for key, text, tip in (("pan", "Pan", "Drag to pan, wheel to zoom (Shift+wheel: vertical exaggeration)"),
                               ("box", "Box", "Drag a rectangle to select · Shift add · Ctrl subtract"),
                               ("lasso", "Lasso", "Draw a freehand outline to select · Shift add · Ctrl subtract")):
            b = QToolButton()
            b.setIcon(icon({"pan": "navigate", "box": "box", "lasso": "lasso"}[key]))
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            grp.addButton(b)
            self.tool_btns[key] = b
            r1.addWidget(b)
        r1.addSpacing(6)
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setToolTip("Previous subset (PageUp)")
        self.btn_prev.clicked.connect(lambda: cb.subset_page(-1))
        self.btn_next = QPushButton("▶")
        self.btn_next.setToolTip("Next subset along the axis (PageDown)")
        self.btn_next.clicked.connect(lambda: cb.subset_page(1))
        r1.addWidget(self.btn_prev)
        r1.addWidget(self.btn_next)
        r1.addSpacing(6)
        self.sp_width = QDoubleSpinBox(minimum=0.5, maximum=1e4, value=20.0, singleStep=1, decimals=1)
        self.sp_width.setSuffix(" m")
        self.sp_width.setKeyboardTracking(False)
        self.sp_width.valueChanged.connect(cb.subset_width)
        self.sp_overlap = QSpinBox(minimum=0, maximum=90, value=10, singleStep=5)
        self.sp_overlap.setSuffix(" %")
        self.sp_overlap.setToolTip("Overlap between consecutive pages")
        self.sp_overlap.valueChanged.connect(lambda v_: cb.subset_overlap(v_ / 100))
        self.sp_length = QDoubleSpinBox(minimum=1.0, maximum=1e5, value=100.0, singleStep=10, decimals=1)
        self.sp_length.setSuffix(" m")
        self.sp_length.setKeyboardTracking(False)
        self.sp_length.setToolTip("Length of the subset along its axis (page size)")
        self.sp_length.valueChanged.connect(cb.subset_length)
        for sp in (self.sp_length, self.sp_width, self.sp_overlap):
            sp.setMaximumWidth(105)
        r1.addWidget(lab("Length"))
        r1.addWidget(self.sp_length)
        r1.addWidget(lab("Width"))
        r1.addWidget(self.sp_width)
        r1.addWidget(lab("Overlap"))
        r1.addWidget(self.sp_overlap)
        r1.addStretch()
        self.btn_focus = QPushButton("Focus")
        self.btn_focus.setToolTip("Zoom the 3D view onto this subset")
        self.btn_focus.clicked.connect(cb.subset_focus)
        self.btn_done = QPushButton("✓ Done")
        self.btn_done.setToolTip("Mark this subset as reviewed (Ctrl+M)")
        self.btn_done.clicked.connect(cb.subset_mark_done)
        r1.addWidget(self.btn_focus)
        r1.addWidget(self.btn_done)
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("Close the subset editor")
        close.clicked.connect(cb.subset_close)
        r1.addWidget(close)
        v.addLayout(r1)

        # row 2: reject / accept, display
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        self.btn_reject = QPushButton(" Reject")
        self.btn_reject.setIcon(icon("trash", "#ff6b6b"))
        self.btn_reject.setProperty("danger", True)
        self.btn_reject.setToolTip("Reject the selected points (Delete)")
        self.btn_reject.clicked.connect(cb.reject_selected)
        self.cmb_reject = QComboBox()
        self.cmb_reject.addItem("as Class 7 (low noise)", ("class", 7))
        self.cmb_reject.addItem("as Class 18 (high noise)", ("class", 18))
        self.cmb_reject.addItem("as another class…", ("class", None))
        self.cmb_reject.addItem("to Trash", ("trash", None))
        self.cmb_reject.setToolTip("LAS / LAZ: flag the points with a class (kept in the file; ASPRS 7 = low noise,\n"
                                   "18 = high noise) — or remove them to the Trash (non-LAS files always use Trash).")
        self.cmb_reject.activated.connect(lambda i: cb.set_reject_mode(*self.cmb_reject.itemData(i)))
        self.btn_accept = QPushButton("Accept")
        self.btn_accept.setToolTip("Restore the selected rejected points to their original class")
        self.btn_accept.clicked.connect(cb.accept_selected)
        self.btn_filter = QPushButton("Filter…")
        self.btn_filter.setToolTip("Auto filter the points of this subset (statistical / median / surface)")
        self.btn_filter.clicked.connect(lambda: cb.open_filter_dialog("subset"))
        self.chk_noise = QCheckBox("Show rejected")
        self.chk_noise.toggled.connect(cb.set_show_noise)
        self.chk_ref = QCheckBox("Reference surface")
        self.chk_ref.setChecked(True)
        self.chk_ref.toggled.connect(self._toggle_ref)
        self.sp_vex = QDoubleSpinBox(minimum=0.01, maximum=1e4, value=1.0, singleStep=1, decimals=1)
        self.sp_vex.setSuffix(" ×")
        self.sp_vex.setKeyboardTracking(False)
        btn_fit = QPushButton("Fit")
        for w in (self.btn_reject, self.cmb_reject, self.btn_accept, self.btn_filter):
            r2.addWidget(w)
        r2.addSpacing(10)
        r2.addWidget(self.chk_noise)
        r2.addWidget(self.chk_ref)
        r2.addStretch()
        r2.addWidget(lab("V.exag"))
        r2.addWidget(self.sp_vex)
        r2.addWidget(btn_fit)
        v.addLayout(r2)

        self.plot = SubsetPlot(cb.subset_select)
        v.addWidget(self.plot, 1)
        self.lbl = QLabel(" ")
        self.lbl.setObjectName("muted")
        v.addWidget(self.lbl)
        self.sp_vex.valueChanged.connect(self.plot.set_vex)
        self.plot.vex_changed = self._sync_vex
        btn_fit.clicked.connect(lambda: (self.plot.fit(), self._sync_vex()))
        self.plot.cursor_moved.connect(self._on_cursor)
        self._status = ""
        self.set_tool("box")

    def set_tool(self, tool):
        self.tool_btns[tool].setChecked(True)
        self.plot.set_tool(tool)

    def _toggle_ref(self, on):
        self.plot.show["tin"] = on
        self.plot.show["dem"] = on
        self.plot.update()

    def _sync_vex(self):
        self.sp_vex.blockSignals(True)
        self.sp_vex.setValue(self.plot.vex)
        self.sp_vex.blockSignals(False)

    def set_reject_options(self, can_classify, mode, cls=7):
        self.cmb_reject.blockSignals(True)
        if self.cmb_reject.count() > 4:                     # drop a previously added custom entry
            self.cmb_reject.removeItem(4)
        for k in range(3):
            self.cmb_reject.model().item(k).setEnabled(can_classify)
        if not can_classify or mode == "trash":
            self.cmb_reject.setCurrentIndex(3)
        elif cls in (7, 18):
            self.cmb_reject.setCurrentIndex(0 if cls == 7 else 1)
        else:
            self.cmb_reject.addItem(f"as Class {cls}", ("class", cls))
            self.cmb_reject.setCurrentIndex(4)
        self.cmb_reject.blockSignals(False)
        self.btn_accept.setEnabled(can_classify)
        self.chk_noise.setEnabled(can_classify)
    def show_points(self, x, z, idx, colors, selected_mask, ref, depth, fit, xlabel):
        cols = colors.copy()
        if selected_mask is not None and selected_mask.any():
            cols[selected_mask] = SELECTION_RGBA
        self.plot.idx = idx
        self.plot.depth = depth
        tin = ref.get("tin") if ref else None
        dem = ref.get("dem") if ref else None
        self.plot.set_data(x, z, cols, tin, dem, fit=fit)
        self.plot.xlabel = xlabel
        if fit:
            self._sync_vex()

    def set_status(self, text):
        self._status = text
        self.lbl.setText(text)

    def _on_cursor(self, pt):
        if pt is None:
            self.lbl.setText(self._status)
            return
        c, z = pt
        zl = f"depth {-z:,.3f}" if self.plot.depth else f"Z {z:,.3f}"
        self.lbl.setText(f"{self._status}     ·  x {c:,.2f} m · {zl} m")
