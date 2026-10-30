"""Checkable lists and tables that are easy to tick.

* ClickToggleMixin — a click anywhere on the check cell (and on the name next to it) toggles the tick.
  Qt by itself only toggles when the click hits the small indicator, which depends on the platform style
  and is easy to miss; here the tick is toggled whenever Qt did not already do it.
* check_bar() — "All", "None", "Invert" buttons for such a list or table.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QPushButton, QWidget


class ClickToggleMixin:
    check_col = 0              # tables: column holding the check item
    toggle_cols = (0, 1)       # tables: clicks in these columns toggle

    def check_items(self):
        if isinstance(self, QListWidget):
            return [self.item(i) for i in range(self.count())]
        return [self.item(r, self.check_col) for r in range(self.rowCount())
                if self.item(r, self.check_col) is not None]

    def _check_item_at(self, pos):
        if isinstance(self, QListWidget):
            it = self.itemAt(pos)
        else:
            r, c = self.rowAt(pos.y()), self.columnAt(pos.x())
            it = self.item(r, self.check_col) if r >= 0 and c in self.toggle_cols else None
        if it is None or not (it.flags() & Qt.ItemIsUserCheckable) or not (it.flags() & Qt.ItemIsEnabled):
            return None
        return it

    def mousePressEvent(self, e):
        p = e.position().toPoint()
        it = self._check_item_at(p) if e.button() == Qt.LeftButton else None
        self._press = (p, it, it.checkState() if it is not None else None)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        p0, it, state = getattr(self, "_press", (None, None, None))
        self._press = (None, None, None)
        if it is None or e.button() != Qt.LeftButton:
            return
        p = e.position().toPoint()
        if (p - p0).manhattanLength() > 6 or self._check_item_at(p) is not it:
            return                                  # a drag (e.g. reordering), not a click
        try:
            if it.checkState() == state:            # Qt did not toggle it (click beside the indicator)
                it.setCheckState(Qt.Unchecked if state == Qt.Checked else Qt.Checked)
        except RuntimeError:                         # a listener rebuilt the list (the toggle already happened)
            pass

    def keyPressEvent(self, e):
        # Space toggles all selected rows
        if e.key() == Qt.Key_Space:
            items = self.selectedItems()
            if not isinstance(self, QListWidget):
                rows = sorted({i.row() for i in items})
                items = [self.item(r, self.check_col) for r in rows]
            items = [i for i in items if i is not None and i.flags() & Qt.ItemIsUserCheckable]
            if items:
                new = Qt.Unchecked if all(i.checkState() == Qt.Checked for i in items) else Qt.Checked
                for i in items:
                    i.setCheckState(new)
                return
        super().keyPressEvent(e)

    def set_all(self, mode):
        """mode: 'all' | 'none' | 'invert'. Emits one change at the end (itemChanged of the last item)."""
        items = [i for i in self.check_items() if i.flags() & Qt.ItemIsUserCheckable]
        if not items:
            return
        self.blockSignals(True)
        for i in items:
            if mode == "all":
                i.setCheckState(Qt.Checked)
            elif mode == "none":
                i.setCheckState(Qt.Unchecked)
            else:
                i.setCheckState(Qt.Unchecked if i.checkState() == Qt.Checked else Qt.Checked)
        self.blockSignals(False)
        self.itemChanged.emit(items[-1])            # listeners update once
        self.viewport().update()


class CheckList(ClickToggleMixin, QListWidget):
    """QListWidget with click-anywhere toggling and set_all()."""


def check_bar(view, label=None):
    """Small "All · None · Invert" button row for a ClickToggleMixin list / table, with a live
    "n of m selected" count."""
    from PySide6.QtWidgets import QLabel
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    if label:
        from ui.cable_dialog import _muted
        h.addWidget(_muted(label))
    for text, mode, tip in (("All", "all", "Tick every row"), ("None", "none", "Untick every row"),
                            ("Invert", "invert", "Swap ticked and unticked rows")):
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setMaximumWidth(70)
        b.clicked.connect(lambda _=False, m=mode: view.set_all(m))
        h.addWidget(b)
    count = QLabel("")
    count.setObjectName("muted")
    h.addSpacing(10)
    h.addWidget(count)
    h.addStretch()

    alive = {"on": True}

    def refresh(*_):
        # The model can still emit (rowsRemoved, modelReset …) while the list or the dialog holding it is being
        # destroyed — e.g. when a dialog rebuilds its lists after a route is imported. Never touch dead widgets.
        if not alive["on"]:
            return
        try:
            from shiboken6 import isValid
            if not (isValid(view) and isValid(count)):
                alive["on"] = False
                return
            items = [i for i in view.check_items() if i is not None and i.flags() & Qt.ItemIsUserCheckable]
            n = sum(1 for i in items if i.checkState() == Qt.Checked)
            count.setText(f"{n} of {len(items)} selected")
        except RuntimeError:                       # deleted between the check and the call
            alive["on"] = False

    def _dead(*_):
        alive["on"] = False

    view.destroyed.connect(_dead)
    count.destroyed.connect(_dead)
    m = view.model()
    for sig in (m.dataChanged, m.rowsInserted, m.rowsRemoved, m.modelReset, m.layoutChanged):
        sig.connect(refresh)
    view.itemChanged.connect(refresh)
    refresh()
    w.refresh_count = refresh
    return w
