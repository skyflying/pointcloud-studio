"""Inline SVG icons (no external icon package needed)."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ui.theme import C

_CUBE = ('<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/>'
         '<path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>')
_FACE = '<path d="{d}" fill="{a}" fill-opacity="0.55" stroke="none"/>'

_ICONS = {
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "top": _FACE.format(d="M12 3l8 4.5L12 12 4 7.5z", a="{a}") + _CUBE,
    "front": _FACE.format(d="M4 7.5L12 12v9l-8-4.5z", a="{a}") + _CUBE,
    "side": _FACE.format(d="M20 7.5L12 12v9l8-4.5z", a="{a}") + _CUBE,
    "iso": _CUBE,
    "navigate": '<ellipse cx="12" cy="12" rx="9" ry="4.2"/><circle cx="12" cy="12" r="2.2"/><path d="M17.5 6.6l3 .9-1.3 2.7"/>',
    "pick": '<circle cx="12" cy="12" r="6"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/><circle cx="12" cy="12" r="1" fill="{a}" stroke="{a}"/>',
    "box": '<rect x="4" y="5" width="16" height="14" rx="1.5" stroke-dasharray="3 2.6"/>',
    "lasso": '<path d="M7.5 16.5C3.5 14 3.5 8 9 5.8 14 3.8 21 6 20.4 10.6 19.8 15 13 16.3 9.6 15.4" stroke-dasharray="3 2.4"/><circle cx="8" cy="17.6" r="1.9"/><path d="M7.4 19.4L6.6 22"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M5.5 7l1 12a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "restore": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
    "export": '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14"/>',
    "section": '<path d="M3.5 18.5l5.5-9 5 4.5 6.5-9.5"/><circle cx="3.5" cy="18.5" r="1.6" fill="{a}" stroke="{a}"/><circle cx="9" cy="9.5" r="1.6" fill="{a}" stroke="{a}"/><circle cx="14" cy="14" r="1.6" fill="{a}" stroke="{a}"/><circle cx="20.5" cy="4.5" r="1.6" fill="{a}" stroke="{a}"/>',
    "fit": '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
}


def _render(svg, size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return pm


def icon(name, color=None, size=20):
    color = color or "#c9ced6"
    body = _ICONS[name].replace("{a}", C["accent"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="1.7" stroke-linecap="round" '
           f'stroke-linejoin="round">{body}</svg>')
    ic = QIcon()
    for s in (size, size * 2):
        ic.addPixmap(_render(svg, s))
    return ic


def point_cloud_pixmap(size=120):
    """Decorative illustration for the empty state: a dotted terrain."""
    rng = np.random.default_rng(3)
    dots = []
    for _ in range(260):
        x = rng.uniform(8, 112)
        h = 18 * np.sin(x / 18) + 10 * np.cos(x / 9)
        y = rng.uniform(62 - h * 0.6, 100)
        t = (100 - y) / 60
        r = 1.3 + 0.8 * rng.random()
        op = 0.25 + 0.65 * t
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" '
                    f'fill="{C["accent"]}" fill-opacity="{op:.2f}"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">'
           f'{"".join(dots)}</svg>')
    return _render(svg, size)
