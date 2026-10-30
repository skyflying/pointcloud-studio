"""Chart background for every chart and exported figure: dark (as the program) or white (reports).

The choice is stored in the settings (View → Chart Background, or the selector in the chart dialogs) and
applies at once to all open charts. On white, light line colours (white, yellow, pale blue …) are darkened
so they stay visible; the hue is kept."""
from __future__ import annotations

import weakref

from PySide6.QtGui import QColor

from ui.theme import C

KEY = "charts/background"
THEMES = {
    "dark": {"surface": C["surface"], "bg": C["bg"], "plot": "#0e1013", "border": C["border"], "muted": C["muted"],
             "text": C["text"], "grid_rgba": (255, 255, 255, 18), "cursor_rgba": (255, 255, 255, 60)},
    "white": {"surface": "#ffffff", "bg": "#ffffff", "plot": "#ffffff", "border": "#b9bec7", "muted": "#4a4f57",
              "text": "#15181d", "grid_rgba": (0, 0, 0, 28), "cursor_rgba": (0, 0, 0, 90)},
}
LABELS = {"dark": "Dark", "white": "White"}
_listeners = weakref.WeakSet()


def current():
    from PySide6.QtCore import QSettings
    v = QSettings("PointCloudStudio", "PointCloudStudio").value(KEY, "dark")
    return v if v in THEMES else "dark"


def colors(name=None):
    return THEMES[name or current()]


def set_current(name):
    from PySide6.QtCore import QSettings
    if name not in THEMES:
        return
    QSettings("PointCloudStudio", "PointCloudStudio").setValue(KEY, name)
    for w in list(_listeners):
        try:
            cb = getattr(w, "on_chart_theme", None)
            if cb is not None:
                cb(name)
            else:
                w.update()
        except RuntimeError:                    # widget already deleted
            pass


def register(widget):
    """Repaint `widget` (or call its on_chart_theme) when the background changes."""
    _listeners.add(widget)


def adapt(color, name=None):
    """Line / text colour for the background: on white, too-light colours are darkened (same hue)."""
    c = QColor(color)
    if (name or current()) != "white":
        return c
    lum = (0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF())
    if lum > 0.55:
        h, s, v, a = c.getHsvF()
        if s < 0.15:                             # white / grey → dark grey
            return QColor.fromHsvF(h if h >= 0 else 0, s, 0.25, a)
        return QColor.fromHsvF(h if h >= 0 else 0, min(1.0, s * 1.15 + 0.1), max(0.35, v * 0.55 / lum), a)
    return c


def selector(parent=None):
    """A small combo box 'Charts: Dark / White' bound to the global setting."""
    from PySide6.QtWidgets import QComboBox
    cmb = QComboBox(parent)
    for k, lab in LABELS.items():
        cmb.addItem(f"Chart background: {lab}", k)
    cmb.setCurrentIndex(max(cmb.findData(current()), 0))
    cmb.setToolTip("Background of the charts and of the exported figures (dark as the program, or white for "
                   "reports)")
    cmb.currentIndexChanged.connect(lambda _i: set_current(cmb.currentData()))

    def on_theme(name, cmb=cmb):
        j = cmb.findData(name)
        if j >= 0 and j != cmb.currentIndex():
            cmb.blockSignals(True)
            cmb.setCurrentIndex(j)
            cmb.blockSignals(False)
    cmb.on_chart_theme = on_theme
    register(cmb)
    return cmb
