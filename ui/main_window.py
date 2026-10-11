"""Main window."""
from __future__ import annotations

import os
import time
import traceback

import numpy as np

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QProgressDialog, QPushButton, QScrollArea, QSlider, QStackedWidget,
    QToolBar, QVBoxLayout, QWidget,
)

from core import profile as prof
from core.history import History
from core.surface import DEM_METHODS, build_dem, build_tin, mean_spacing
from core.selection import Selection, describe
from core.trash import ClassifyCommand, DeleteCommand, RestoreCommand, Trash

from fileio.las_reader import read_las
from fileio.surface_io import DEM_FORMATS, TIN_FORMATS, export_dem, export_tin
from fileio.writers import export as write_export
from fileio.xyz_reader import DELIMITERS, read_xyz
from render.colorbar import ColorBarOverlay
from render.colormap import MODE_LABELS, SCALAR_MODES, available_modes, compute_colors, map_scalar, scalar_values
from render.interaction import SelectionController
from render.picking import pick_point, select_polygon
from render.viewer import PointCloudViewer
from ui.icons import icon, point_cloud_pixmap
from ui.about_dialog import AboutDialog, ShortcutsDialog
from ui.data_panel import DataCard
from ui.project_mixin import ALL_FILTER, ProjectFileMixin, ProjectMixin
from ui.analysis_mixin import AnalysisMixin, ContourMixin, DesignMixin
from ui.subset_mixin import ClassifyMixin, FilterMixin, RouteMixin, SubsetMixin
from ui.export_dialog import ExportDialog
from ui.import_dialog import XyzImportDialog
from ui.profile_panel import ProfilePanel
from ui.surface_dialogs import DemDialog, TinDialog
from ui.theme import dark_titlebar
from ui.widgets import (Card, ColorScaleCard, FileInfoCard, SelectionCard, TrashCard,
                        muted as _muted)

from core.platform_util import key_text  # noqa: E402
from core.version import APP_NAME, __version__  # noqa: E402
MODE_HINTS = {
    "navigate": "Navigate · Left drag rotate · Right drag / wheel zoom · Shift+Left or Middle drag pan",
    "pick": "Pick · Click a point · Shift+click add · Ctrl+click remove · Wheel zoom · Middle drag pan · V to rotate",
    "box": "Box select · Drag a rectangle · Shift add · Ctrl subtract · Wheel zoom · Middle drag pan · V to rotate",
    "section": "Section · Click to add vertices · Double-click or Enter to finish · Backspace removes last · Esc cancels · Drag a vertex to edit",
    "subset": "Subset · Drag the long axis of a subset box in plan view (width in the Subset panel) · Esc cancels",
    "lasso": "Lasso select · Draw a freehand outline · Shift add · Ctrl subtract · Wheel zoom · Middle drag pan · V to rotate",
}
XYZ_EXT = (".xyz", ".asc", ".txt", ".csv", ".pts")
LAS_EXT = (".las", ".laz")
FILE_FILTER = ("Point clouds (*.xyz *.asc *.txt *.csv *.pts *.las *.laz);;"
               "LAS / LAZ (*.las *.laz);;XYZ / ASC (*.xyz *.asc *.txt *.csv *.pts);;All files (*)")


