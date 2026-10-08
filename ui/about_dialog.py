"""Help → About and Help → Keyboard Shortcuts."""
from __future__ import annotations

import platform

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from core.version import APP_NAME, AUTHOR, AUTHOR_EMAIL, __version__
from ui.icons import point_cloud_pixmap
from ui.theme import C, dark_titlebar


def _lib_versions():
    out = []
    for name, mod in (("PySide6", "PySide6"), ("VisPy", "vispy"), ("NumPy", "numpy"), ("SciPy", "scipy"),
                      ("pandas", "pandas"), ("laspy", "laspy"), ("rasterio", "rasterio"), ("pyproj", "pyproj")):
        try:
            m = __import__(mod)
            out.append(f"{name} {getattr(m, '__version__', '?')}")
        except Exception:
            out.append(f"{name} (not installed)")
    return out


class _Base(QDialog):
    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)


class AboutDialog(_Base):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 20)
        v.setSpacing(6)
        art = QLabel()
        art.setPixmap(point_cloud_pixmap(96))
        art.setAlignment(Qt.AlignCenter)
        v.addWidget(art)
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        v.addWidget(title)
        ver = QLabel(f"Version {__version__}")
        ver.setObjectName("muted")
        ver.setAlignment(Qt.AlignCenter)
        v.addWidget(ver)
        v.addSpacing(12)
        who = QLabel(f"Developed by <b>{AUTHOR}</b><br>"
                     f"<a href='mailto:{AUTHOR_EMAIL}' style='color:{C['accent']}; text-decoration:none'>"
                     f"{AUTHOR_EMAIL}</a>")
        who.setAlignment(Qt.AlignCenter)
        who.setOpenExternalLinks(True)
        who.setTextInteractionFlags(Qt.TextBrowserInteraction)
        v.addWidget(who)
        v.addSpacing(12)
        libs = QLabel("Built with " + " · ".join(_lib_versions()) +
                      f"<br>Python {platform.python_version()} · {platform.system()} {platform.release()}")
        libs.setObjectName("muted")
        libs.setWordWrap(True)
        libs.setAlignment(Qt.AlignCenter)
        v.addWidget(libs)
        v.addSpacing(10)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("Close")
        ok.setProperty("primary", True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        row.addStretch()
        v.addLayout(row)


SHORTCUTS = [
    ("File", [("Ctrl+O", "Open point cloud"), ("Ctrl+E", "Export point cloud"), ("Drag & drop", "Open a file")]),
    ("View", [("1 · 2 · 3 · 4", "Top · Front · Side · Iso view"), ("F", "Fit to extent"),
              ("Left drag", "Rotate (Navigate mode)"), ("Right drag / Wheel", "Zoom"),
              ("Middle drag / Shift+Left", "Pan")]),
    ("Select", [("V · P · B · L", "Navigate · Pick · Box · Lasso"), ("Shift / Ctrl", "Add / subtract"),
                ("Ctrl+A · Ctrl+I", "Select all · Invert"), ("Esc", "Clear selection / cancel drawing")]),
    ("Edit", [("Delete / Backspace", "Move selection to trash"), ("Ctrl+Z", "Undo"),
              ("Ctrl+Y / Ctrl+Shift+Z", "Redo")]),
    ("Surface", [("Ctrl+T", "Create TIN"), ("Ctrl+G", "Create DEM")]),
    ("Section", [("S", "Section mode"), ("Click · Double-click / Enter", "Add vertex · Finish"),
                 ("Backspace · Esc", "Remove last vertex · Cancel"), ("Drag vertex", "Edit line"),
                 ("Profile: Wheel · Shift+Wheel", "Zoom · Vertical exaggeration")]),
    ("Color bar", [("Drag ends / middle", "Change min-max / shift range"), ("Wheel · Double-click", "Zoom range · Auto")]),
]


class ShortcutsDialog(_Base):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(5)
        r = 0
        for group, items in SHORTCUTS:
            h = QLabel(group.upper())
            h.setObjectName("sectionTitle")
            grid.addWidget(h, r, 0, 1, 2)
            r += 1
            for keys, what in items:
                k = QLabel(keys)
                k.setStyleSheet(f"color:{C['text']}; font-weight:600")
                d = QLabel(what)
                d.setObjectName("muted")
                grid.addWidget(k, r, 0)
                grid.addWidget(d, r, 1)
                r += 1
            grid.setRowMinimumHeight(r, 8)
            r += 1
        v.addLayout(grid)
        b = QPushButton("Close")
        b.clicked.connect(self.accept)
        v.addWidget(b, 0, Qt.AlignRight)
