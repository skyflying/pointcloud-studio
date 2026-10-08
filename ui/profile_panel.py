"""Section profile panel: chainage × elevation/depth plot with measuring."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QToolButton,
    QVBoxLayout, QWidget,
)

from render.colormap import nice_ticks
from ui.icons import icon
from ui.theme import C

TIN_COLOR = "#ffd166"
DEM_COLOR = "#4cc9f0"
MEAS_COLOR = "#ff9f43"
ML, MR, MT, MB = 70, 18, 14, 34      # plot margins


class ProfilePlot(QWidget):
    cursor_moved = Signal(object)    # (chainage, z) or None
    measured = Signal(object)        # dict or None

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumHeight(180)
        self.ch = self.z = self.col = None
        self.tin = self.dem = None
        self.show = {"points": True, "tin": True, "dem": True}
        self.depth = False
        self.vex = 1.0
        self.x0, self.x1, self.yc = 0.0, 1.0, 0.0
        self.measure_mode = False
        self.meas = []
        self._cursor = None
        self._drag = None
        self.point_size = 2

    # ---------- data / view ----------
    def set_data(self, ch, z, colors, tin, dem, fit=False):
        self.ch, self.z, self.col, self.tin, self.dem = ch, z, colors, tin, dem
        if fit:
            self.fit()
        self.update()

    def _plot_rect(self):
        return QRectF(ML, MT, max(10, self.width() - ML - MR), max(10, self.height() - MT - MB))

    def _yspan(self):
        r = self._plot_rect()
        return (self.x1 - self.x0) * r.height() / r.width() / self.vex

    def _all_z(self):
        parts = []
        if self.z is not None and len(self.z) and self.show["points"]:
            parts.append(self.z)
        for k in ("tin", "dem"):
            d = getattr(self, k)
            if d is not None and self.show[k] and len(d[1]):
                parts.append(d[1][np.isfinite(d[1])])
        return np.concatenate(parts) if parts else np.empty(0)

    def _all_ch(self):
        parts = [self.ch] if self.ch is not None and len(self.ch) else []
        for k in ("tin", "dem"):
            d = getattr(self, k)
            if d is not None and len(d[0]):
                parts.append(d[0][np.isfinite(d[0])])
        return np.concatenate(parts) if parts else np.empty(0)

    def fit(self, keep_vex=False):
        ch, z = self._all_ch(), self._all_z()
        if len(ch) == 0:
            return
        a, b = float(ch.min()), float(ch.max())
        pad = max((b - a) * 0.02, 0.5)
        self.x0, self.x1 = a - pad, b + pad
        if len(z):
            zl, zh = float(np.nanmin(z)), float(np.nanmax(z))
            self.yc = (zl + zh) / 2
            if not keep_vex:
                r = self._plot_rect()
                zr = max(zh - zl, 1e-3) / 0.8
                self.vex = max(1.0, round((self.x1 - self.x0) * r.height() / r.width() / zr, 1))
        self.update()

    def set_vex(self, v):
        self.vex = max(0.01, float(v))
        self.update()

    def to_px(self, ch, z):
        r = self._plot_rect()
        ys = self._yspan()
        x = r.left() + (np.asarray(ch) - self.x0) / (self.x1 - self.x0) * r.width()
        y = r.top() + (self.yc + ys / 2 - np.asarray(z)) / ys * r.height()
        return x, y

    def to_data(self, px, py):
        r = self._plot_rect()
        ys = self._yspan()
        ch = self.x0 + (px - r.left()) / r.width() * (self.x1 - self.x0)
        z = self.yc + ys / 2 - (py - r.top()) / r.height() * ys
        return ch, z

    # ---------- paint ----------
    def _zlabel(self, v, dec):
        v = -v if self.depth else v
        return f"{0.0 if v == 0 else v:,.{dec}f}"

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(C["bg"]))
        r = self._plot_rect()
        p.fillRect(r, QColor("#0e1013"))
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1))
        p.setFont(f)
        # grid + ticks
        ys = self._yspan()
        xt, xd = nice_ticks(self.x0, self.x1, max(3, int(r.width() / 90)))
        yt, yd = nice_ticks(self.yc - ys / 2, self.yc + ys / 2, max(3, int(r.height() / 40)))
        grid = QPen(QColor(255, 255, 255, 18), 1)
        for v in xt:
            x, _ = self.to_px(v, 0)
            p.setPen(grid)
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(x - 50, r.bottom() + 4, 100, 16), Qt.AlignHCenter | Qt.AlignTop, f"{v:,.{xd}f}")
        for v in yt:
            _, y = self.to_px(0, v)
            p.setPen(grid)
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(0, y - 8, ML - 8, 16), Qt.AlignRight | Qt.AlignVCenter, self._zlabel(v, yd))
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(r.left(), self.height() - 16, r.width(), 14), Qt.AlignHCenter,
                   "Chainage (m)")
        p.save()
        p.translate(12, r.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-80, -8, 160, 16), Qt.AlignCenter, "Depth (m)" if self.depth else "Elevation (m)")
        p.restore()

        p.setClipRect(r)
        # points → raster buffer (fast for many coloured points)
        if self.show["points"] and self.ch is not None and len(self.ch):
            W, H = int(r.width()), int(r.height())
            x, y = self.to_px(self.ch, self.z)
            xi = (x - r.left()).astype(np.int64)
            yi = (y - r.top()).astype(np.int64)
            buf = np.zeros((H, W), dtype=np.uint32)
            rgba = (np.clip(self.col, 0, 1) * 255).astype(np.uint32)
            argb = (255 << 24) | (rgba[:, 0] << 16) | (rgba[:, 1] << 8) | rgba[:, 2]
            s = self.point_size
            for dx in range(s):
                for dy in range(s):
                    ok = (xi + dx >= 0) & (xi + dx < W) & (yi + dy >= 0) & (yi + dy < H)
                    buf[yi[ok] + dy, xi[ok] + dx] = argb[ok]
            img = QImage(buf.data, W, H, W * 4, QImage.Format_ARGB32_Premultiplied)
            p.drawImage(QPointF(r.left(), r.top()), img)
        # surface lines
        for key, color in (("dem", DEM_COLOR), ("tin", TIN_COLOR)):
            d = getattr(self, key)
            if d is None or not self.show[key] or len(d[0]) == 0:
                continue
            x, y = self.to_px(*d)
            path = QPainterPath()
            pen_down = False
            for xx, yy in zip(x, y):
                if not (math.isfinite(xx) and math.isfinite(yy)):
                    pen_down = False
                    continue
                if pen_down:
                    path.lineTo(xx, yy)
                else:
                    path.moveTo(xx, yy)
                    pen_down = True
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(color), 1.8))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        # measurement
        pts = list(self.meas)
        if self.measure_mode and len(pts) == 1 and self._cursor is not None:
            pts.append(self._cursor)
        if pts:
            p.setRenderHint(QPainter.Antialiasing)
            xy = [self.to_px(c, z) for c, z in pts]
            p.setPen(QPen(QColor(MEAS_COLOR), 1.6, Qt.DashLine if len(self.meas) < 2 else Qt.SolidLine))
            if len(xy) == 2:
                p.drawLine(QPointF(*xy[0]), QPointF(*xy[1]))
            p.setBrush(QColor(MEAS_COLOR))
            for x, y in xy:
                p.drawEllipse(QPointF(x, y), 3.5, 3.5)
        # cursor crosshair
        if self._cursor is not None:
            x, y = self.to_px(*self._cursor)
            p.setPen(QPen(QColor(255, 255, 255, 60), 1, Qt.DashLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setClipping(False)
        # legend
        lx = r.right() - 8
        p.setRenderHint(QPainter.Antialiasing)
        for key, label, color in (("dem", "DEM", DEM_COLOR), ("tin", "TIN", TIN_COLOR)):
            d = getattr(self, key)
            if d is None or not self.show[key]:
                continue
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(lx - 40, r.top() + 4, 40, 16), Qt.AlignRight | Qt.AlignVCenter, label)
            p.setPen(QPen(QColor(color), 2))
            p.drawLine(QPointF(lx - 66, r.top() + 12), QPointF(lx - 46, r.top() + 12))
            lx -= 80
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(r.left() + 8, r.top() + 4, 200, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   f"V.exag {self.vex:g}×")
        p.end()

    # ---------- interaction ----------
    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() == Qt.LeftButton and self.measure_mode:
            pt = self.to_data(pos.x(), pos.y())
            self.meas = [pt] if len(self.meas) != 1 else self.meas + [pt]
            self._emit_measure()
            self.update()
        elif e.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._drag = (pos.x(), pos.y(), self.x0, self.x1, self.yc)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is not None:
            px, py, x0, x1, yc = self._drag
            r = self._plot_rect()
            dx = (pos.x() - px) / r.width() * (x1 - x0)
            dy = (pos.y() - py) / r.height() * self._yspan()
            self.x0, self.x1, self.yc = x0 - dx, x1 - dx, yc + dy
        if self._plot_rect().contains(pos):
            self._cursor = self.to_data(pos.x(), pos.y())
        else:
            self._cursor = None
        self.cursor_moved.emit(self._cursor)
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.CrossCursor if self.measure_mode else Qt.ArrowCursor)

    def leaveEvent(self, _):
        self._cursor = None
        self.cursor_moved.emit(None)
        self.update()

    def wheelEvent(self, e):
        pos = e.position()
        c, z = self.to_data(pos.x(), pos.y())
        f = 0.85 if e.angleDelta().y() > 0 else 1 / 0.85
        if e.modifiers() & Qt.ShiftModifier:         # change vertical exaggeration only
            self.vex = max(0.01, self.vex / f)
        else:
            self.x0 = c - (c - self.x0) * f
            self.x1 = c + (self.x1 - c) * f
            ys_new = self._yspan()
            r = self._plot_rect()
            t = (pos.y() - r.top()) / r.height()
            self.yc = z + (t - 0.5) * ys_new
        self.update()
        self.vex_changed()

    def vex_changed(self):  # overridden by the panel
        pass

    def set_measure_mode(self, on):
        self.measure_mode = bool(on)
        self.meas = []
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        self._emit_measure()
        self.update()

    def _emit_measure(self):
        if len(self.meas) == 2:
            (c1, z1), (c2, z2) = self.meas
            dl, dz = c2 - c1, z2 - z1
            self.measured.emit(dict(dl=dl, dz=dz, dist=math.hypot(dl, dz),
                                    slope=dz / dl * 100 if abs(dl) > 1e-12 else float("inf"),
                                    deg=math.degrees(math.atan2(dz, abs(dl)))))
        else:
            self.measured.emit(None)


class ProfilePanel(QWidget):
    """Toolbar + plot; talks to the main window through callbacks."""

    def __init__(self, cb):
        super().__init__()
        self.cb = cb
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 6)
        v.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.cmb = QComboBox()
        self.cmb.setMinimumWidth(130)
        self.cmb.currentIndexChanged.connect(lambda i: cb.select_section(i))
        self.sp_width = QDoubleSpinBox(minimum=0.01, maximum=1e4, value=1.0, singleStep=0.5, decimals=2)
        self.sp_width.setPrefix("± ")
        self.sp_width.setSuffix(" m")
        self.sp_width.setToolTip("Corridor half-width: points within this distance of the line are shown")
        self.sp_width.setKeyboardTracking(False)
        self.sp_width.valueChanged.connect(lambda val: cb.set_section_width(val))
        self.sp_start = QDoubleSpinBox(minimum=-1e9, maximum=1e9, value=0.0, singleStep=10, decimals=2)
        self.sp_start.setSuffix(" m")
        self.sp_start.setToolTip("Chainage at the first vertex (e.g. KP offset)")
        self.sp_start.setKeyboardTracking(False)
        self.sp_start.valueChanged.connect(lambda val: cb.set_section_start(val))
        self.sp_vex = QDoubleSpinBox(minimum=0.01, maximum=1e4, value=1.0, singleStep=1, decimals=1)
        self.sp_vex.setSuffix(" ×")
        self.sp_vex.setToolTip("Vertical exaggeration (Shift + wheel on the plot)")
        self.sp_vex.setKeyboardTracking(False)
        self.chk = {}
        for w in (QLabel("Section"), self.cmb, QLabel("Width"), self.sp_width, QLabel("Start"), self.sp_start):
            if isinstance(w, QLabel):
                w.setObjectName("muted")
            bar.addWidget(w)
        bar.addSpacing(8)
        for key, label in (("points", "Points"), ("tin", "TIN"), ("dem", "DEM")):
            c = QCheckBox(label)
            c.setChecked(True)
            c.toggled.connect(lambda on, k=key: self._toggle(k, on))
            self.chk[key] = c
            bar.addWidget(c)
        bar.addSpacing(8)
        lbl = QLabel("V.exag")
        lbl.setObjectName("muted")
        bar.addWidget(lbl)
        bar.addWidget(self.sp_vex)
        bar.addStretch()
        self.btn_measure = QPushButton("Measure")
        self.btn_measure.setCheckable(True)
        self.btn_measure.setToolTip("Click two points on the profile")
        self.btn_fit = QPushButton("Fit")
        self.btn_csv = QPushButton("CSV")
        self.btn_csv.setIcon(icon("export"))
        self.btn_png = QPushButton("PNG")
        self.btn_png.setIcon(icon("export"))
        self.btn_del = QToolButton()
        self.btn_del.setIcon(icon("trash", "#ff6b6b"))
        self.btn_del.setToolTip("Delete this section")
        for b in (self.btn_measure, self.btn_fit, self.btn_csv, self.btn_png, self.btn_del):
            bar.addWidget(b)
        v.addLayout(bar)

        self.plot = ProfilePlot()
        v.addWidget(self.plot, 1)
        self.lbl = QLabel(" ")
        self.lbl.setObjectName("muted")
        v.addWidget(self.lbl)

        self.sp_vex.valueChanged.connect(self.plot.set_vex)
        self.plot.vex_changed = lambda: self._sync_vex()
        self.btn_measure.toggled.connect(self.plot.set_measure_mode)
        self.btn_fit.clicked.connect(lambda: (self.plot.fit(), self._sync_vex()))
        self.btn_csv.clicked.connect(cb.export_section_csv)
        self.btn_png.clicked.connect(cb.export_section_png)
        self.btn_del.clicked.connect(cb.delete_section)
        self.plot.cursor_moved.connect(self._on_cursor)
        self.plot.measured.connect(self._on_measure)
        self._readout = ""
        self._measure = ""
        self.section = None

    def _toggle(self, key, on):
        self.plot.show[key] = on
        self.plot.update()

    def _sync_vex(self):
        self.sp_vex.blockSignals(True)
        self.sp_vex.setValue(self.plot.vex)
        self.sp_vex.blockSignals(False)

    def set_sections(self, names, current):
        self.cmb.blockSignals(True)
        self.cmb.clear()
        self.cmb.addItems(names)
        self.cmb.setCurrentIndex(current)
        self.cmb.blockSignals(False)

    def show_section(self, sec, colors, depth, fit):
        self.section = sec
        for sp, val in ((self.sp_width, sec.width), (self.sp_start, sec.start)):
            sp.blockSignals(True)
            sp.setValue(val)
            sp.blockSignals(False)
        r = sec.result
        off = sec.start
        tin = (r["tin"][0] + off, r["tin"][1]) if r.get("tin") is not None else None
        dem = (r["dem"][0] + off, r["dem"][1]) if r.get("dem") is not None else None
        self.chk["tin"].setEnabled(tin is not None)
        self.chk["dem"].setEnabled(dem is not None)
        self.plot.depth = depth
        self.plot.set_data(r["chainage"] + off, r["z"], colors, tin, dem, fit=fit)
        if fit:
            self._sync_vex()
        self._readout = (f"{sec.name}: length {sec.length:,.2f} m · {len(r['idx']):,} points within "
                         f"±{sec.width:g} m")
        self._refresh_label()

    def _on_cursor(self, pt):
        if pt is None or self.section is None:
            self._refresh_label()
            return
        c, z = pt
        x, y = self.section.xy_at(c)
        zl = f"depth {-z:,.3f}" if self.plot.depth else f"Z {z:,.3f}"
        self._refresh_label(f"chainage {c:,.2f} m · {zl} m · X {x:,.2f}  Y {y:,.2f}")

    def _on_measure(self, m):
        if m is None:
            self._measure = ""
        else:
            dz = -m["dz"] if self.plot.depth else m["dz"]
            lab = "Δdepth" if self.plot.depth else "ΔZ"
            self._measure = (f"Measure: ΔL {m['dl']:,.3f} m · {lab} {dz:+,.3f} m · distance {m['dist']:,.3f} m · "
                             f"slope {m['slope']:+.2f}% ({m['deg']:+.2f}°)")
        self._refresh_label()

    def _refresh_label(self, cursor=""):
        parts = [p for p in (self._measure or self._readout, cursor) if p]
        self.lbl.setText("     ".join(parts) or " ")