# ---------------------------------------------------------------- helpers
class _Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(float)

    def __init__(self, fn, *args, report_progress=False, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        if report_progress:
            self.kwargs["progress"] = self.progress.emit

    @Slot()
    def run(self):
        try:
            self.finished.emit(self.fn(*self.args, **self.kwargs))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")


class EmptyState(QWidget):
    def __init__(self, on_open):
        super().__init__()
        self.setObjectName("emptyState")
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.addStretch(3)
        art = QLabel()
        art.setPixmap(point_cloud_pixmap(140))
        art.setAlignment(Qt.AlignCenter)
        title = QLabel("No point cloud loaded")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        hint = _muted("Open a file or drag & drop it anywhere in this window\n"
                      "LAS · LAZ · XYZ · ASC · TXT · CSV · PTS")
        hint.setAlignment(Qt.AlignCenter)
        btn = QPushButton("  Open File…")
        btn.setIcon(icon("open", "#ffffff"))
        btn.setProperty("primary", True)
        btn.setFixedWidth(170)
        btn.clicked.connect(on_open)
        for w in (art, title, hint):
            v.addWidget(w)
        v.addSpacing(10)
        v.addWidget(btn, 0, Qt.AlignCenter)
        v.addSpacing(6)
        v.addWidget(_muted("Ctrl+O"), 0, Qt.AlignCenter)
        v.addStretch(4)


# ---------------------------------------------------------------- main window
class MainWindow(ClassifyMixin, ContourMixin, DesignMixin, AnalysisMixin, RouteMixin, FilterMixin, SubsetMixin, ProjectFileMixin, ProjectMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1480, 920)
        self.pc = None
        self._thread = None
        self._worker = None
        self._progress = None

        self.sel = None
        self.trash = None
        self.history = History()
        self._sel_methods = []
        self._dirty = False
        self._init_project_state()
        self._init_subset_state()
        self.filter_dialog = None
        self._init_analysis_state()
        self.region_visible = self.subset_visible = self.reviewed_visible = True
        self._filter_saved = None
        self.sections = []
        self.active_section = -1
        self.viewer = PointCloudViewer()
        self.selector = SelectionController(self.viewer, self._on_select_request)
        self.depth_mode = False
        self.colorbar = ColorBarOverlay(self.viewer.widget, self._on_bar_change,
                                        lambda: self.viewer.reset_display())
        self._recolor_timer = QTimer(self, singleShot=True, interval=60, timeout=self.viewer.recolor)
        self.viewer.on_scale_changed = self._on_scale_changed
        self.selector.on_section_draft = self._on_section_draft
        self.selector.on_section_finish = self._on_section_finish
        self.selector.get_section_vertices = (
            lambda: (self.sections[self.active_section].xy
                     if self.active_section >= 0 and self.sections[self.active_section].kp is None else None))
        self.selector.on_vertex_moved = self._on_vertex_moved
        self.selector.on_subset_draft = self._on_subset_draft
        self.selector.on_subset_finish = self._on_subset_finish
        self.stack = QStackedWidget()
        self.stack.addWidget(EmptyState(self.open_file))
        self.stack.addWidget(self.viewer.widget)
        self.setCentralWidget(self.stack)

        self._build_actions()
        self._build_toolbar()
        self._build_sidebar()
        self._build_status()
        self.setAcceptDrops(True)
        self._set_enabled(False)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    # ---------- UI construction ----------
    def _build_actions(self):
        self.act_open = QAction(icon("open"), "Open", self, shortcut=QKeySequence.Open,
                                triggered=self.open_file, toolTip="Open point cloud (Ctrl+O)")
        self.act_add = QAction(icon("add"), "Add Data…", self, shortcut="Ctrl+Shift+O",
                               triggered=self.add_data, toolTip="Add point clouds or DEM grids to the project")
        self.act_route = QAction(icon("section"), "Import Route…", self, shortcut="Ctrl+R", triggered=self.import_route,
                                 toolTip="Import a cable route (RPL CSV / TXT or Shapefile)")
        self.act_save = QAction("Save Project", self, shortcut=QKeySequence.Save, triggered=self.save_project)
        self.act_save_as = QAction("Save Project As…", self, shortcut="Ctrl+Shift+S", triggered=self.save_project_as)
        self.act_export = QAction(icon("export"), "Export…", self, shortcut="Ctrl+E",
                                  triggered=self.export_file, toolTip="Export point cloud (Ctrl+E)")
        self.act_quit = QAction("Exit", self, shortcut=QKeySequence.Quit, triggered=self.close)
        self.view_actions = {}
        for key, label, sc in (("top", "Top", "1"), ("front", "Front", "2"),
                               ("side", "Side", "3"), ("iso", "Iso", "4")):
            a = QAction(icon(key), label, self, shortcut=sc, toolTip=f"{label} view ({sc})")
            a.triggered.connect(lambda _=False, k=key: self.viewer.set_view(k))
            self.view_actions[key] = a
        self.act_fit = QAction(icon("fit"), "Fit", self, shortcut="F",
                               triggered=self.viewer.fit_view, toolTip="Zoom to extent (F)")

        self.mode_group = QActionGroup(self)
        self.mode_actions = {}
        for key, label, sc, tip in (("navigate", "Navigate", "V", "Rotate / zoom / pan"),
                                    ("pick", "Pick", "P", "Select a single point"),
                                    ("box", "Box", "B", "Rectangle selection"),
                                    ("lasso", "Lasso", "L", "Freehand selection"),
                                    ("section", "Section", "S", "Draw a cross-section line"),
                                    ("subset", "Subset", "U", "Subset editor: drag a box, edit side views, page along")):
            a = QAction(icon(key), label, self, shortcut=sc, checkable=True,
                        toolTip=f"{label} ({sc}) — {tip}")
            a.triggered.connect(lambda _=False, k=key: self.set_mode(k))
            self.mode_group.addAction(a)
            self.mode_actions[key] = a
        self.mode_actions["navigate"].setChecked(True)

        self.act_sel_all = QAction("Select All", self, shortcut=QKeySequence.SelectAll,
                                   triggered=self.select_all)
        self.act_invert = QAction("Invert Selection", self, shortcut="Ctrl+I",
                                  triggered=self.invert_selection)
        self.act_clear = QAction("Clear Selection", self, shortcut="Esc",
                                 triggered=self._on_escape)

        self.act_delete = QAction(icon("trash", "#ff6b6b"), "Reject", self, triggered=self.reject_selected,
                                  toolTip="Reject the selected points (Delete) — LAS: reject class (7 / 18 / …) or Trash, "
                                          "set in the Subset editor; other formats: Trash")
        self.act_delete.setShortcuts([QKeySequence.Delete, QKeySequence("Backspace")])
        self.act_undo = QAction(icon("undo"), "Undo", self, shortcut=QKeySequence.Undo,
                                triggered=self.undo)
        self.act_redo = QAction(icon("redo"), "Redo", self, triggered=self.redo)
        self.act_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.act_restore_all = QAction(icon("restore"), "Restore All from Trash", self,
                                       triggered=self.restore_all)
        self.act_empty = QAction("Empty Trash…", self, triggered=self.empty_trash)
        self.act_accept = QAction("Accept (restore rejected points)", self, triggered=self.accept_selected)
        self.act_filter = QAction("Auto Filter…", self, shortcut="Ctrl+F", triggered=lambda: self.open_filter_dialog())
        self.act_classify = QAction("Classify Points (Ground / Vegetation / Buildings)…", self, shortcut="Ctrl+K",
                                    triggered=self.open_classify_dialog)
        self.act_page_next = QAction("Next Subset", self, shortcut="PgDown", triggered=lambda: self.subset_page(1))
        self.act_page_prev = QAction("Previous Subset", self, shortcut="PgUp", triggered=lambda: self.subset_page(-1))
        self.act_mark_done = QAction("Mark Subset Complete", self, shortcut="Ctrl+M", triggered=self.subset_mark_done)
        for a in (self.act_page_next, self.act_page_prev, self.act_mark_done):
            self.addAction(a)

        self.act_finish_sec = QAction("Finish Section", self, triggered=self.selector.finish_section)
        self.act_finish_sec.setShortcuts([QKeySequence("Return"), QKeySequence("Enter")])
        self.addAction(self.act_finish_sec)
        self.act_volume = QAction("Volume / Difference…", self, shortcut="Ctrl+Shift+V",
                                  triggered=self.open_volume_dialog)
        self.act_c2s = QAction("Point Cloud to Surface…", self, triggered=self.compare_points_to_surface)
        self.act_design = QAction("Earthworks Design (Cut / Fill / Trench)…", self, shortcut="Ctrl+Shift+D",
                                  triggered=self.open_design_dialog)
        self.act_slope = QAction("Slope Map…", self, triggered=self.open_slope_map)
        self.act_contours = QAction("Contours…", self, shortcut="Ctrl+Shift+C", triggered=self.open_contour_dialog)
        self.act_tin = QAction("Create TIN…", self, shortcut="Ctrl+T", triggered=self.create_tin)
        self.act_dem = QAction("Create DEM…", self, shortcut="Ctrl+G", triggered=self.create_dem)
        self.act_exp_tin = QAction(icon("export"), "Export TIN…", self, triggered=self.export_tin)
        self.act_exp_dem = QAction(icon("export"), "Export DEM…", self, triggered=self.export_dem)

        m_file = self.menuBar().addMenu("&File")
        m_file.addAction(self.act_open)
        m_file.addAction(self.act_add)
        m_file.addAction(self.act_route)
        m_file.addSeparator()
        m_file.addAction(self.act_save)
        m_file.addAction(self.act_save_as)
        m_file.addSeparator()
        m_file.addAction(self.act_export)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)
        m_view = self.menuBar().addMenu("&View")
        for a in self.view_actions.values():
            m_view.addAction(a)
        m_view.addSeparator()
        m_view.addAction(self.act_fit)
        m_edit = self.menuBar().addMenu("&Edit")
        for a in (self.act_undo, self.act_redo):
            m_edit.addAction(a)
        m_edit.addSeparator()
        m_edit.addAction(self.act_delete)
        m_edit.addAction(self.act_accept)
        m_edit.addSeparator()
        m_edit.addAction(self.act_filter)
        m_edit.addAction(self.act_classify)
        m_edit.addSeparator()
        m_edit.addAction(self.act_restore_all)
        m_edit.addAction(self.act_empty)
        self.menuBar().insertMenu(m_view.menuAction(), m_edit)
        m_surf = self.menuBar().addMenu("S&urface")
        for a in (self.act_tin, self.act_dem):
            m_surf.addAction(a)
        m_surf.addSeparator()
        for a in (self.act_exp_tin, self.act_exp_dem):
            m_surf.addAction(a)
        m_view.addSeparator()
        self._m_view = m_view
        m_an = self.menuBar().addMenu("&Analysis")
        m_an.addAction(self.act_volume)
        m_an.addAction(self.act_c2s)
        m_an.addSeparator()
        m_an.addAction(self.act_design)
        m_an.addAction(self.act_slope)
        m_an.addAction(self.act_contours)
        m_sel = self.menuBar().addMenu("&Select")
        self._m_sel = m_sel
        for a in self.mode_actions.values():
            m_sel.addAction(a)
        m_sel.addSeparator()
        for a in (self.act_sel_all, self.act_invert, self.act_clear):
            m_sel.addAction(a)
        m_help = self.menuBar().addMenu("&Help")
        m_help.addAction("Keyboard Shortcuts", lambda: ShortcutsDialog(self).exec(), "F1")
        m_help.addSeparator()
        m_help.addAction(f"About {APP_NAME}", lambda: AboutDialog(self).exec())

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        tb.addAction(self.act_open)
        tb.addAction(self.act_add)
        tb.addAction(self.act_export)
        tb.addSeparator()
        for a in self.view_actions.values():
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_fit)
        for a in list(self.view_actions.values()) + [self.act_fit]:   # compact: icon only (tooltips + 1-4 / F)
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.addSeparator()
        for a in self.mode_actions.values():
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_delete)
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        for a in (self.act_add, self.act_export, self.act_undo, self.act_redo):   # compact: icon only
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonIconOnly)

    def _build_sidebar(self):
        side = QWidget()
        side.setObjectName("sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(12)

        self.sel_card = SelectionCard(self.invert_selection, self.clear_selection, self.reject_selected)
        v.addWidget(self.sel_card)
        self.trash_card = TrashCard(self.restore_selected_batches, self.restore_all,
                                    self.empty_trash, self.purge_batches, self._update_trash_view)
        self.trash_card.setVisible(False)
        v.addWidget(self.trash_card)
        self.data_card = DataCard(self)
        self.data_card.setVisible(False)
        v.addWidget(self.data_card)

        # display card
        disp = Card("Display")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.cmb_color = QComboBox()
        self.cmb_color.currentIndexChanged.connect(
            lambda: self.viewer.set_color_mode(self.cmb_color.currentData()))

        size_row = QHBoxLayout()
        self.sld_size = QSlider(Qt.Horizontal, minimum=1, maximum=40, value=4)
        self.lbl_size = QLabel("2.0 px")
        self.lbl_size.setMinimumWidth(48)
        self.lbl_size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.sld_size.valueChanged.connect(self._on_point_size)
        size_row.addWidget(self.sld_size)
        size_row.addWidget(self.lbl_size)

        self.spin_z = QDoubleSpinBox(minimum=0.1, maximum=100, singleStep=0.5, value=1.0)
        self.spin_z.setSuffix(" ×")
        self.spin_z.valueChanged.connect(self.viewer.set_z_scale)

        self.spin_max = QDoubleSpinBox(minimum=0.1, maximum=500, singleStep=1, value=10, decimals=1)
        self.spin_max.setSuffix(" M pts")
        self.spin_max.setToolTip("Above this count the display is evenly decimated.\n"
                                 "Selection and deletion always use every point.")
        self.spin_max.editingFinished.connect(self._on_max_display)

        form.addRow(_muted("Color by"), self.cmb_color)
        form.addRow(_muted("Point size"), size_row)
        form.addRow(_muted("Z exaggeration"), self.spin_z)
        form.addRow(_muted("Display limit"), self.spin_max)
        disp.body.addLayout(form)
        self._display_widgets = [self.cmb_color, self.sld_size, self.spin_z, self.spin_max]

        # navigation card
        self.scale_card = ColorScaleCard(self)
        self.scale_card.setVisible(False)

        nav = Card("Navigation", closable=True, on_close=lambda: self.act_nav.setChecked(False))
        self.nav_card = nav
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for r, (k, d) in enumerate((("Left drag", "Rotate"), ("Right drag / Wheel", "Zoom"),
                                    ("Middle / Shift+Left", "Pan"), ("1 · 2 · 3 · 4", "Top · Front · Side · Iso"),
                                    ("F", "Fit to extent"), ("V · P · B · L", "Navigate · Pick · Box · Lasso"),
                                    ("Shift / Ctrl", "Add / subtract selection"),
                                    ("Ctrl+A · Ctrl+I · Esc", "All · Invert · Clear"),
                                    ("Delete", "Reject selection (class / trash)"),
                                    ("U · PgUp · PgDn", "Subset · previous · next page"),
                                    ("Ctrl+Z · Ctrl+Y", "Undo · Redo"))):
            grid.addWidget(QLabel(key_text(k)), r, 0)
            grid.addWidget(_muted(d), r, 1)
        nav.body.addLayout(grid)

        self.file_card = FileInfoCard()

        v.addWidget(disp)
        v.addWidget(self.scale_card)
        v.addWidget(nav)
        v.addWidget(self.file_card)
        v.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dock = QDockWidget(self)
        dock.setTitleBarWidget(QWidget())
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        dock.setWidget(scroll)
        dock.setMinimumWidth(330)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        self.profile = ProfilePanel(self)
        self.profile_dock = QDockWidget("Section profile", self)
        self.profile_dock.setObjectName("profileDock")
        self.profile_dock.setWidget(self.profile)
        self.profile_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.profile_dock)
        self.resizeDocks([self.profile_dock], [300], Qt.Vertical)
        self.profile_dock.hide()
        self._build_subset_dock()
        from PySide6.QtCore import QSettings
        self.act_nav = QAction("Navigation Help", self, checkable=True)
        self.act_nav.setChecked(QSettings("PointCloudStudio", "PointCloudStudio").value("ui/show_navigation", True, bool))
        self.act_nav.toggled.connect(self._set_nav_visible)
        self._m_view.addAction(self.act_nav)
        from PySide6.QtGui import QActionGroup
        from core.route import DIST_LABEL
        m_dist = self._m_view.addMenu("Distance Label")
        grp = QActionGroup(self)
        saved = QSettings("PointCloudStudio", "PointCloudStudio").value("ui/dist_label", "KP")
        DIST_LABEL[0] = saved if saved in ("KP", "Chainage") else "KP"
        for lab, tip in (("KP", "Kilometre point — cables, pipelines offshore"),
                         ("Chainage", "Chainage — roads, railways, land pipelines")):
            a = QAction(lab, self, checkable=True)
            a.setToolTip(tip)
            a.setChecked(DIST_LABEL[0] == lab)
            a.triggered.connect(lambda _=False, l=lab: self._set_dist_label(l))
            grp.addAction(a)
            m_dist.addAction(a)
        self._set_nav_visible(self.act_nav.isChecked())
        act = self.profile_dock.toggleViewAction()
        act.setText("Section Profile Panel")
        self._m_view.addAction(act)

    def _build_status(self):
        self.chips = {}
        for key in ("selected", "noise", "trash", "total", "active", "shown"):
            lbl = QLabel()
            lbl.setObjectName({"selected": "chipAccent", "trash": "chipDanger", "noise": "chipDanger"}.get(key, "chip"))
            lbl.setVisible(False)
            self.statusBar().addPermanentWidget(lbl)
            self.chips[key] = lbl
        self.mode_label = QLabel()
        self.mode_label.setObjectName("muted")
        self.statusBar().addWidget(self.mode_label, 1)
        # temporary messages and the mode hint share the left side: show one at a time
        self.statusBar().messageChanged.connect(lambda m: self.mode_label.setVisible(not m))

    def _set_enabled(self, on):
        for w in self._display_widgets:
            w.setEnabled(on)
        for a in (list(self.view_actions.values()) + list(self.mode_actions.values())
                  + [self.act_fit, self.act_sel_all, self.act_invert, self.act_clear, self.act_export,
                     self.act_tin, self.act_dem]):
            a.setEnabled(on)
        self._update_edit_actions()
        self._update_surface_actions()

    # ---------- display handlers ----------
    def _on_point_size(self, v):
        size = v / 2.0
        self.lbl_size.setText(f"{size:.1f} px")
        self.viewer.set_point_size(size)

    def _on_max_display(self):
        self.viewer.set_max_display(int(self.spin_max.value() * 1_000_000))
        self._update_chips()

    def _update_chips(self):
        if self.pc is None:
            n = len(self.project.datasets)
            for k, lbl in self.chips.items():
                lbl.setVisible(k == "total" and n > 0)
                if k == "total":
                    lbl.setText(f"{n} dataset{'s' if n != 1 else ''} · editing locked")
            return
        n_sel = self.sel.count if self.sel is not None else 0
        n_trash = self.trash.total if self.trash is not None else 0
        n_noise = self.pc.n_noise if self.pc.can_classify else 0
        vals = {"selected": f"{n_sel:,} selected", "trash": f"{n_trash:,} in trash", "noise": f"{n_noise:,} rejected",
                "total": f"{len(self.pc):,} pts",
                "active": f"{self.pc.n_active:,} active",
                "shown": f"{len(self.viewer.display_idx):,} shown"}
        for k, lbl in self.chips.items():
            lbl.setText(vals[k])
            lbl.setVisible({"selected": n_sel > 0, "trash": n_trash > 0, "noise": n_noise > 0}.get(k, True))

    # ---------- file loading ----------


    def _run_loader(self, path, fn, *args, **kwargs):
        """Load as a NEW project (kept for scripts / tests; the UI uses load_path)."""
        self._pending = dict(path=path, add=False, opts={})
        self._run_task(f"Loading {os.path.basename(path)}…", fn, args, kwargs,
                       self._on_loaded, report_progress=False)

    def _run_task(self, label, fn, args, kwargs, on_done, report_progress):
        if self._thread is not None and self._thread.isRunning():
            return
        self._progress = QProgressDialog(label, "", 0, 1000 if report_progress else 0, self)
        self._progress.setCancelButton(None)
        self._progress.setWindowTitle(APP_NAME)
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setMinimumWidth(360)
        self._progress.show()
        self.statusBar().showMessage(label)

        self._t0 = time.perf_counter()
        self._thread = QThread(self)
        self._worker = _Worker(fn, *args, report_progress=report_progress, **kwargs)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # connect to bound methods (not lambdas) so the slots run on the GUI thread
        self._task_done_cb = on_done
        if report_progress:
            self._worker.progress.connect(self._on_task_progress)
        self._worker.finished.connect(self._on_task_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    @Slot(float)
    def _on_task_progress(self, f):
        if self._progress is not None:
            self._progress.setValue(int(f * 1000))

    @Slot(object)
    def _on_task_finished(self, result):
        self._task_done_cb(result)

    def _close_progress(self):
        if self._progress is not None:
            self._progress.close()
            self._progress = None


    @Slot(str)
    def _on_failed(self, msg):
        self._close_progress()
        self.statusBar().showMessage("Failed")
        title = "Export failed" if getattr(self, "_exporting", False) else "Load failed"
        self._exporting = False
        from core.deps import module_from_error
        mod = module_from_error(msg)
        if mod:
            from ui.deps_dialog import missing_module_box
            if missing_module_box(mod, self):
                return
        box = QMessageBox(QMessageBox.Critical, title, msg.split("\n\n")[0], parent=self)
        box.setDetailedText(msg)
        box.exec()

    # ---------- color scale ----------
    def _on_scale_changed(self, mode, sc):
        if sc is None or sc.vmin is None:
            self.scale_card.setVisible(False)
            self.colorbar.hide()
            return
        self.scale_card.setVisible(True)
        depth = self.depth_mode and mode == "elevation"
        self.scale_card.sync(mode, sc, depth, self.viewer.data_range())
        title = ("Depth" if depth else "Elevation") if mode == "elevation" else MODE_LABELS[mode]
        self.colorbar.set_state(self._bar_lut(sc), sc.amin, sc.amax, sc.vmin, sc.vmax, title,
                                unit="m" if mode == "elevation" else "", depth=depth,
                                gray_out=sc.out_of_range == "gray")
        self.colorbar.setVisible(self.scale_card.chk_bar.isChecked())
        self._refresh_profile_colors()
        if self.colorbar.isVisible():
            self.colorbar.raise_()

    def _on_bar_change(self, lo, hi, final):
        sc = self.viewer.scale
        if sc is None:
            return
        if final:
            self._recolor_timer.stop()
            self.viewer.set_color_scale(vmin=lo, vmax=hi)   # recolor + full UI sync
        else:  # live drag: update numbers now, recolor at most every 60 ms
            sc.vmin, sc.vmax = lo, hi            # display range only; the scale stays fixed
            depth = self.depth_mode and self.viewer.scale_mode == "elevation"
            self.scale_card.sync(self.viewer.scale_mode, sc, depth, self.viewer.data_range())
            if not self._recolor_timer.isActive():
                self._recolor_timer.start()

    # callbacks used by ColorScaleCard
    def on_palette(self, name):
        self.viewer.set_color_scale(palette=name)

    def on_reverse(self, on):
        self.viewer.set_color_scale(reverse=bool(on))

    def on_axis(self, amin, amax):
        """Shallow / Deep (or Min / Max) typed in the card → fixed scale of the colour bar."""
        self.viewer.set_color_scale(amin=amin, amax=amax)

    def on_reset_display(self):
        self.viewer.reset_display()

    def on_auto(self):
        self.viewer.reset_range("auto")

    def on_full(self):
        self.viewer.reset_range("full")

    def on_out(self, mode):
        self.viewer.set_color_scale(out_of_range=mode)

    def on_depth(self, on):
        self.depth_mode = bool(on)
        self._on_scale_changed(self.viewer.scale_mode, self.viewer.scale)
        if self.project.of_kind("contour"):
            self._show_contours(rebuild=True)          # labels as depth or elevation

    def on_show_bar(self, on):
        self._on_scale_changed(self.viewer.scale_mode, self.viewer.scale)

    # ---------- selection ----------
    def set_mode(self, mode):
        # the camera is left exactly as the user set it (no automatic switch to top view)
        self.mode_actions[mode].setChecked(True)
        self.selector.set_mode(mode)
        self.mode_label.setText(key_text(MODE_HINTS[mode]))

    def _on_select_request(self, kind, data, op):
        if self.pc is None:
            return
        method = {"pick": "Pick", "polygon": self.selector.mode.capitalize()}.get(kind)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            t0 = time.perf_counter()
            if kind == "clear":
                self.sel.clear()
            elif kind == "pick":
                i = pick_point(self.viewer, self.pc, data)
                if i is None:
                    if op == "replace":
                        self.sel.clear()
                else:
                    self.sel.apply([i], op)
            elif kind == "polygon":
                idx = select_polygon(self.viewer, self.pc, data)
                self.sel.apply(idx, op)
            dt = time.perf_counter() - t0
        finally:
            QApplication.restoreOverrideCursor()
        self._track_method(method, op)
        self._selection_changed()
        if kind == "polygon":
            self.statusBar().showMessage(f"Selection updated in {dt:.2f} s", 4000)

    def _selection_changed(self):
        idx = self.sel.indices()
        self.viewer.set_selection(idx)
        self.sel_card.update_info(len(idx), describe(self.pc, idx))
        self._update_chips()
        self._update_edit_actions()

    def _track_method(self, method, op):
        if self.sel.count == 0:
            self._sel_methods = []
        elif method:
            if op == "replace":
                self._sel_methods = [method]
            elif method not in self._sel_methods:
                self._sel_methods.append(method)

    def _selection_note(self):
        return " + ".join(self._sel_methods) or "Selection"

    def select_all(self):
        if self.pc is not None:
            self.sel.select_all(self.pc.deleted_batch == 0)
            self._sel_methods = ["All"]
            self._selection_changed()

    def invert_selection(self):
        if self.pc is not None:
            self.sel.invert(self.pc.deleted_batch == 0)
            self._sel_methods = ["Inverted " + self._selection_note()] if self.sel.count else []
            self._selection_changed()

    def clear_selection(self):
        if self.pc is not None and self.sel.count:
            self.sel.clear()
            self._sel_methods = []
            self._selection_changed()

    def _on_escape(self):
        if self.selector.section_drawing:
            self.selector.cancel()
        elif self.selector.drawing:
            self.selector.cancel()
        else:
            self.clear_selection()

    # ---------- delete / trash / undo ----------
    def delete_selected(self):
        if self.selector.section_drawing:
            self.selector.remove_last()
            return
        if self.pc is None or self.sel.count == 0:
            return
        if self.selector.drawing:
            return
        cmd = DeleteCommand(self.trash, self.sel.indices(), self._selection_note())
        self.history.push(cmd)
        self.sel.clear()
        self._sel_methods = []
        self._after_edit(f"Moved {cmd.batch.count:,} points to trash (batch #{cmd.batch.id}) · Ctrl+Z to undo")

    def undo(self):
        if self.pc is None:
            return
        cmd = self.history.undo()
        if cmd is None:
            return
        if isinstance(cmd, ClassifyCommand) and getattr(cmd, "reselect", True):
            self.sel.apply(cmd.idx[self.pc.active_mask()[cmd.idx]], "replace")
            self._sel_methods = ["Subset"]
        if isinstance(cmd, DeleteCommand):  # bring the points back *selected*, ready to adjust
            self.sel.apply(cmd.idx, "replace")
            self._sel_methods = [cmd.batch.note]
        self._after_edit(f"Undo: {cmd.label}")

    def redo(self):
        if self.pc is None:
            return
        cmd = self.history.redo()
        if cmd is not None:
            self._after_edit(f"Redo: {cmd.label}")

    def restore_selected_batches(self):
        ids = self.trash_card.selected_ids()
        if ids:
            self._restore(ids)

    def restore_all(self):
        if self.trash is not None and self.trash.batches:
            self._restore(list(self.trash.batches))

    def _restore(self, ids):
        cmd = RestoreCommand(self.trash, ids)
        if cmd.count == 0:
            return
        self.history.push(cmd)
        self._after_edit(f"Restored {cmd.count:,} points · Ctrl+Z to undo")

    def empty_trash(self):
        if self.trash is not None and self.trash.batches:
            self.purge_batches(list(self.trash.batches))

    def purge_batches(self, ids):
        n = sum(self.trash.batches[i].count for i in ids if i in self.trash.batches)
        if n == 0:
            return
        what = "everything in the trash" if set(ids) == set(self.trash.batches) else \
               f"{len(ids)} batch{'es' if len(ids) > 1 else ''}"
        box = QMessageBox(QMessageBox.Warning, "Delete permanently",
                          f"Permanently delete {what} ({n:,} points)?", parent=self)
        box.setInformativeText("These points can't be restored and will be excluded from export.\n"
                               "Undo history will be cleared.")
        btn = box.addButton("Delete permanently", QMessageBox.DestructiveRole)
        btn.setProperty("danger", True)
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is not btn:
            return
        self.trash.purge(ids)
        self.history.clear()
        self._after_edit(f"Permanently deleted {n:,} points")

    def _after_edit(self, message):
        """Refresh everything that depends on which points are active."""
        self.sel.mask &= self.pc.deleted_batch == 0
        if self.sel.count == 0:
            self._sel_methods = []
        self.viewer.refresh()
        self.trash_card.refresh(self.trash)
        self._update_trash_view()
        self._selection_changed()
        self._update_edit_actions()
        bds = self._bound_ds()
        if bds is not None:
            bds.edit_version += 1
        self._refresh_data_card()
        self._recompute_section()
        self._refresh_subset_if_open()
        self._set_dirty(True)
        self.statusBar().showMessage(key_text(message), 6000)

    def _update_trash_view(self):
        if self.pc is None:
            return
        db = self.pc.deleted_batch
        ids = self.trash_card.selected_ids()
        hi = np.isin(db, ids) if ids else np.zeros(len(db), bool)
        dim = (db > 0) & ~hi if self.trash_card.chk_show.isChecked() else np.zeros(len(db), bool)
        self.viewer.set_trash_view(np.flatnonzero(dim), np.flatnonzero(hi))

    def _update_edit_actions(self):
        loaded = self.pc is not None
        has_trash = loaded and self.trash is not None and bool(self.trash.batches)
        self.act_undo.setEnabled(loaded and bool(self.history.undo_label))
        self.act_redo.setEnabled(loaded and bool(self.history.redo_label))
        self.act_undo.setText(f"Undo {self.history.undo_label}".strip())
        self.act_redo.setText(f"Redo {self.history.redo_label}".strip())
        self.act_undo.setIconText("Undo")
        self.act_redo.setIconText("Redo")
        self.act_undo.setToolTip(f"Undo {self.history.undo_label} (Ctrl+Z)" if self.history.undo_label else "Nothing to undo")
        self.act_redo.setToolTip(f"Redo {self.history.redo_label} (Ctrl+Y)" if self.history.redo_label else "Nothing to redo")
        self.act_delete.setEnabled(loaded and ((self.sel is not None and self.sel.count > 0)
                                               or self.selector.section_drawing))
        self.act_restore_all.setEnabled(has_trash)
        self.act_empty.setEnabled(has_trash)

    # ---------- sections / profiles ----------
    def _overlay_sections(self, draft=None, cursor=None):
        vis = [i for i, sc in enumerate(self.sections) if sc.visible]
        active = vis.index(self.active_section) if self.active_section in vis else -1
        self.viewer.set_section_overlay([(self.sections[i].xy, self.sections[i].width) for i in vis], active,
                                        draft, cursor)

    def _on_section_draft(self, draft, cursor):
        self._overlay_sections(draft, cursor)
        self._update_edit_actions()   # Backspace removes the last vertex while drawing

    def _on_section_finish(self, pts):
        if self._poly_mode:
            return self._finish_polygon(pts)
        n = len(self.sections) + 1
        width = self.sections[self.active_section].width if self.active_section >= 0 else 1.0
        sec = prof.Section(f"Section {n}", np.asarray(pts, dtype=np.float64), width=width)
        self.sections.append(sec)
        self._set_dirty(True)
        self.active_section = len(self.sections) - 1
        self._show_dock(self.profile_dock)
        self._recompute_section(fit=True)
        self._fit_profile_later()
        self._refresh_drawings()
        self.statusBar().showMessage(f"{sec.name}: {sec.length:,.2f} m, {len(sec.result['idx']):,} points "
                                     f"within ±{sec.width:g} m", 6000)

    def _on_vertex_moved(self, i, xy, final):
        sec = self.sections[self.active_section]
        sec.xy[i] = xy
        self._overlay_sections()
        if final:
            self._recompute_section()

    def _project_path_stem(self, fallback):
        for d in self.project.datasets:
            if d.path:
                return d.path
        return fallback

    @staticmethod
    def _bar_lut(sc):
        """LUT drawn between the colour-bar handles (non-linear palettes are sampled through map_scalar)."""
        if sc.palette == "Topo-bathy" and sc.vmin is not None:
            return map_scalar(np.linspace(sc.vmin, sc.vmax, 256), sc)
        return sc.lut

    def _set_dist_label(self, label):
        from PySide6.QtCore import QSettings

        from core.route import DIST_LABEL
        DIST_LABEL[0] = label
        QSettings("PointCloudStudio", "PointCloudStudio").setValue("ui/dist_label", label)
        self._show_routes()
        self._recompute_section()
        self._refresh_subset_if_open()

    def _set_nav_visible(self, on):
        from PySide6.QtCore import QSettings
        self.nav_card.setVisible(bool(on))
        QSettings("PointCloudStudio", "PointCloudStudio").setValue("ui/show_navigation", bool(on))

    def _fit_profile_later(self):
        """Fit the profile once its dock has its real size (fitting a hidden dock gives a wrong V.exag)."""
        def fit():
            self.profile.plot.fit()
            self.profile._sync_vex()
        QTimer.singleShot(0, fit)

    def _profile_cloud(self):
        if self.pc is not None:
            return self.pc
        vis = self.project.of_kind("points", visible_only=True)
        return vis[0].obj if vis else None

    def _section_colors(self, idx):
        pc = self._profile_cloud()
        mode = self.viewer.color_mode if pc is self.pc else "elevation"
        if mode in SCALAR_MODES and self.viewer.scales[mode].vmin is not None:
            return map_scalar(scalar_values(pc, idx, mode), self.viewer.scales[mode])
        return compute_colors(pc, idx, mode)

    def _recompute_section(self, fit=False):
        pc = self._profile_cloud()
        if self.active_section < 0 or (pc is None and self.tin is None and self.dem is None):
            self._overlay_sections()
            return
        sec = self.sections[self.active_section]
        if pc is None:      # surfaces only: empty point set
            from core.pointcloud import PointCloud
            pc = PointCloud(np.array([[sec.xy[0, 0], sec.xy[0, 1], 0.0]]))
            pc.deleted_batch[:] = -1
        prof.compute(sec, pc, None, None)          # surfaces + other clouds come as coloured layers
        sec.result["layers"] = self._profile_layers(sec, pc)
        self.profile.set_sections([sc.name for sc in self.sections], self.active_section)
        depth = self.depth_mode and self.viewer.scale_mode in (None, "elevation")
        self.profile.show_section(sec, self._section_colors(sec.result["idx"]), depth, fit,
                                  sec.result.get("layers", []))
        self._overlay_sections()

    def _refresh_profile_colors(self):
        if self.active_section < 0 or not self.profile_dock.isVisible():
            return
        sec = self.sections[self.active_section]
        if sec.result:
            depth = self.depth_mode and self.viewer.scale_mode in (None, "elevation")
            self.profile.show_section(sec, self._section_colors(sec.result["idx"]), depth, False,
                                      sec.result.get("layers", []))

    # ProfilePanel callbacks
    def select_section(self, i):
        if 0 <= i < len(self.sections):
            self.active_section = i
            self._recompute_section(fit=True)

    def set_section_width(self, w):
        if self.active_section >= 0:
            self.sections[self.active_section].width = float(w)
            self._recompute_section()

    def set_section_start(self, v):
        if self.active_section >= 0:
            self.sections[self.active_section].start = float(v)
            self._recompute_section(fit=True)

    def delete_section(self):
        if self.active_section < 0:
            return
        del self.sections[self.active_section]
        self._set_dirty(True)
        self.active_section = len(self.sections) - 1
        if self.active_section < 0:
            self.profile_dock.hide()
            self._overlay_sections()
        else:
            self._recompute_section(fit=True)
        self._refresh_drawings()

    def export_section_csv(self):
        if self.active_section < 0:
            return
        sec = self.sections[self.active_section]
        stem = os.path.splitext(self._project_path_stem("section"))[0]
        path, _ = QFileDialog.getSaveFileName(self, "Export section", f"{stem}_{sec.name.replace(' ', '_')}.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        r = sec.result
        rows = []
        if len(r["idx"]):
            xy = self._profile_cloud().xyz[r["idx"], :2]
            rows.append(np.column_stack([np.zeros(len(xy)), sec.disp(r["chainage"]), xy, r["z"], r["offset"],
                                         r["idx"]]))
        names = ["point"]
        for lay in r.get("layers", []):
            c, z = lay["ch"], lay["z"]
            ok = np.isfinite(c) & np.isfinite(z)
            xy = sec.xy_at(c[ok])
            names.append(lay["name"].replace(",", " "))
            rows.append(np.column_stack([np.full(ok.sum(), len(names) - 1), c[ok], xy, z[ok],
                                         np.zeros(ok.sum()), np.full(ok.sum(), -1)]))
        data = np.vstack(rows) if rows else np.empty((0, 7))
        names = np.array(names)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# {sec.name}; length {sec.length:.3f} m; corridor ±{sec.width:g} m; start chainage {sec.start:g} m\n")
            f.write("# vertices: " + "; ".join(f"{x:.3f},{y:.3f}" for x, y in sec.xy) + "\n")
            f.write("type,chainage,x,y,z,offset,point_index\n")
            for row in data:
                f.write(f"{names[int(row[0])]},{row[1]:.3f},{row[2]:.3f},{row[3]:.3f},{row[4]:.3f},"
                        f"{row[5]:.3f},{int(row[6])}\n")
        self.statusBar().showMessage(f"Exported {os.path.basename(path)} ({len(data):,} rows)", 6000)

    def export_section_png(self):
        if self.active_section < 0:
            return
        sec = self.sections[self.active_section]
        stem = os.path.splitext(self._project_path_stem("section"))[0]
        path, _ = QFileDialog.getSaveFileName(self, "Save profile image",
                                              f"{stem}_{sec.name.replace(' ', '_')}.png", "PNG (*.png)")
        if path:
            self.profile.plot.grab().save(path)
            self.statusBar().showMessage(f"Saved {os.path.basename(path)}", 6000)

    # ---------- surfaces (TIN / DEM) ----------














    def _save_surface_path(self, title, formats, suffix):
        stem = os.path.splitext(self._project_path_stem("surface"))[0] + suffix
        filters = ";;".join(formats.values())
        path, flt = QFileDialog.getSaveFileName(self, title, stem + next(iter(formats)), filters)
        if not path:
            return None
        ext = os.path.splitext(path)[1].lower()
        if ext not in formats and ext not in (".tiff",):
            ext = next(k for k, v in formats.items() if v == flt)
            path += ext
        return path



    def _on_surface_exported(self, path):
        self._exporting = False
        self._close_progress()
        self.statusBar().showMessage(f"Exported {os.path.basename(path)}", 8000)

    # ---------- export ----------
    def export_file(self, ds=None):
        if ds is None or ds is False:
            ds = self._bound_ds() or self._pick_source_points("Export point cloud")
        if ds is None:
            return
        pc, sel = ds.obj, ds.sel
        db = pc.deleted_batch
        noise = pc.noise_mask() & (db == 0)
        counts = dict(active=int(np.count_nonzero(db == 0)), selected=sel.count if ds.id == self._bound_id else 0,
                      trash=int(np.count_nonzero(db > 0)), noise=int(np.count_nonzero(noise)))
        dlg = ExportDialog(pc, counts, self)
        if dlg.exec() != QDialog.Accepted:
            return
        job = dlg.job()
        targets = [job["path"]] + ([job["trash_path"]] if job["trash_path"] else [])
        src = os.path.abspath(pc.source.get("path", ""))
        clash = [t for t in targets if os.path.abspath(t) == src]
        if clash:
            r = QMessageBox.warning(self, "Overwrite source file?",
                                    "The output file is the file you opened. Overwrite it?",
                                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if r != QMessageBox.Yes:
                return
        content = {"active": db == 0, "selected": sel.mask, "trash": db > 0}[job["content"]]
        if job.get("exclude_noise"):
            content = content & ~noise
        tasks = [(np.flatnonzero(content), job["path"])]
        if job["trash_path"]:
            tasks.append((np.flatnonzero(db > 0), job["trash_path"]))
        self._export_job = job

        fmt, opts = job["fmt"], job["opts"]

        def run(progress):
            total = sum(len(i) for i, _ in tasks)
            done = 0
            for idx, path in tasks:
                base = done
                write_export(pc, idx, path, fmt, opts,
                             progress=lambda f, b=base, n=len(idx): progress((b + f * n) / max(total, 1)))
                done += len(idx)
            return [(len(i), p) for i, p in tasks]

        self._exporting = True
        self._run_task(f"Exporting {os.path.basename(job['path'])}…", run, (), {},
                       self._on_exported, report_progress=True)

    @Slot(object)
    def _on_exported(self, written):
        self._exporting = False
        self._close_progress()
        dt = time.perf_counter() - self._t0
        parts = [f"{n:,} pts → {os.path.basename(p)}" for n, p in written]
        self.statusBar().showMessage(f"Exported {' · '.join(parts)} in {dt:.1f} s", 10000)
        if self._export_job["content"] == "active":
            self._set_dirty(False)

    # ---------- unsaved changes ----------
    def _set_dirty(self, dirty):
        self._dirty = dirty
        names = [d.name for d in self.project.datasets] if hasattr(self, "project") else []
        if not names:
            self.setWindowTitle(APP_NAME)
            return
        main = names[0] + (f" (+{len(names) - 1})" if len(names) > 1 else "")
        if getattr(self, "project_path", None):
            main = os.path.splitext(os.path.basename(self.project_path))[0] + ".pcsproj"
        self.setWindowTitle(f"{'● ' if dirty else ''}{main} — {APP_NAME}")

    def _confirm_discard(self, action):
        if not self._dirty or not self.project.datasets:
            return True
        box = QMessageBox(QMessageBox.Warning, "Unsaved changes",
                          "The project has changes that have not been saved.", parent=self)
        box.setInformativeText(f"Save the project before you {action}?")
        btn_save = box.addButton("Save Project", QMessageBox.AcceptRole)
        btn_discard = box.addButton("Discard", QMessageBox.DestructiveRole)
        btn_discard.setProperty("danger", True)
        btn_discard.style().unpolish(btn_discard)
        btn_discard.style().polish(btn_discard)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_save:
            return bool(self.save_project())
        return clicked is btn_discard

    def closeEvent(self, e):
        if self._confirm_discard("quit"):
            e.accept()
        else:
            e.ignore()

    # ---------- drag & drop ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            paths = [u.toLocalFile() for u in urls]
            if self.project.datasets:
                self._load_queue = paths
                self._next_in_queue()
            else:
                self._load_queue = paths[1:]
                self.load_path(paths[0], add=False)

    # ---------- file info ----------
    @staticmethod
    def _info_rows(pc, load_time):
        s = pc.source
        mn, mx = pc.bbox_min, pc.bbox_max
        rows = [("Name", os.path.basename(s.get("path", "")))]
        if s.get("type") == "las":
            rows += [("Format", f"{'LAZ' if s['compressed'] else 'LAS'} {s['version']}"),
                     ("Point format", str(s["point_format"]))]
        elif s.get("type") == "xyz":
            rows += [("Format", "XYZ / ASC"), ("Encoding", s["encoding"]),
                     ("Start line", str(s["skip_rows"] + 1)),
                     ("Delimiter", DELIMITERS[s["delimiter"]][0]),
                     ("Columns", ", ".join(f"{i + 1}:{r}" for i, r in enumerate(s["roles"])))]
        rows += [
            ("Points", f"{len(pc):,}"),
            ("X", f"{mn[0]:,.3f}  →  {mx[0]:,.3f}"),
            ("Y", f"{mn[1]:,.3f}  →  {mx[1]:,.3f}"),
            ("Z", f"{mn[2]:,.3f}  →  {mx[2]:,.3f}"),
            ("Attributes", ", ".join(pc.attrs) or "—"),
            ("View origin", ", ".join(f"{v:,.0f}" for v in pc.offset)),
        ]
        if s.get("type") == "las":
            rows += [("Scale", ", ".join(f"{v:g}" for v in s["scales"])),
                     ("Offset", ", ".join(f"{v:g}" for v in s["offsets"])),
                     ("Dimensions", ", ".join(s["dimensions"])),
                     ("VLRs", "\n".join(s["vlrs"]) or "—")]
        if s.get("dropped_rows"):
            rows.append(("Skipped rows", f"{s['dropped_rows']:,}"))
        rows.append(("Load time", f"{load_time:.2f} s"))
        return rows
