"""Data panel: every dataset of the project (point clouds, DEMs, TINs) with visibility, edit target, actions."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSlider, QToolButton, QVBoxLayout,
    QWidget,
)

from core import crs as crsmod
from core.project import KIND_LABEL
from ui.icons import icon
from ui.theme import C
from ui.widgets import Card, muted

KIND_COLOR = {"points": "#4c8dff", "grid": "#3ecf8e", "tin": "#ffd166", "route": "#4cc9f0", "contour": "#c9ced6",
              "structure": "#ff9f43", "vector": "#c77dff", "mesh": "#f78fb3"}


LAYER_MIME = "application/x-pointcloudstudio-layer"


class _Grip(QLabel):
    """Drag handle of a layer row: press and drag to move the layer up or down the list."""

    def __init__(self, ds_id, row, card):
        super().__init__()
        self.ds_id = ds_id
        self.row = row
        self.card = card
        self._press = None
        self.setFixedSize(12, 18)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag to change the layer order (top of the list is drawn in front)")

    def paintEvent(self, e):
        # 2 × 3 dots drawn directly (no font glyph needed — same look on Windows, macOS and Linux)
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["muted"]))
        cx, cy = self.width() / 2, self.height() / 2
        for dx in (-2.5, 2.5):
            for dy in (-5, 0, 5):
                p.drawEllipse(QPoint(int(round(cx + dx)), int(round(cy + dy))), 1, 1)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._press = None
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        md = QMimeData()
        md.setData(LAYER_MIME, str(self.ds_id).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(md)
        pm = self.row.grab()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(12, pm.height() // 2))
        self.row.setEnabled(False)                       # dim the row being moved
        card = self.card
        card._pending_move = None
        card._dragging = True
        try:
            drag.exec(Qt.MoveAction)
        finally:
            card._dragging = False
        # the drop only records the move; the panel is rebuilt after the drag has completely finished
        # (rebuilding inside the drag's own event loop would delete this row while it is still in use)
        self.row.setEnabled(True)
        self.setCursor(Qt.OpenHandCursor)
        if card._pending_move is not None:
            ds_id, gap = card._pending_move
            card._pending_move = None
            QTimer.singleShot(0, lambda: card.cb.ds_move(ds_id, to=gap))


class DataCard(Card):
    """cb: ds_visible(id,on) ds_opacity(id,a) ds_edit(id,on) ds_zoom(id) ds_export(id) ds_remove(id)
    ds_wire(id,on) add_data() create_tin() create_dem() set_hillshade(on) set_project_crs()"""

    def __init__(self, cb):
        super().__init__("Data")
        self.cb = cb
        self.lbl_crs = QLabel()
        self.lbl_crs.setObjectName("muted")
        self.lbl_crs.setWordWrap(True)
        self.lbl_crs.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.addWidget(self.lbl_crs)
        self.lbl_lock = QLabel()
        self.lbl_lock.setWordWrap(True)
        self.body.addWidget(self.lbl_lock)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(10)
        self.body.addLayout(self.rows_box)
        # drag and drop of layer rows (grip ⠿): a line shows where the layer will go
        self.setAcceptDrops(True)
        self._drop_line = QFrame(self)
        self._drop_line.setFixedHeight(3)
        self._drop_line.setStyleSheet(f"background:{C['accent']}; border-radius:1px")
        self._drop_line.hide()
        self._drop_gap = None
        self._pending_move = None
        self.lbl_draw = QLabel("DRAWINGS")
        self.lbl_draw.setObjectName("sectionTitle")
        self.lbl_draw.setToolTip("Lines and areas drawn in the 3D view — tick to show / hide")
        self.body.addWidget(self.lbl_draw)
        self.draw_box = QVBoxLayout()
        self.draw_box.setSpacing(6)
        self.body.addLayout(self.draw_box)
        self._draw_widgets = []
        self.chk_shade = QCheckBox("Hillshade surfaces")
        self.chk_shade.setChecked(True)
        self.chk_shade.toggled.connect(cb.set_hillshade)
        self.body.addWidget(self.chk_shade)
        from PySide6.QtCore import QSettings
        self.chk_order = QCheckBox("Draw in layer order (top layer in front)")
        self.chk_order.setToolTip("On: a layer higher in the list is drawn in front of the layers below it, "
                                  "wherever they overlap on screen (▲ ▼ change the order).\n"
                                  "Off: true 3D depth — whatever is nearer to the eye is in front.")
        on = QSettings("PointCloudStudio", "PointCloudStudio").value("view/draw_in_layer_order", True)
        self.chk_order.setChecked(on in (True, "true", "1", 1))
        cb._draw_in_order = self.chk_order.isChecked()
        self.chk_order.toggled.connect(cb.set_draw_in_order)
        self.body.addWidget(self.chk_order)
        r1 = QHBoxLayout()
        b = QPushButton(" Add data…")
        b.setIcon(icon("add"))
        b.setToolTip("Add point clouds or DEM grids to the project (Ctrl+Shift+O)")
        b.clicked.connect(cb.add_data)
        r1.addWidget(b)
        self.body.addLayout(r1)
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        bt = QPushButton("Create TIN…")
        bt.clicked.connect(cb.create_tin)
        bd = QPushButton("Create DEM…")
        bd.clicked.connect(cb.create_dem)
        r2.addWidget(bt)
        r2.addWidget(bd)
        self.body.addLayout(r2)
        self._widgets = []

    def refresh_drawings(self, drawings):
        """drawings: [dict(key, name, info, visible, color, actions=[(label, callback)])]"""
        for w in self._draw_widgets:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        self._draw_widgets = []
        self.lbl_draw.setVisible(bool(drawings))
        for d in drawings:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(1)
            top = QHBoxLayout()
            chk = QCheckBox()
            chk.setText(chk.fontMetrics().elidedText(d["name"], Qt.ElideMiddle, 150))
            chk.setToolTip(d["name"])
            chk.setChecked(d["visible"])
            chk.toggled.connect(lambda on, k=d["key"]: self.cb.drawing_visible(k, on))
            top.addWidget(chk, 1)
            sw = QLabel("━")
            sw.setStyleSheet(f"color:{d['color']}; font-weight:700")
            top.addWidget(sw)
            if d.get("actions"):
                mb = QToolButton()
                mb.setText("⋯")
                mb.setPopupMode(QToolButton.InstantPopup)
                m = QMenu(mb)
                for label, fn in d["actions"]:
                    m.addAction(label, fn)
                mb.setMenu(m)
                top.addWidget(mb)
            v.addLayout(top)
            if d.get("info"):
                lab = muted(d["info"])
                lab.setContentsMargins(26, 0, 0, 0)
                v.addWidget(lab)
            self.draw_box.addWidget(w)
            self._draw_widgets.append(w)

    def refresh(self, project, outdated_ids=()):
        for w in self._widgets:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        self._widgets = []
        self.lbl_crs.setText(f"Coordinate system: <b>{crsmod.label(project.crs)}</b>")
        pts = project.points
        if len(pts) >= 2 and project.edit_id is None:
            self.lbl_lock.setText(f"<span style='color:{C['muted']}'>🔒 Editing is locked (several point clouds). "
                                  f"Click <b>Edit</b> on one cloud to edit it.</span>")
            self.lbl_lock.show()
        elif not pts and project.datasets:
            self.lbl_lock.setText(f"<span style='color:{C['muted']}'>🔒 No point cloud — editing is off.</span>")
            self.lbl_lock.show()
        else:
            self.lbl_lock.hide()
        for ds in project.datasets:
            w = self._row(ds, ds.id == project.edit_id, ds.id in outdated_ids)
            self.rows_box.addWidget(w)
            self._widgets.append(w)
        self.chk_shade.setVisible(any(d.kind in ("grid", "tin", "mesh") for d in project.datasets))
        self.chk_order.setVisible(sum(d.kind in ("points", "grid", "tin", "mesh") for d in project.datasets) >= 2)
        n = len(project.datasets)
        for k, w in enumerate(self._widgets):
            up, down = getattr(w, "_btn_up", None), getattr(w, "_btn_down", None)
            if up is not None:
                up.setEnabled(k > 0)
                down.setEnabled(k < n - 1)

    # ---------- drag and drop of layers ----------
    def _gap_at(self, y):
        """Insertion gap (0…n) for a drop at card y: before the first row whose middle is below y."""
        rows = [w for w in self._widgets if getattr(w, "_ds_id", None) is not None]
        for k, w in enumerate(rows):
            g = w.geometry()
            top = w.mapTo(self, QPoint(0, 0)).y()
            if y < top + g.height() / 2:
                return k, rows
        return len(rows), rows

    def _show_drop_line(self, gap, rows):
        if not rows:
            return
        if gap < len(rows):
            y = rows[gap].mapTo(self, QPoint(0, 0)).y() - 6
        else:
            last = rows[-1]
            y = last.mapTo(self, QPoint(0, 0)).y() + last.height() + 4
        x0 = rows[0].mapTo(self, QPoint(0, 0)).x()
        self._drop_line.setGeometry(x0, max(0, y), rows[0].width(), 3)
        self._drop_line.show()
        self._drop_line.raise_()

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(LAYER_MIME):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if not e.mimeData().hasFormat(LAYER_MIME):
            e.ignore()
            return
        gap, rows = self._gap_at(e.position().y())
        self._drop_gap = gap
        self._show_drop_line(gap, rows)
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drop_line.hide()
        self._drop_gap = None

    def dropEvent(self, e):
        self._drop_line.hide()
        if not e.mimeData().hasFormat(LAYER_MIME):
            e.ignore()
            return
        try:
            ds_id = int(bytes(e.mimeData().data(LAYER_MIME)).decode("ascii"))
        except ValueError:
            e.ignore()
            return
        gap, _ = self._gap_at(e.position().y())
        e.acceptProposedAction()
        self._pending_move = (ds_id, gap)                # applied by the grip once QDrag.exec has returned
        QTimer.singleShot(150, self._apply_pending_move)  # fallback if the platform delivers the drop late

    def _apply_pending_move(self):
        if getattr(self, "_dragging", False):
            QTimer.singleShot(100, self._apply_pending_move)
            return
        if self._pending_move is not None:
            ds_id, gap = self._pending_move
            self._pending_move = None
            self.cb.ds_move(ds_id, to=gap)

    def _row(self, ds, editing, outdated):
        cb = self.cb
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        top = QHBoxLayout()
        w._ds_id = ds.id
        top.addWidget(_Grip(ds.id, w, self))
        chk = QCheckBox()
        name = chk.fontMetrics().elidedText(ds.name, Qt.ElideMiddle, 130)   # long names must not widen the panel
        chk.setText(name)
        chk.setChecked(ds.visible)
        chk.setToolTip(ds.name + (f"\n{ds.path}" if ds.path else ""))
        chk.setMinimumWidth(0)
        chk.toggled.connect(lambda on, i=ds.id: cb.ds_visible(i, on))
        top.addWidget(chk, 1)
        kind = QLabel(KIND_LABEL[ds.kind])
        kind.setStyleSheet(f"color:{KIND_COLOR[ds.kind]}; border:1px solid {KIND_COLOR[ds.kind]}; "
                           f"border-radius:7px; padding:0 6px; font-size:10px; font-weight:700")
        top.addWidget(kind)
        if outdated:
            b = QLabel("Out of date")
            b.setObjectName("chipDanger")
            b.setToolTip("The source point cloud was edited after this surface was built — rebuild to update")
            top.addWidget(b)
        if ds.kind == "points":
            e = QToolButton()
            e.setText("✎ Edit")
            e.setCheckable(True)
            e.setChecked(editing)
            e.setToolTip("Make this point cloud the edit target (selection, delete, reject)")
            e.setStyleSheet(f"QToolButton:checked {{ background:{C['accent_dim']}; border-color:{C['accent']}; }}")
            e.toggled.connect(lambda on, i=ds.id: cb.ds_edit(i, on))
            top.addWidget(e)
        btn_up, btn_dn = QToolButton(), QToolButton()
        for b, txt, tip, d in ((btn_up, "▲", "Move layer up (drawn in front of the layers below)", -1),
                               (btn_dn, "▼", "Move layer down", +1)):
            b.setText(txt)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.setFixedWidth(22)
            b.clicked.connect(lambda _=False, i=ds.id, dd=d: cb.ds_move(i, dd))
            top.addWidget(b)
        w._btn_up, w._btn_down = btn_up, btn_dn
        menu_btn = QToolButton()
        menu_btn.setText("⋯")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(menu_btn)
        m.addAction(icon("fit"), "Zoom to", lambda i=ds.id: cb.ds_zoom(i))
        if ds.kind == "route":
            m.addAction(icon("section"), "Section along route", lambda i=ds.id: cb.route_section(i))
            m.addAction(icon("subset"), "Subset editor along route", lambda i=ds.id: cb.route_subset(i))
        elif ds.kind == "structure":
            m.addAction("Scour monitoring…", lambda: cb.open_seabed_dialog(0))
        elif ds.kind == "vector":
            if ds.obj.polygons():
                m.addAction("Use a polygon as analysis area…", lambda i=ds.id: cb.vector_use_polygon(i))
            if ds.obj.lines():
                m.addAction("Make routes from lines…", lambda i=ds.id: cb.vector_to_routes(i))
            m.addAction(icon("export"), "Export as…", lambda i=ds.id: cb.vector_export(i))
        else:
            m.addAction(icon("export"), "Export…", lambda i=ds.id: cb.ds_export(i))
        if ds.kind in ("points", "grid"):
            a = m.addAction("Z values are depths (flip sign)")
            a.setCheckable(True)
            a.setChecked(bool(getattr(ds, "z_flipped", False)) or bool(getattr(ds.obj, "source", {}).get("z_is_depth"))
                         if hasattr(ds.obj, "source") else bool(getattr(ds, "z_flipped", False)))
            a.setToolTip("Use when the file stores depths as positive numbers (down +)")
            a.triggered.connect(lambda _=False, i=ds.id: cb.ds_flip_z(i))
        if ds.kind in ("points", "grid", "tin"):
            m.addAction("Survey date…", lambda i=ds.id: cb.ds_set_date(i))
        if ds.kind == "mesh":
            m.addAction("Mesh / model information…", lambda i=ds.id: cb.mesh_info(i))
        if ds.kind in ("tin", "mesh"):
            a = m.addAction("Wireframe")
            a.setCheckable(True)
            a.toggled.connect(lambda on, i=ds.id: cb.ds_wire(i, on))
        m.addSeparator()
        m.addAction("Move to top", lambda i=ds.id: cb.ds_move(i, to="top"))
        m.addAction("Move to bottom", lambda i=ds.id: cb.ds_move(i, to="bottom"))
        m.addSeparator()
        m.addAction(icon("trash"), "Remove from project", lambda i=ds.id: cb.ds_remove(i))
        menu_btn.setMenu(m)
        top.addWidget(menu_btn)
        v.addLayout(top)
        info = ds.size_text
        if ds.source_crs is not None and ds.crs is not None and crsmod.same(ds.source_crs, ds.crs) is False:
            info += f" · reprojected from EPSG:{crsmod.epsg_of(ds.source_crs) or '?'}"
        if getattr(ds, "z_flipped", False) or (hasattr(ds.obj, "source") and ds.obj.source.get("z_is_depth")):
            info += " · Z from depths"
        if getattr(ds, "survey_date", None):
            info += (" · simulated " if getattr(ds, "simulated", False) else " · surveyed ") + ds.survey_date
        if editing:
            info += " · editing"
        lab = muted(info)
        lab.setWordWrap(True)
        lab.setContentsMargins(26, 0, 0, 0)
        v.addWidget(lab)
        if ds.kind in ("grid", "tin", "mesh"):
            orow = QHBoxLayout()
            orow.setContentsMargins(26, 2, 0, 0)
            orow.addWidget(muted("Opacity"))
            sl = QSlider(Qt.Horizontal, minimum=10, maximum=100, value=int(ds.opacity * 100))
            sl.valueChanged.connect(lambda val, i=ds.id: cb.ds_opacity(i, val / 100))
            orow.addWidget(sl, 1)
            v.addLayout(orow)
        return w
