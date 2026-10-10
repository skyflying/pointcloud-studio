"""Mouse interaction for selection modes: navigate / pick / box / lasso."""
from __future__ import annotations

import math

import numpy as np

from PySide6.QtCore import Qt
from vispy.util import keys

from render.overlay import SelectionOverlay

MODES = ("navigate", "pick", "box", "lasso", "section", "subset")


class SelectionController:
    """Translates mouse gestures into selection requests.

    on_select(kind, data, op) is called with:
      ("pick", (x, y), op) · ("polygon", [(x, y), ...], op) · ("clear", None, "replace")
    op ∈ {"replace", "add", "subtract"}: Shift = add, Ctrl / Alt = subtract.
    """

    def __init__(self, viewer, on_select):
        self.viewer = viewer
        self.on_select = on_select
        self.mode = "navigate"
        self.overlay = SelectionOverlay(viewer.widget)
        self._press = None
        self._path = []
        # section drawing
        self.draft = []                 # world XY vertices of the line being drawn
        self.cursor_xy = None
        self._vdrag = None
        self.on_section_draft = None    # callback(draft, cursor_xy)
        self.on_section_finish = None   # callback(list of world xy)
        self.get_section_vertices = None
        self.on_vertex_moved = None     # callback(i, xy, final)
        # subset drawing (drag the long axis in plan view)
        self._sub_p0 = None
        self.on_subset_draft = None     # callback(p0, p1) or (None, None) to clear
        self.on_subset_finish = None    # callback(p0, p1)
        ev = viewer.canvas.events
        ev.mouse_double_click.connect(self._on_double)
        ev.mouse_press.connect(self._on_press)
        ev.mouse_move.connect(self._on_move)
        ev.mouse_release.connect(self._on_release)

    # ---------- state ----------
    def set_mode(self, mode):
        assert mode in MODES
        self.cancel()
        self.mode = mode
        self.viewer.camera.block_left = mode != "navigate"
        self.viewer.widget.setCursor(Qt.ArrowCursor if mode == "navigate" else Qt.CrossCursor)

    @property
    def drawing(self):
        return self._press is not None or self._sub_p0 is not None

    def cancel(self):
        self._press = None
        self._path = []
        self.overlay.clear()
        if self._sub_p0 is not None:
            self._sub_p0 = None
            if self.on_subset_draft:
                self.on_subset_draft(None, None)
        had = bool(self.draft)
        self.draft = []
        self.cursor_xy = None
        self._vdrag = None
        if had and self.on_section_draft:
            self.on_section_draft([], None)

    @property
    def section_drawing(self):
        return bool(self.draft)

    def remove_last(self):
        if self.draft:
            self.draft.pop()
            self.on_section_draft(self.draft, self.cursor_xy)

    def finish_section(self):
        if len(self.draft) >= 2 and self.on_section_finish:
            pts = list(self.draft)
            self.draft = []
            self.cursor_xy = None
            self.on_section_draft([], None)
            self.on_section_finish(pts)

    @staticmethod
    def _op(mods):
        if keys.SHIFT in mods:
            return "add"
        if keys.CONTROL in mods or keys.ALT in mods or keys.META in mods:
            return "subtract"
        return "replace"

    # ---------- mouse ----------
    def _on_double(self, e):
        if self.mode == "section" and len(self.draft) >= 2:
            self.finish_section()

    def _on_press(self, e):
        if self.mode == "navigate" or e.button != 1:
            return
        if self.mode == "subset":
            self._sub_p0 = self.viewer.screen_to_xy(e.pos)
            return
        if self.mode == "section" and not self.draft and self.get_section_vertices is not None:
            verts = self.get_section_vertices()
            if verts is not None and len(verts):
                sp = self.viewer.xy_to_screen(verts)
                d = np.hypot(sp[:, 0] - e.pos[0], sp[:, 1] - e.pos[1])
                if d.min() <= 10:
                    self._vdrag = int(d.argmin())
                    return
        self._press = (float(e.pos[0]), float(e.pos[1]))
        self._path = [self._press]

    def _on_move(self, e):
        if self.mode == "subset":
            if self._sub_p0 is not None:
                p1 = self.viewer.screen_to_xy(e.pos)
                if p1 is not None:
                    self.on_subset_draft(self._sub_p0, p1)
            return
        if self.mode == "section":
            if self._vdrag is not None:
                xy = self.viewer.screen_to_xy(e.pos)
                if xy is not None:
                    self.on_vertex_moved(self._vdrag, xy, False)
            elif self.draft:
                self.cursor_xy = self.viewer.screen_to_xy(e.pos)
                self.on_section_draft(self.draft, self.cursor_xy)
            return
        if self._press is None:
            return
        pos = (float(e.pos[0]), float(e.pos[1]))
        if self.mode == "box":
            self.overlay.set_rect(self._press, pos)
        elif self.mode == "lasso":
            lx, ly = self._path[-1]
            if math.hypot(pos[0] - lx, pos[1] - ly) >= 2.0:
                self._path.append(pos)
                self.overlay.set_path(self._path)

    def _on_release(self, e):
        if self.mode == "subset" and e.button == 1:
            p0, self._sub_p0 = self._sub_p0, None
            p1 = self.viewer.screen_to_xy(e.pos)
            self.on_subset_draft(None, None)
            if p0 is not None and p1 is not None and np.hypot(*(p1 - p0)) > 1e-3:
                s0 = self.viewer.xy_to_screen(p0)[0]
                if math.hypot(s0[0] - e.pos[0], s0[1] - e.pos[1]) > 5:
                    self.on_subset_finish(p0, p1)
            return
        if self.mode == "section" and e.button == 1:
            if self._vdrag is not None:
                xy = self.viewer.screen_to_xy(e.pos)
                i, self._vdrag = self._vdrag, None
                if xy is not None:
                    self.on_vertex_moved(i, xy, True)
                self._press = None
                return
            if self._press is None:
                return
            p0 = self._press
            self._press = None
            if math.hypot(e.pos[0] - p0[0], e.pos[1] - p0[1]) > 3.0:
                return                                  # a drag, not a click
            xy = self.viewer.screen_to_xy(e.pos)
            if xy is None:
                return
            if self.draft:
                last = self.viewer.xy_to_screen(self.draft[-1])[0]
                if math.hypot(last[0] - e.pos[0], last[1] - e.pos[1]) < 4:
                    return                              # duplicate click (e.g. double-click)
            self.draft.append(xy)
            self.cursor_xy = xy
            self.on_section_draft(self.draft, self.cursor_xy)
            return
        if self._press is None or e.button != 1:
            return
        p0 = self._press
        p1 = (float(e.pos[0]), float(e.pos[1]))
        path = self._path
        op = self._op(e.modifiers)
        # a closed lasso ends near its start, so measure the farthest point reached
        moved = max(math.hypot(x - p0[0], y - p0[1]) for x, y in path + [p1]) > 3.0
        self.cancel()

        if self.mode == "pick":
            if not moved:
                self.on_select("pick", p1, op)
        elif self.mode == "box" and moved:
            (x0, y0), (x1, y1) = p0, p1
            self.on_select("polygon", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], op)
        elif self.mode == "lasso" and moved and len(path) >= 3:
            self.on_select("polygon", path + [p1], op)
        elif not moved and op == "replace":
            self.on_select("clear", None, "replace")
