"""Reusable sidebar widgets."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from ui.icons import icon


def muted(text):
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    return lbl


class Card(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 16)
        self.body.setSpacing(12)
        self.header = QHBoxLayout()
        self.title = QLabel(title.upper())
        self.title.setObjectName("sectionTitle")
        self.header.addWidget(self.title)
        self.header.addStretch()
        self.body.addLayout(self.header)


class KeyValueGrid(QGridLayout):
    def __init__(self, empty_text="—"):
        super().__init__()
        self.empty_text = empty_text
        self.setHorizontalSpacing(14)
        self.setVerticalSpacing(7)
        self.setColumnStretch(1, 1)
        self.rows = []
        self.set_rows(None)

    def set_rows(self, rows):
        while self.count():
            w = self.takeAt(0).widget()
            if w:
                w.hide()          # deleteLater alone leaves it painted until the next event loop pass
                w.setParent(None)
                w.deleteLater()
        self.rows = list(rows or [])
        if not self.rows:
            self.addWidget(muted(self.empty_text), 0, 0, 1, 2)
            return
        for r, (k, v) in enumerate(self.rows):
            kl = muted(k)
            kl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            vl = QLabel(v)
            vl.setWordWrap(True)
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.addWidget(kl, r, 0)
            self.addWidget(vl, r, 1)

    def as_text(self):
        return "\n".join(f"{k}\t{v}" for k, v in self.rows)


class FileInfoCard(Card):
    def __init__(self):
        super().__init__("File")
        self.grid = KeyValueGrid("No file loaded")
        self.body.addLayout(self.grid)

    def set_rows(self, rows):
        self.grid.set_rows(rows)


class SelectionCard(Card):
    """Shown only while something is selected."""

    def __init__(self, on_invert, on_clear, on_delete):
        super().__init__("Selection")
        self.badge = QLabel()
        self.badge.setObjectName("chipAccent")
        self.header.addWidget(self.badge)
        self.grid = KeyValueGrid()
        self.body.addLayout(self.grid)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_copy = QPushButton(" Copy")
        self.btn_copy.setIcon(icon("copy"))
        self.btn_copy.setToolTip("Copy these values to the clipboard (tab-separated)")
        self.btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.grid.as_text()))
        btn_inv = QPushButton("Invert")
        btn_inv.setToolTip("Invert selection (Ctrl+I)")
        btn_inv.clicked.connect(on_invert)
        btn_clear = QPushButton("Clear")
        btn_clear.setToolTip("Clear selection (Esc)")
        btn_clear.clicked.connect(on_clear)
        for b in (self.btn_copy, btn_inv, btn_clear):
            row.addWidget(b)
        self.body.addLayout(row)
        self.btn_delete = QPushButton(" Delete to Trash")
        self.btn_delete.setIcon(icon("trash", "#ff6b6b"))
        self.btn_delete.setProperty("danger", True)
        self.btn_delete.setToolTip("Move selected points to the trash (Delete)")
        self.btn_delete.clicked.connect(on_delete)
        self.body.addWidget(self.btn_delete)
        self.setVisible(False)

    def update_info(self, count, rows):
        if count == 0:
            self.setVisible(False)
            return
        self.title.setText("SELECTED POINT" if count == 1 else "SELECTION")
        self.badge.setText(f"{count:,} pt" + ("" if count == 1 else "s"))
        self.grid.set_rows(rows)
        self.setVisible(True)


class TrashCard(Card):
    """Lists delete batches; preview, restore, or permanently empty them."""

    def __init__(self, on_restore, on_restore_all, on_empty, on_purge_ids, on_preview):
        super().__init__("Trash")
        from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QListWidget, QMenu
        self._on_preview = on_preview
        self._on_purge_ids = on_purge_ids

        self.badge = QLabel()
        self.badge.setObjectName("chipDanger")
        self.header.addWidget(self.badge)

        self.empty = muted("Trash is empty. Deleted points are kept here\nuntil you empty the trash.")
        self.body.addWidget(self.empty)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self._QMenu = QMenu
        self.body.addWidget(self.list)

        self.chk_show = QCheckBox("Show deleted points in view")
        self.chk_show.toggled.connect(lambda _: on_preview())
        self.body.addWidget(self.chk_show)
        self.hint = muted("Tip: click a batch to highlight it in orange.")
        self.body.addWidget(self.hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_restore = QPushButton(" Restore")
        self.btn_restore.setIcon(icon("restore"))
        self.btn_restore.setToolTip("Restore selected batches")
        self.btn_restore.clicked.connect(on_restore)
        self.btn_restore_all = QPushButton("Restore all")
        self.btn_restore_all.clicked.connect(on_restore_all)
        self.btn_empty = QPushButton("Empty…")
        self.btn_empty.setProperty("danger", True)
        self.btn_empty.setToolTip("Permanently remove everything in the trash")
        self.btn_empty.clicked.connect(on_empty)
        for b in (self.btn_restore, self.btn_restore_all, self.btn_empty):
            row.addWidget(b)
        self.body.addLayout(row)
        self.refresh(None)

    # ---------- state ----------
    def selected_ids(self):
        return [it.data(Qt.UserRole) for it in self.list.selectedItems()]

    def refresh(self, trash):
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QListWidgetItem, QWidget
        keep = set(self.selected_ids())
        self.list.blockSignals(True)
        self.list.clear()
        batches = trash.sorted_batches() if trash is not None else []
        for b in batches:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, b.id)
            w = QWidget()
            w.setAttribute(Qt.WA_TranslucentBackground)
            h = QHBoxLayout(w)
            h.setContentsMargins(10, 6, 10, 6)
            left = QVBoxLayout()
            left.setSpacing(1)
            left.addWidget(QLabel(f"<b>#{b.id}</b>  {b.note}"))
            left.addWidget(muted(b.created.strftime("%H:%M:%S")))
            h.addLayout(left)
            h.addStretch()
            h.addWidget(QLabel(f"{b.count:,} pts"))
            it.setSizeHint(QSize(0, 50))
            self.list.addItem(it)
            self.list.setItemWidget(it, w)
            if b.id in keep:
                it.setSelected(True)
        self.list.blockSignals(False)
        self.list.setFixedHeight(min(len(batches), 5) * 52 + 4)

        has = bool(batches)
        total = trash.total if trash is not None else 0
        self.badge.setText(f"{total:,} pts")
        self.badge.setVisible(has)
        self.empty.setVisible(not has)
        for wdg in (self.list, self.chk_show, self.hint):
            wdg.setVisible(has)
        self.btn_restore_all.setEnabled(has)
        self.btn_empty.setEnabled(has)
        self._update_buttons()

    def _update_buttons(self):
        self.btn_restore.setEnabled(bool(self.selected_ids()))

    def _selection_changed(self):
        self._update_buttons()
        self._on_preview()

    def _context_menu(self, pos):
        ids = self.selected_ids()
        if not ids:
            return
        menu = self._QMenu(self)
        menu.addAction(icon("restore"), "Restore", self.btn_restore.click)
        menu.addSeparator()
        menu.addAction(icon("trash"), "Delete permanently…", lambda: self._on_purge_ids(ids))
        menu.exec(self.list.mapToGlobal(pos))


def _gradient_icon(lut, w=56, h=12):
    import numpy as np
    from PySide6.QtGui import QIcon, QImage, QPixmap
    rgba = (np.clip(lut, 0, 1) * 255).astype(np.uint8)
    img = QImage(rgba.tobytes(), len(rgba), 1, len(rgba) * 4, QImage.Format_RGBA8888).copy()
    return QIcon(QPixmap.fromImage(img.scaled(w, h)))


class ColorScaleCard(Card):
    """Palette, value range (elevation or depth), out-of-range handling, color bar toggle."""

    def __init__(self, cb):
        """cb: object with on_palette(name), on_reverse(bool), on_range(lo_text_value, hi_value),
        on_auto(), on_full(), on_out(mode), on_depth(bool), on_show_bar(bool)"""
        super().__init__("Color scale")
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout

        from render.colormap import PALETTES, get_lut
        self.cb = cb
        self.badge = QLabel()
        self.badge.setObjectName("chip")
        self.header.addWidget(self.badge)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.cmb_palette = QComboBox()
        self.cmb_palette.setIconSize(QSize(56, 12))
        for name in PALETTES:
            self.cmb_palette.addItem(_gradient_icon(get_lut(name)), name)
        self.cmb_palette.currentTextChanged.connect(cb.on_palette)

        self.chk_reverse = QCheckBox("Reverse")
        self.chk_reverse.toggled.connect(cb.on_reverse)

        self.cmb_axis = QComboBox()
        self.cmb_axis.addItem("Elevation  (Z, up +)", False)
        self.cmb_axis.addItem("Depth  (−Z, down +)", True)
        self.cmb_axis.currentIndexChanged.connect(lambda: cb.on_depth(self.cmb_axis.currentData()))
        self.lbl_axis = muted("Values as")

        self.spin_a = QDoubleSpinBox()
        self.spin_b = QDoubleSpinBox()
        for sp in (self.spin_a, self.spin_b):
            sp.setRange(-1e9, 1e9)
            sp.setDecimals(2)
            sp.setKeyboardTracking(False)   # apply on Enter / arrows / focus-out
            sp.valueChanged.connect(self._range_edited)
        self.lbl_a = muted("Max")
        self.lbl_b = muted("Min")

        self.cmb_out = QComboBox()
        self.cmb_out.addItem("Clamp to end colors", "clamp")
        self.cmb_out.addItem("Gray out", "gray")
        self.cmb_out.currentIndexChanged.connect(lambda: cb.on_out(self.cmb_out.currentData()))

        form.addRow(muted("Palette"), self.cmb_palette)
        form.addRow(muted(""), self.chk_reverse)
        form.addRow(self.lbl_axis, self.cmb_axis)
        form.addRow(self.lbl_a, self.spin_a)
        form.addRow(self.lbl_b, self.spin_b)
        form.addRow(muted("Out of range"), self.cmb_out)
        self.body.addLayout(form)

        self.lbl_data = muted("")
        self.body.addWidget(self.lbl_data)

        drow = QHBoxLayout()
        self.lbl_disp = QLabel("")
        self.lbl_disp.setWordWrap(True)
        self.lbl_disp.setToolTip("Display range = the colour-bar handles. Drag them on the colour bar; "
                                 "the scale (Shallow / Deep) stays fixed.")
        self.btn_reset_disp = QPushButton("Reset")
        self.btn_reset_disp.setToolTip("Handles back to the full scale")
        self.btn_reset_disp.clicked.connect(cb.on_reset_display)
        drow.addWidget(self.lbl_disp, 1)
        drow.addWidget(self.btn_reset_disp)
        self.body.addLayout(drow)

        row = QHBoxLayout()
        row.setSpacing(8)
        b_auto = QPushButton("Auto 2–98%")
        b_auto.setToolTip("Scale = data range ignoring the 2% extremes at each end")
        b_auto.clicked.connect(cb.on_auto)
        b_full = QPushButton("Full range")
        b_full.setToolTip("Scale = data minimum to maximum")
        b_full.clicked.connect(cb.on_full)
        row.addWidget(b_auto)
        row.addWidget(b_full)
        self.body.addLayout(row)

        self.chk_bar = QCheckBox("Show color bar in view")
        self.chk_bar.setChecked(True)
        self.chk_bar.toggled.connect(cb.on_show_bar)
        self.body.addWidget(self.chk_bar)
        self._sync_args = None

    def sync(self, mode, sc, depth, data_range):
        """Update widgets from state without emitting change callbacks."""
        widgets = (self.cmb_palette, self.chk_reverse, self.cmb_axis, self.spin_a, self.spin_b, self.cmb_out)
        for w in widgets:
            w.blockSignals(True)
        self._sync_args = (mode, depth)
        self.cmb_palette.setCurrentText(sc.palette)
        self.chk_reverse.setChecked(sc.reverse)
        is_elev = mode == "elevation"
        self.cmb_axis.setVisible(is_elev)
        self.lbl_axis.setVisible(is_elev)
        self.cmb_axis.setCurrentIndex(1 if depth and is_elev else 0)
        self.cmb_out.setCurrentIndex(self.cmb_out.findData(sc.out_of_range))

        span = max(abs(sc.amax - sc.amin), 1e-9)
        import math
        step = 10 ** math.floor(math.log10(span / 20))
        dec = min(6, max(2, -math.floor(math.log10(step)) + 1))
        unit = " m" if is_elev else ""
        for sp in (self.spin_a, self.spin_b):
            sp.setDecimals(dec)
            sp.setSingleStep(step)
            sp.setSuffix(unit)
        if is_elev and depth:
            self.lbl_a.setText("Shallow (top)")
            self.lbl_b.setText("Deep (bottom)")
            self.spin_a.setValue(-sc.amax)
            self.spin_b.setValue(-sc.amin)
            disp = f"Display: {-sc.vmax:,.2f} → {-sc.vmin:,.2f} m"
            lo, hi = -data_range[1], -data_range[0]
            self.lbl_data.setText(f"Data depth: {lo:,.2f} → {hi:,.2f} m")
        else:
            self.lbl_a.setText("Max (top)")
            self.lbl_b.setText("Min (bottom)")
            self.spin_a.setValue(sc.amax)
            self.spin_b.setValue(sc.amin)
            disp = f"Display: {sc.vmin:,.2f} → {sc.vmax:,.2f}{unit}"
            self.lbl_data.setText(f"Data range: {data_range[0]:,.2f} → {data_range[1]:,.2f}{unit}")
        self.badge.setText("Auto" if sc.auto else "Manual")
        narrowed = abs(sc.vmin - sc.amin) > 1e-9 or abs(sc.vmax - sc.amax) > 1e-9
        self.lbl_disp.setText(f"<span style='color:{'#4c8dff' if narrowed else '#8b919c'}'>{disp}"
                              f"{'' if narrowed else '  (full scale)'}</span>")
        self.btn_reset_disp.setEnabled(narrowed)
        for w in widgets:
            w.blockSignals(False)

    def _range_edited(self):
        mode, depth = self._sync_args or ("elevation", False)
        a, b = self.spin_a.value(), self.spin_b.value()
        if mode == "elevation" and depth:
            vmax, vmin = -a, -b          # shallow / deep (positive down) → Z
        else:
            vmax, vmin = a, b
        self.cb.on_axis(vmin, vmax)


class LayersCard(Card):
    """Point cloud / TIN / DEM layers: visibility, opacity, per-layer actions."""

    def __init__(self, cb):
        """cb provides: set_layer_visible(name, on), set_layer_opacity(name, a), set_wireframe(on),
        set_hillshade(on), create_tin(), create_dem(), export_tin(), export_dem(), remove_surface(kind)"""
        super().__init__("Layers")
        from PySide6.QtWidgets import QCheckBox, QMenu, QSlider, QToolButton, QWidget
        self.cb = cb
        self.rows = {}
        for name, label in (("points", "Point cloud"), ("tin", "TIN"), ("dem", "DEM")):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            top = QHBoxLayout()
            chk = QCheckBox(label)
            chk.setChecked(True)
            chk.toggled.connect(lambda on, n=name: cb.set_layer_visible(n, on))
            top.addWidget(chk)
            badge = QLabel("Out of date")
            badge.setObjectName("chipDanger")
            badge.setToolTip("Points were edited after this surface was built — rebuild to update")
            badge.setVisible(False)
            top.addWidget(badge)
            top.addStretch()
            slider = None
            if name != "points":
                btn = QToolButton()
                btn.setText("⋯")
                btn.setToolTip("Layer actions")
                btn.setPopupMode(QToolButton.InstantPopup)
                menu = QMenu(btn)
                menu.addAction(icon("export"), "Export…", cb.export_tin if name == "tin" else cb.export_dem)
                if name == "tin":
                    self.act_wire = menu.addAction("Wireframe")
                    self.act_wire.setCheckable(True)
                    self.act_wire.toggled.connect(cb.set_wireframe)
                menu.addAction("Rebuild…", cb.create_tin if name == "tin" else cb.create_dem)
                menu.addSeparator()
                menu.addAction(icon("trash"), "Remove", lambda n=name: cb.remove_surface(n))
                btn.setMenu(menu)
                top.addWidget(btn)
            v.addLayout(top)
            info = muted("")
            info.setWordWrap(True)
            info.setContentsMargins(26, 0, 0, 0)
            v.addWidget(info)
            if name != "points":
                orow = QHBoxLayout()
                orow.setContentsMargins(26, 2, 0, 0)
                orow.addWidget(muted("Opacity"))
                slider = QSlider(Qt.Horizontal, minimum=10, maximum=100, value=100)
                slider.valueChanged.connect(lambda val, n=name: cb.set_layer_opacity(n, val / 100))
                orow.addWidget(slider, 1)
                v.addLayout(orow)
            self.body.addWidget(w)
            self.rows[name] = dict(widget=w, chk=chk, info=info, badge=badge, slider=slider)

        self.chk_shade = QCheckBox("Hillshade surfaces")
        self.chk_shade.setChecked(True)
        self.chk_shade.toggled.connect(cb.set_hillshade)
        self.body.addWidget(self.chk_shade)
        row = QHBoxLayout()
        row.setSpacing(8)
        b1 = QPushButton("Create TIN…")
        b1.clicked.connect(cb.create_tin)
        b2 = QPushButton("Create DEM…")
        b2.clicked.connect(cb.create_dem)
        row.addWidget(b1)
        row.addWidget(b2)
        self.body.addLayout(row)
        for n in ("tin", "dem"):
            self.set_layer(n, None)

    def set_points_info(self, text):
        self.rows["points"]["info"].setText(text)

    def set_layer(self, name, info, outdated=False):
        r = self.rows[name]
        r["widget"].setVisible(info is not None)
        if info is not None:
            r["info"].setText(info)
            r["chk"].blockSignals(True)
            r["chk"].setChecked(True)
            r["chk"].blockSignals(False)
            if r["slider"] is not None:
                r["slider"].blockSignals(True)
                r["slider"].setValue(100)
                r["slider"].blockSignals(False)
        r["badge"].setVisible(bool(info is not None and outdated))
        self.chk_shade.setVisible(any(self.rows[n]["widget"].isVisible() for n in ("tin", "dem")))

    def set_outdated(self, name, outdated):
        self.rows[name]["badge"].setVisible(outdated and self.rows[name]["widget"].isVisible())
