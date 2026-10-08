"""Dark theme: Fusion style + palette + stylesheet."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette

C = dict(
    bg="#121418", surface="#1a1d23", surface_alt="#1e2128", surface2="#24282f",
    border="#2e333c", text="#e6e8eb", muted="#8b919c", muted_dim="#5a606b",
    accent="#4c8dff", accent_hover="#6aa1ff", accent_dim="#1f3a66",
    success="#3ecf8e", danger="#ff6b6b", skip_bg="#1f2228", start_bg="#173a2a",
)
VIEWPORT_BG = "#0e1013"
SELECTION_HEX = "#ff3dd8"
SELECTION_RGBA = (1.0, 0.24, 0.85, 1.0)
TRASH_DIM_RGBA = (1.0, 0.32, 0.32, 0.30)
TRASH_HI_RGBA = (1.0, 0.55, 0.15, 1.0)

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets").replace("\\", "/")

QSS = """
QMainWindow, QDialog {{ background: {bg}; }}
QToolTip {{ background: {surface2}; color: {text}; border: 1px solid {border}; padding: 5px 8px; border-radius: 6px; }}

QMenuBar {{ background: {bg}; padding: 3px 6px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {surface2}; }}
QMenu {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accent_dim}; }}
QMenu::item:disabled {{ color: {muted_dim}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}

QToolBar {{ background: {bg}; border: none; border-bottom: 1px solid {border}; padding: 6px 10px; spacing: 4px; }}
QToolBar::separator {{ width: 1px; background: {border}; margin: 6px 8px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 6px 10px; color: {text}; }}
QToolButton:hover {{ background: {surface2}; border-color: {border}; }}
QToolButton:pressed, QToolButton:checked {{ background: {accent_dim}; border-color: {accent}; }}
QToolButton:disabled {{ color: {muted_dim}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QPushButton {{ background: {surface2}; border: 1px solid {border}; border-radius: 8px; padding: 7px 18px; color: {text}; }}
QPushButton:hover {{ border-color: {muted_dim}; }}
QPushButton:disabled {{ color: {muted_dim}; }}
QPushButton[primary="true"] {{ background: {accent}; border-color: {accent}; color: white; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton[primary="true"]:disabled {{ background: {surface2}; border-color: {border}; color: {muted_dim}; }}

QComboBox, QAbstractSpinBox {{ background: {surface2}; border: 1px solid {border}; border-radius: 7px; padding: 5px 8px; min-height: 18px; color: {text}; }}
QComboBox:hover, QAbstractSpinBox:hover {{ border-color: {muted_dim}; }}
QComboBox:focus, QAbstractSpinBox:focus {{ border-color: {accent}; }}
QComboBox:disabled, QAbstractSpinBox:disabled {{ color: {muted_dim}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({assets}/chevron-down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {surface}; border: 1px solid {border}; padding: 4px; selection-background-color: {accent_dim}; outline: 0; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; background: transparent; width: 18px; subcontrol-origin: border; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
QAbstractSpinBox::up-arrow {{ image: url({assets}/chevron-up.svg); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url({assets}/chevron-down.svg); width: 10px; height: 10px; }}

QSlider::groove:horizontal {{ height: 4px; background: {border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {text}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: white; }}
QSlider::handle:horizontal:disabled {{ background: {muted_dim}; }}

QPlainTextEdit, QTextBrowser {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 6px; selection-background-color: {accent_dim}; color: {text}; }}
QTableWidget {{ background: {surface}; alternate-background-color: {surface_alt}; border: 1px solid {border}; border-radius: 8px; gridline-color: {border}; selection-background-color: {accent_dim}; color: {text}; }}
QHeaderView {{ background: {surface2}; }}
QHeaderView::section {{ background: {surface2}; color: {muted}; border: none; border-right: 1px solid {border}; border-bottom: 1px solid {border}; padding: 6px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {surface2}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {muted_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{ background: {bg}; border-top: 1px solid {border}; color: {muted}; }}
QStatusBar::item {{ border: none; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QDockWidget {{ border: none; color: {muted}; }}
QDockWidget::title {{ background: {bg}; padding: 6px 10px; border-top: 1px solid {border}; }}

QFrame#card {{ background: {surface}; border: 1px solid {border}; border-radius: 12px; }}
QFrame#card QLabel {{ background: transparent; }}
QWidget#sidebar {{ background: {bg}; border-left: 1px solid {border}; }}
QWidget#emptyState {{ background: {bg}; }}
QLabel#sectionTitle {{ color: {muted}; font-size: 11px; font-weight: 700; }}
QLabel#muted {{ color: {muted}; }}
QLabel#title {{ font-size: 20px; font-weight: 600; }}
QLabel#chipAccent {{ background: #3a1838; border: 1px solid {sel}; border-radius: 10px; padding: 2px 10px; color: #ffd6f6; margin: 3px 2px; }}
QLabel#chipDanger {{ background: #3a1a1a; border: 1px solid #a8423f; border-radius: 10px; padding: 2px 10px; color: #ffc9c7; margin: 3px 2px; }}
QPushButton[danger="true"] {{ color: {danger}; }}
QPushButton[danger="true"]:hover {{ background: #3a1a1a; border-color: {danger}; }}
QPushButton[danger="true"]:disabled {{ color: {muted_dim}; }}
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {surface2}; }}
QListWidget::item:selected {{ background: #3a2416; border: 1px solid #b4652a; }}
QCheckBox {{ spacing: 8px; color: {text}; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {muted_dim}; background: {surface2}; }}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; image: url({assets}/check.svg); }}
QListWidget::indicator, QTableWidget::indicator {{ width: 14px; height: 14px; border-radius: 4px; border: 1px solid {muted_dim}; background: {surface2}; margin-right: 6px; }}
QListWidget::indicator:checked {{ background: {accent}; border-color: {accent}; image: url({assets}/check.svg); }}
QRadioButton {{ spacing: 8px; color: {text}; background: transparent; padding: 2px 0; }}
QRadioButton:disabled {{ color: {muted_dim}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; border: 1px solid {muted_dim}; background: {surface2}; }}
QRadioButton::indicator:hover {{ border-color: {accent}; }}
QRadioButton::indicator:checked {{ border: 4px solid {accent}; background: #ffffff; width: 8px; height: 8px; }}
QLineEdit {{ background: {surface2}; border: 1px solid {border}; border-radius: 7px; padding: 6px 8px; color: {text}; selection-background-color: {accent_dim}; }}
QLineEdit:focus {{ border-color: {accent}; }}
QLineEdit:disabled {{ color: {muted_dim}; }}
QLabel#chip {{ background: {surface2}; border: 1px solid {border}; border-radius: 10px; padding: 2px 10px; color: {text}; margin: 3px 2px; }}
QProgressBar {{ background: {surface2}; border: none; border-radius: 3px; max-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
"""


def apply_theme(app):
    app.setStyle("Fusion")
    try:  # Qt 6.8+: dark native title bar / dialogs
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:
        pass

    p = QPalette()
    roles = {
        QPalette.Window: C["bg"], QPalette.WindowText: C["text"],
        QPalette.Base: C["surface"], QPalette.AlternateBase: C["surface_alt"],
        QPalette.Text: C["text"], QPalette.Button: C["surface2"],
        QPalette.ButtonText: C["text"], QPalette.Highlight: C["accent"],
        QPalette.HighlightedText: "#ffffff", QPalette.ToolTipBase: C["surface2"],
        QPalette.ToolTipText: C["text"], QPalette.PlaceholderText: C["muted"],
        QPalette.Link: C["accent"],
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(C["muted_dim"]))
    app.setPalette(p)

    font = QFont()
    font.setFamilies(["Segoe UI Variable", "Segoe UI", "Inter", "SF Pro Text",
                      "Noto Sans", "Helvetica Neue", "Arial"])
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(QSS.format(assets=_ASSETS, sel=SELECTION_HEX, **C))


def dark_titlebar(widget):
    """Windows 10/11: force a dark title bar."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        val = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass
