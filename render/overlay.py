"""Transparent Qt overlay drawn on top of the 3D canvas (selection rectangle / lasso)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ui.theme import C


class SelectionOverlay(QWidget):
    def __init__(self, target):
        super().__init__(target)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._rect = None
        self._path = None
        self._closed = False
        target.installEventFilter(self)
        self.resize(target.size())
        self.raise_()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            self.resize(obj.size())
        return False

    def set_rect(self, p0, p1):
        self._rect = QRectF(QPointF(*p0), QPointF(*p1)).normalized()
        self._path = None
        self.update()

    def set_path(self, pts):
        self._path = [QPointF(*p) for p in pts]
        self._rect = None
        self.update()

    def clear(self):
        self._rect = self._path = None
        self.update()

    def paintEvent(self, _):
        if self._rect is None and not self._path:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        accent = QColor(C["accent"])
        fill = QColor(accent)
        fill.setAlpha(45)
        pen = QPen(accent, 1.5, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(fill)
        if self._rect is not None:
            p.drawRect(self._rect)
        elif len(self._path) >= 2:
            p.drawPolygon(QPolygonF(self._path))
        p.end()
