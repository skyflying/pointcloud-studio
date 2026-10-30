"""Tall analysis dialogs: the action buttons stay in a footer that is always visible, the dialog is never
narrower than its content (nothing cut off at the right), and it opens fitted to the screen."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget


def make_footer(outer, run_widgets=(), buttons=(), columns=3):
    """Footer below the scroll area: first row = message + run button(s), then the buttons in a grid."""
    foot = QWidget()
    foot.setObjectName("dialogBody")
    fv = QVBoxLayout(foot)
    fv.setContentsMargins(22, 8, 22, 12)
    fv.setSpacing(6)
    if run_widgets:
        r = QHBoxLayout()
        for w, stretch in run_widgets:
            r.addWidget(w, stretch)
        fv.addLayout(r)
    if buttons:
        g = QGridLayout()
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(6)
        for k, b in enumerate(buttons):
            g.addWidget(b, k // columns, k % columns)
        fv.addLayout(g)
    outer.addWidget(foot)
    return foot


class _Fitter(QObject):
    def __init__(self, dlg, body, scroll, pref):
        super().__init__(dlg)
        self.dlg, self.body, self.scroll, self.pref = dlg, body, scroll, pref
        self.done_first = False
        body.installEventFilter(self)
        dlg.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self.body and ev.type() == QEvent.LayoutRequest:
            QTimer.singleShot(0, self.fit_width)
        elif obj is self.dlg and ev.type() == QEvent.Show and not self.done_first:
            self.done_first = True
            QTimer.singleShot(0, self.first_fit)
        return False

    def need_width(self):
        self.body.layout().activate()
        return (self.body.minimumSizeHint().width() + self.scroll.verticalScrollBar().sizeHint().width()
                + 2 * self.scroll.frameWidth() + 4)

    def fit_width(self):
        try:
            need = self.need_width()
        except RuntimeError:
            return
        if self.dlg.minimumWidth() != need:
            self.dlg.setMinimumWidth(need)
        if self.dlg.width() < need:
            self.dlg.resize(need, self.dlg.height())

    def first_fit(self):
        scr = self.dlg.screen() or (self.dlg.parentWidget().screen() if self.dlg.parentWidget() else None)
        need = self.need_width()
        self.dlg.setMinimumWidth(need)
        w, h = max(self.pref[0], need), self.pref[1]
        if scr is not None:
            av = scr.availableGeometry()
            w = min(w, av.width() - 20) if need <= av.width() - 20 else need
            h = min(h, av.height() - 60)                      # title bar + taskbar
            self.dlg.resize(w, h)
            g = self.dlg.frameGeometry()
            x = min(max(g.x(), av.x()), av.right() - g.width())
            y = min(max(g.y(), av.y()), av.bottom() - g.height())
            self.dlg.move(max(x, av.x()), max(y, av.y()))
        else:
            self.dlg.resize(w, h)


def fit_dialog(dlg, body, scroll, pref=(760, 1000)):
    """Keep the dialog at least as wide as its content and open it within the screen."""
    dlg._fitter = _Fitter(dlg, body, scroll, pref)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return dlg._fitter
