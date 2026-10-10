"""Data panel: every dataset of the project (point clouds, DEMs, TINs) with visibility, edit target, actions."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QMenu, QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from core import crs as crsmod
from core.project import KIND_LABEL
from ui.icons import icon
from ui.theme import C
from ui.widgets import Card, muted

KIND_COLOR = {"points": "#4c8dff", "grid": "#3ecf8e", "tin": "#ffd166", "route": "#4cc9f0", "contour": "#c9ced6"}


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
        self.chk_shade.setVisible(any(d.kind in ("grid", "tin") for d in project.datasets))

    def _row(self, ds, editing, outdated):
        cb = self.cb
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        top = QHBoxLayout()
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
        menu_btn = QToolButton()
        menu_btn.setText("⋯")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(menu_btn)
        m.addAction(icon("fit"), "Zoom to", lambda i=ds.id: cb.ds_zoom(i))
        if ds.kind == "route":
            m.addAction(icon("section"), "Section along route", lambda i=ds.id: cb.route_section(i))
            m.addAction(icon("subset"), "Subset editor along route", lambda i=ds.id: cb.route_subset(i))
        else:
            m.addAction(icon("export"), "Export…", lambda i=ds.id: cb.ds_export(i))
        if ds.kind in ("points", "grid"):
            a = m.addAction("Z values are depths (flip sign)")
            a.setCheckable(True)
            a.setChecked(bool(getattr(ds, "z_flipped", False)) or bool(getattr(ds.obj, "source", {}).get("z_is_depth"))
                         if hasattr(ds.obj, "source") else bool(getattr(ds, "z_flipped", False)))
            a.setToolTip("Use when the file stores depths as positive numbers (down +)")
            a.triggered.connect(lambda _=False, i=ds.id: cb.ds_flip_z(i))
        if ds.kind == "tin":
            a = m.addAction("Wireframe")
            a.setCheckable(True)
            a.toggled.connect(lambda on, i=ds.id: cb.ds_wire(i, on))
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
        if editing:
            info += " · editing"
        lab = muted(info)
        lab.setWordWrap(True)
        lab.setContentsMargins(26, 0, 0, 0)
        v.addWidget(lab)
        if ds.kind in ("grid", "tin"):
            orow = QHBoxLayout()
            orow.setContentsMargins(26, 2, 0, 0)
            orow.addWidget(muted("Opacity"))
            sl = QSlider(Qt.Horizontal, minimum=10, maximum=100, value=int(ds.opacity * 100))
            sl.valueChanged.connect(lambda val, i=ds.id: cb.ds_opacity(i, val / 100))
            orow.addWidget(sl, 1)
            v.addLayout(orow)
        return w
