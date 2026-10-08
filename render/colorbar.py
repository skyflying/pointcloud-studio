"""Interactive colour bar drawn over the 3D view.

The bar's ends are the fixed SCALE limits (Shallow / Deep in the Color Scale card).
Two handles on the bar set the DISPLAY range — the colours are stretched between the
handles; beyond them points take the end colours (or grey when "Gray out" is on).

  drag a handle      → move that end of the display range (scale stays fixed)
  drag between them  → shift the display range
  wheel              → narrow / widen the display range around the cursor
  double-click       → handles back to the full scale
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from render.colormap import GRAY_RGBA, nice_ticks
from ui.theme import C

W = 124
HANDLE_PX = 9


class ColorBarOverlay(QWidget):
    def __init__(self, target, on_change, on_reset):
        super().__init__(target)
        self.on_change = on_change        # (vmin, vmax, final: bool) — display range
        self.on_reset = on_reset          # double-click: handles to full scale
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setToolTip("Drag a handle: display range (the scale stays fixed) · Drag between handles: shift · "
                        "Wheel: narrow / widen · Double-click: reset handles to the full scale")
        self.lut = None
        self.amin, self.amax = 0.0, 1.0
        self.vmin, self.vmax = 0.0, 1.0
        self.title, self.unit = "", ""
        self.depth = False
        self.gray_out = False
        self._drag = None
        self._img = None
        self._wheel_timer = QTimer(self, singleShot=True, interval=250,
                                   timeout=lambda: self.on_change(self.vmin, self.vmax, True))
        target.installEventFilter(self)
        self._place()
        self.hide()

    # ---------- layout ----------
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            self._place()
        return False

    def _place(self):
        p = self.parentWidget()
        h = int(min(440, max(240, p.height() * 0.58)))
        self.setGeometry(p.width() - W - 16, max(16, (p.height() - h) // 2), W, h)

    def _bar(self):
        return QRectF(16, 44, 16, self.height() - 44 - 34)

    # ---------- state ----------
    def set_state(self, lut, amin, amax, vmin, vmax, title, unit="", depth=False, gray_out=False):
        if lut is not None and (self.lut is None or not np.array_equal(lut, self.lut)):
            rgba = (np.clip(lut[::-1], 0, 1) * 255).astype(np.uint8)   # top = high value
            self._img = QImage(rgba.tobytes(), 1, len(rgba), 4, QImage.Format_RGBA8888).copy()
            self.lut = lut.copy()
        self.amin, self.amax = float(amin), float(amax)
        if self._drag is None:                       # don't fight the user's drag
            self.vmin, self.vmax = float(vmin), float(vmax)
        self.title, self.unit, self.depth, self.gray_out = title, unit, depth, gray_out
        self.update()

    def _span(self):
        return (self.amax - self.amin) or 1.0

    def _y(self, v, bar):
        return bar.bottom() - (v - self.amin) / self._span() * bar.height()

    def _v(self, y, bar):
        return self.amin + (bar.bottom() - y) / bar.height() * self._span()

    def _label(self, v, decimals):
        v = -v if self.depth else v
        return f"{0.0 if v == 0 else v:,.{decimals}f}"

    # ---------- paint ----------
    def paintEvent(self, _):
        if self._img is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        panel = QPainterPath()
        panel.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 10, 10)
        p.fillPath(panel, QColor(22, 24, 29, 215))
        p.setPen(QPen(QColor(C["border"]), 1))
        p.drawPath(panel)

        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1.5))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(8, 8, self.width() - 16, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   self.title.upper() + (f"  ({self.unit})" if self.unit else ""))

        bar = self._bar()
        y_hi, y_lo = self._y(self.vmax, bar), self._y(self.vmin, bar)
        top_c = QColor.fromRgbF(*(GRAY_RGBA if self.gray_out else self.lut[-1]))
        bot_c = QColor.fromRgbF(*(GRAY_RGBA if self.gray_out else self.lut[0]))
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        if y_hi > bar.top():
            p.fillRect(QRectF(bar.left(), bar.top(), bar.width(), y_hi - bar.top()), top_c)
        if y_lo < bar.bottom():
            p.fillRect(QRectF(bar.left(), y_lo, bar.width(), bar.bottom() - y_lo), bot_c)
        if y_lo - y_hi > 0.5:
            p.drawImage(QRectF(bar.left(), y_hi, bar.width(), y_lo - y_hi), self._img)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bar)

        # ticks along the fixed scale
        f.setBold(False)
        p.setFont(f)
        ticks, dec = nice_ticks(self.amin, self.amax, target=max(3, int(bar.height() / 55)))
        x_lab = bar.right() + 10
        lab_w = self.width() - x_lab - 4
        busy = [bar.top(), bar.bottom(), y_hi, y_lo]
        p.setPen(QPen(QColor(C["muted"]), 1))
        for v in ticks:
            y = self._y(v, bar)
            p.drawLine(QPointF(bar.right(), y), QPointF(bar.right() + 4, y))
            if min(abs(y - b) for b in busy) < 13:
                continue
            p.drawText(QRectF(x_lab, y - 8, lab_w, 16), Qt.AlignLeft | Qt.AlignVCenter, self._label(v, dec))

        # scale limits (fixed ends) — bold
        end_dec = max(dec, 2)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        for v, y in ((self.amax, bar.top()), (self.amin, bar.bottom())):
            if min(abs(y - y_hi), abs(y - y_lo)) >= 13:
                p.drawText(QRectF(x_lab, y - 8, lab_w, 16), Qt.AlignLeft | Qt.AlignVCenter, self._label(v, end_dec))

        # display-range handles: triangle + line + value tag (accent)
        accent = QColor(C["accent"])
        for v, y in ((self.vmax, y_hi), (self.vmin, y_lo)):
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawLine(QPointF(bar.left() - 3, y), QPointF(bar.right() + 3, y))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255))
            p.drawPolygon(QPolygonF([QPointF(bar.left() - 4, y), QPointF(bar.left() - 11, y - 5),
                                     QPointF(bar.left() - 11, y + 5)]))
            p.setPen(accent)
            p.drawText(QRectF(x_lab, y - 8, lab_w, 16), Qt.AlignLeft | Qt.AlignVCenter, self._label(v, end_dec))
        p.end()

    # ---------- interaction ----------
    def _hit(self, y):
        bar = self._bar()
        y_hi, y_lo = self._y(self.vmax, bar), self._y(self.vmin, bar)
        d_hi, d_lo = abs(y - y_hi), abs(y - y_lo)
        if min(d_hi, d_lo) <= HANDLE_PX:
            return "max" if d_hi <= d_lo else "min"
        if y_hi < y < y_lo:
            return "pan"
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        hit = self._hit(e.position().y())
        if hit:
            self._drag = (hit, e.position().y(), self.vmin, self.vmax)
            if hit == "pan":
                self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        y = e.position().y()
        if self._drag is None:
            hit = self._hit(y)
            self.setCursor(Qt.SizeVerCursor if hit in ("min", "max")
                           else Qt.OpenHandCursor if hit == "pan" else Qt.ArrowCursor)
            return
        bar = self._bar()
        kind, y0, lo0, hi0 = self._drag
        eps = self._span() * 2e-3
        v = min(max(self._v(y, bar), self.amin), self.amax)       # handles stay on the fixed scale
        lo, hi = lo0, hi0
        if kind == "max":
            hi = max(v, lo0 + eps)
        elif kind == "min":
            lo = min(v, hi0 - eps)
        else:
            dv = (y0 - y) / bar.height() * self._span()
            dv = min(max(dv, self.amin - lo0), self.amax - hi0)
            lo, hi = lo0 + dv, hi0 + dv
        self.vmin, self.vmax = lo, hi
        self.update()
        self.on_change(lo, hi, False)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.ArrowCursor)
            self.on_change(self.vmin, self.vmax, True)

    def mouseDoubleClickEvent(self, e):
        self._drag = None
        self.on_reset()

    def wheelEvent(self, e):
        bar = self._bar()
        y = min(max(e.position().y(), bar.top()), bar.bottom())
        pivot = min(max(self._v(y, bar), self.vmin), self.vmax)
        factor = 0.85 if e.angleDelta().y() > 0 else 1 / 0.85
        lo = max(self.amin, pivot - (pivot - self.vmin) * factor)
        hi = min(self.amax, pivot + (self.vmax - pivot) * factor)
        if hi - lo > self._span() * 2e-3:
            self.vmin, self.vmax = lo, hi
            self.update()
            self.on_change(lo, hi, False)
            self._wheel_timer.start()
        e.accept()
