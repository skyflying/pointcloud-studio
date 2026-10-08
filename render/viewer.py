"""VisPy 3D point cloud view.

Navigate mode: left drag rotate · right drag / wheel zoom · Shift+left or middle drag pan.
Selection modes: left button is reserved for selecting; wheel, right and middle still work.
"""
from __future__ import annotations

import numpy as np
from vispy import scene
from vispy.visuals.transforms import STTransform

from render.colormap import (
    AUTO_PERCENTILES, DEFAULT_PALETTE, SCALAR_MODES, ColorScale, auto_range, compute_colors,
    full_range, map_scalar, scalar_values,
)
from render.surfaces import SurfaceLayer, dem_mesh, tin_mesh
from ui.theme import SELECTION_RGBA, TRASH_DIM_RGBA, TRASH_HI_RGBA, VIEWPORT_BG

VIEW_PRESETS = {
    "top": (90.0, 0.0),
    "front": (0.0, 0.0),
    "side": (0.0, 90.0),
    "iso": (30.0, 45.0),
}


class _Camera(scene.cameras.TurntableCamera):
    """Turntable camera that can yield the left button to selection tools
    and supports middle-button panning."""

    block_left = False

    def viewbox_key_event(self, event):
        # VisPy resets the camera on Backspace; keyboard is handled by the app's shortcuts
        return

    def viewbox_mouse_event(self, event):
        if self.block_left:
            if event.type in ("mouse_press", "mouse_release") and event.button == 1:
                return
            if event.type == "mouse_move" and 1 in (event.buttons or ()):
                return
        if (event.type == "mouse_move" and event.press_event is not None
                and 3 in (event.buttons or ()) and self.interactive):
            self._pan(event)
            return
        super().viewbox_mouse_event(event)

    def _pan(self, event):
        p1 = event.mouse_event.press_event.pos
        p2 = event.mouse_event.pos
        norm = np.mean(self._viewbox.size)
        if self._event_value is None or len(self._event_value) == 2:
            self._event_value = self.center
        dist = (p1 - p2) / norm * self._scale_factor
        dist[1] *= -1
        dx, dy, dz = self._dist_to_trans(dist)
        ff = self._flip_factors
        up, forward, right = self._get_dim_vectors()
        dx, dy, dz = right * dx + forward * dy + up * dz
        dx, dy, dz = ff[0] * dx, ff[1] * dy, dz * ff[2]
        c = self._event_value
        self.center = c[0] + dx, c[1] + dy, c[2] + dz


class PointCloudViewer:
    def __init__(self, parent=None):
        # keys=None: the "interactive" preset closes the canvas on Esc
        self.canvas = scene.SceneCanvas(keys=None, bgcolor=VIEWPORT_BG, parent=parent, show=False)
        self.view = self.canvas.central_widget.add_view()
        self.camera = _Camera(fov=45.0, up="z")
        self.view.camera = self.camera

        self.markers = scene.visuals.Markers(parent=self.view.scene)
        self.markers.set_gl_state("opaque", depth_test=True)
        self.markers.order = 0
        self.trash_markers = scene.visuals.Markers(parent=self.view.scene)
        self.trash_markers.set_gl_state("translucent", depth_test=False)
        self.trash_markers.order = 1
        self.sel_markers = scene.visuals.Markers(parent=self.view.scene)
        self.sel_markers.set_gl_state("translucent", depth_test=False)
        self.sel_markers.order = 2
        for m in (self.markers, self.trash_markers, self.sel_markers):
            try:
                m.antialias = 0
            except Exception:
                pass
            m.visible = False

        self.sec_lines = scene.visuals.Line(parent=self.view.scene, method="gl", width=2.0)
        self.sec_lines.set_gl_state("translucent", depth_test=False)
        self.sec_lines.order = 3
        self.sec_lines.visible = False
        self.sec_verts = scene.visuals.Markers(parent=self.view.scene)
        self.sec_verts.set_gl_state("translucent", depth_test=False)
        self.sec_verts.order = 4
        self.sec_verts.visible = False

        self.surfaces = {"tin": SurfaceLayer(self.view.scene, "tin"),
                         "dem": SurfaceLayer(self.view.scene, "dem")}

        self.pc = None
        self.display_idx = np.empty(0, dtype=np.int64)
        self.sel_idx = np.empty(0, dtype=np.int64)
        self._pos = self._colors = self._sel_pos = None
        self._trash_pos = self._trash_colors = None
        self.color_mode = "elevation"
        self.scales = {m: ColorScale(palette=DEFAULT_PALETTE[m]) for m in SCALAR_MODES}
        self._cvals = None             # scalar values of displayed points (fast recolor)
        self.on_scale_changed = None   # callback(mode, ColorScale)
        self.point_size = 2.0
        self.z_scale = 1.0
        self.max_display = 10_000_000

    @property
    def widget(self):
        return self.canvas.native

    # ---------- data ----------
    def set_pointcloud(self, pc):
        self.pc = pc
        for sc in self.scales.values():
            sc.auto, sc.vmin, sc.vmax = True, None, None
        for layer in self.surfaces.values():
            layer.clear()
        self.set_selection(np.empty(0, dtype=np.int64))
        self.set_trash_view(np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64))
        self.refresh(reset_camera=True)

    def _decimate(self, idx):
        if len(idx) <= self.max_display:
            return idx
        rng = np.random.default_rng(0)
        return idx[rng.random(len(idx), dtype=np.float32) < self.max_display / len(idx)]

    def refresh(self, reset_camera=False):
        pc = self.pc
        if pc is None:
            return
        idx = self._decimate(pc.active_indices())
        self.display_idx = idx
        if len(idx) == 0:
            self.markers.visible = False
            self.canvas.update()
            return
        self._pos = pc.local_xyz(idx)
        self._prepare_colors()
        self._upload()
        self.markers.visible = True
        if reset_camera:
            self.fit_view()
        if self.color_mode == "elevation":
            self.recolor_surfaces()
        self.canvas.update()
        self._notify()

    def _upload(self):
        self.markers.set_data(self._pos, face_color=self._colors, size=self.point_size,
                              edge_width=0, edge_color=self._colors)

    # ---------- selection highlight ----------
    def set_selection(self, idx):
        """Highlight the given point indices (drawn on top, always visible)."""
        self.sel_idx = self._decimate(np.asarray(idx))
        if self.pc is None or len(self.sel_idx) == 0:
            self._sel_pos = None
            self.sel_markers.visible = False
        else:
            self._sel_pos = self.pc.local_xyz(self.sel_idx)
            self._upload_sel()
            self.sel_markers.visible = True
        self.canvas.update()

    def _upload_sel(self):
        size = self.point_size + 1.5 if len(self.sel_idx) > 1 else max(self.point_size * 3, 9)
        self.sel_markers.set_data(self._sel_pos, face_color=SELECTION_RGBA, size=size,
                                  edge_width=0, edge_color=SELECTION_RGBA)

    # ---------- trash preview ----------
    def set_trash_view(self, dim_idx, hi_idx):
        """Ghost trashed points: dim_idx faint red, hi_idx bright orange (selected batches)."""
        dim_idx = self._decimate(np.asarray(dim_idx))
        hi_idx = self._decimate(np.asarray(hi_idx))
        if self.pc is None or len(dim_idx) + len(hi_idx) == 0:
            self._trash_pos = None
            self.trash_markers.visible = False
        else:
            idx = np.concatenate([dim_idx, hi_idx])
            colors = np.empty((len(idx), 4), np.float32)
            colors[:len(dim_idx)] = TRASH_DIM_RGBA
            colors[len(dim_idx):] = TRASH_HI_RGBA
            self._trash_pos = self.pc.local_xyz(idx)
            self._trash_colors = colors
            self._upload_trash()
            self.trash_markers.visible = True
        self.canvas.update()

    def _upload_trash(self):
        self.trash_markers.set_data(self._trash_pos, face_color=self._trash_colors,
                                    size=self.point_size + 0.5, edge_width=0,
                                    edge_color=self._trash_colors)

    # ---------- display ----------
    def set_color_mode(self, mode):
        self.color_mode = mode
        if self.pc is not None and len(self.display_idx):
            self._prepare_colors()
            self._upload()
            self.canvas.update()
        self._notify()

    # ---------- color scale ----------
    @property
    def scale_mode(self):
        if self.color_mode in SCALAR_MODES:
            return self.color_mode
        return "elevation" if self.any_surface_visible() else None

    @property
    def scale(self):
        return self.scales.get(self.scale_mode)

    def _active_values(self, mode=None):
        return scalar_values(self.pc, self.pc.active_indices(), mode or self.scale_mode)

    def _ensure_elev_scale(self):
        sc = self.scales["elevation"]
        if sc.vmin is None and self.pc is not None:
            self._apply_axis(sc, *auto_range(self._active_values("elevation"), *AUTO_PERCENTILES["elevation"]))
        return sc

    @staticmethod
    def _apply_axis(sc, lo, hi):
        """Set the colour-bar scale limits. A display range spanning the whole old scale follows the
        new scale; a narrowed display range is kept but clamped inside it."""
        lo, hi = (lo, hi) if lo <= hi else (hi, lo)
        if hi - lo < 1e-9:
            hi = lo + 1e-6
        full = (sc.vmin is None or sc.amin is None
                or (abs(sc.vmin - sc.amin) < 1e-9 and abs(sc.vmax - sc.amax) < 1e-9))
        sc.amin, sc.amax = float(lo), float(hi)
        if full:
            sc.vmin, sc.vmax = sc.amin, sc.amax
        else:
            sc.vmin = min(max(sc.vmin, lo), hi)
            sc.vmax = min(max(sc.vmax, lo), hi)
            if sc.vmax - sc.vmin < (hi - lo) * 1e-3:
                sc.vmin, sc.vmax = sc.amin, sc.amax

    def _prepare_colors(self):
        """(Re)compute colors of displayed points for the current mode."""
        sc = self.scales.get(self.color_mode)
        self._cvals = scalar_values(self.pc, self.display_idx, self.color_mode) if sc else None
        if self._cvals is not None:
            if sc.auto or sc.vmin is None:
                self._apply_axis(sc, *auto_range(self._active_values(self.color_mode),
                                                 *AUTO_PERCENTILES[self.color_mode]))
            self._colors = map_scalar(self._cvals, sc)
        else:
            self._colors = compute_colors(self.pc, self.display_idx, self.color_mode)

    def recolor(self):
        """Fast path: re-map cached scalar values after a range / palette change."""
        if self._cvals is not None and self._pos is not None and self.color_mode in SCALAR_MODES:
            self._colors = map_scalar(self._cvals, self.scale)
            self._upload()
        if self.scale_mode == "elevation":
            self.recolor_surfaces()
        self.canvas.update()

    # ---------- surface layers ----------
    def any_surface_visible(self):
        return any(l.V is not None and l.mesh.visible for l in self.surfaces.values())

    def set_surface(self, kind, obj):
        layer = self.surfaces[kind]
        if obj is None:
            layer.clear()
        else:
            V, F, z = tin_mesh(obj, self.pc.offset) if kind == "tin" else dem_mesh(obj, self.pc.offset)
            layer.mesh.transform = STTransform(scale=(1.0, 1.0, self.z_scale))
            layer.set_data(V, F, z, self._ensure_elev_scale(), self.z_scale)
            layer.mesh.visible = True
        self.canvas.update()
        self._notify()

    def recolor_surfaces(self):
        sc = self._ensure_elev_scale()
        for layer in self.surfaces.values():
            if layer.V is not None:
                layer.recolor(sc)

    def set_layer_visible(self, name, on):
        if name == "points":
            self.markers.visible = bool(on) and len(self.display_idx) > 0
        else:
            self.surfaces[name].mesh.visible = bool(on) and self.surfaces[name].V is not None
        self.canvas.update()
        self._notify()

    def set_layer_opacity(self, name, a):
        self.surfaces[name].set_opacity(a, self._ensure_elev_scale())
        self.canvas.update()

    def set_wireframe(self, name, on):
        self.surfaces[name].wire.enabled = bool(on)
        self.canvas.update()

    def set_hillshade(self, on):
        for layer in self.surfaces.values():
            layer.shading = bool(on)
            layer.update_shade(self.z_scale, self._ensure_elev_scale())
        self.canvas.update()

    def set_color_scale(self, **changes):
        """amin/amax: colour-bar scale limits (Shallow/Deep). vmin/vmax: display range (handles)."""
        sc = self.scale
        if sc is None:
            return
        if "amin" in changes or "amax" in changes:
            self._apply_axis(sc, changes.pop("amin", sc.amin), changes.pop("amax", sc.amax))
            sc.auto = False
        if "vmin" in changes or "vmax" in changes:
            lo = changes.pop("vmin", sc.vmin)
            hi = changes.pop("vmax", sc.vmax)
            lo, hi = (lo, hi) if lo <= hi else (hi, lo)
            lo = min(max(lo, sc.amin), sc.amax)
            hi = min(max(hi, sc.amin), sc.amax)
            if hi - lo < (sc.amax - sc.amin) * 1e-3:
                hi = min(sc.amax, lo + (sc.amax - sc.amin) * 1e-3)
            sc.vmin, sc.vmax = float(lo), float(hi)
        for k, v in changes.items():
            setattr(sc, k, v)
        self.recolor()
        self._notify()

    def reset_display(self):
        """Handles back to the full scale."""
        sc = self.scale
        if sc is not None and sc.amin is not None:
            sc.vmin, sc.vmax = sc.amin, sc.amax
            self.recolor()
            self._notify()

    def reset_range(self, kind="auto"):
        sc = self.scale
        if sc is None or self.pc is None:
            return
        vals = self._active_values()
        lo, hi = auto_range(vals, *AUTO_PERCENTILES[self.scale_mode]) if kind == "auto" else full_range(vals)
        sc.amin, sc.amax, sc.vmin, sc.vmax = lo, hi, lo, hi
        sc.auto = kind == "auto"
        self.recolor()
        self._notify()

    def data_range(self):
        return full_range(self._active_values()) if self.scale and self.pc is not None else (0.0, 1.0)

    def _notify(self):
        if self.on_scale_changed is not None:
            mode = self.scale_mode
            if mode == "elevation":
                self._ensure_elev_scale()
            self.on_scale_changed(mode, self.scale)

    def set_point_size(self, size):
        self.point_size = float(size)
        if self._pos is not None:
            self._upload()
        if self._sel_pos is not None:
            self._upload_sel()
        if self._trash_pos is not None:
            self._upload_trash()
        self.canvas.update()

    def set_z_scale(self, z):
        self.z_scale = float(z)
        for m in ([self.markers, self.trash_markers, self.sel_markers, self.sec_lines, self.sec_verts]
                  + [l.mesh for l in self.surfaces.values()]):
            m.transform = STTransform(scale=(1.0, 1.0, self.z_scale))
        for layer in self.surfaces.values():
            layer.update_shade(self.z_scale, self._ensure_elev_scale())
        self.fit_view()

    def set_max_display(self, n):
        self.max_display = int(n)
        self.refresh()

    # ---------- plan-view picking & section overlay ----------
    @property
    def overlay_z(self):
        """Local Z of the drawing plane: just above the top of the data."""
        return float(self.pc.bbox_max[2] - self.pc.offset[2]) if self.pc is not None else 0.0

    def screen_to_xy(self, pos):
        """World XY where the view ray through a canvas position meets the drawing plane."""
        if self.pc is None:
            return None
        tr = self.markers.get_transform("visual", "canvas")
        a = np.asarray(tr.imap([pos[0], pos[1], -0.5, 1.0]), dtype=np.float64)
        b = np.asarray(tr.imap([pos[0], pos[1], 0.5, 1.0]), dtype=np.float64)
        a, b = a[:3] / a[3], b[:3] / b[3]
        if abs(b[2] - a[2]) < 1e-12:
            return None
        t = (self.overlay_z - a[2]) / (b[2] - a[2])
        q = a + t * (b - a)
        return np.array([q[0] + self.pc.offset[0], q[1] + self.pc.offset[1]])

    def xy_to_screen(self, xy):
        xy = np.atleast_2d(xy)
        tr = self.markers.get_transform("visual", "canvas")
        pts = np.column_stack([xy[:, 0] - self.pc.offset[0], xy[:, 1] - self.pc.offset[1],
                               np.full(len(xy), self.overlay_z)])
        m = np.asarray(tr.map(pts))
        return m[:, :2] / m[:, 3:4]

    def set_section_overlay(self, sections, active, draft=None, cursor=None):
        """sections: list of (xy, width). Active one drawn with its corridor; draft = in-progress line."""
        if self.pc is None:
            return
        off = self.pc.offset[:2]
        z = self.overlay_z
        pos, col, verts = [], [], []

        def add_poly(xy, color):
            for a, b in zip(xy[:-1], xy[1:]):
                pos.extend([a, b])
                col.extend([color, color])

        for i, (xy, width) in enumerate(sections):
            if i == active:
                add_poly(xy, (1.0, 0.62, 0.26, 1.0))
                d = np.diff(xy, axis=0)
                n = np.column_stack([-d[:, 1], d[:, 0]]) / np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
                for k in range(len(d)):
                    for sgn in (1, -1):
                        add_poly(np.array([xy[k] + sgn * width * n[k], xy[k + 1] + sgn * width * n[k]]),
                                 (1.0, 0.62, 0.26, 0.45))
                verts.extend(xy)
            else:
                add_poly(xy, (0.75, 0.77, 0.82, 0.7))
        if draft is not None and len(draft):
            line = np.array(list(draft) + ([cursor] if cursor is not None else []))
            add_poly(line, (0.30, 0.55, 1.0, 1.0))
            verts.extend(draft)
        if pos:
            P = np.asarray(pos, dtype=np.float64)
            P3 = np.column_stack([P[:, 0] - off[0], P[:, 1] - off[1], np.full(len(P), z)]).astype(np.float32)
            self.sec_lines.set_data(pos=P3, color=np.asarray(col, np.float32), connect="segments")
            self.sec_lines.visible = True
        else:
            self.sec_lines.visible = False
        if verts:
            V = np.asarray(verts, dtype=np.float64)
            V3 = np.column_stack([V[:, 0] - off[0], V[:, 1] - off[1], np.full(len(V), z)]).astype(np.float32)
            self.sec_verts.set_data(V3, face_color=(1, 1, 1, 1), edge_color=(1.0, 0.62, 0.26, 1),
                                    edge_width=2, size=9)
            self.sec_verts.visible = True
        else:
            self.sec_verts.visible = False
        self.canvas.update()

    # ---------- camera ----------
    def fit_view(self):
        if self.pc is None:
            return
        lo = self.pc.bbox_min - self.pc.offset
        hi = self.pc.bbox_max - self.pc.offset
        center = (lo + hi) / 2.0
        ext = hi - lo
        center[2] *= self.z_scale
        ext[2] *= self.z_scale
        self.camera.center = tuple(float(v) for v in center)
        self.camera.scale_factor = float(max(np.linalg.norm(ext), 1e-3))
        self.canvas.update()

    def set_view(self, name):
        elev, azim = VIEW_PRESETS[name]
        self.camera.elevation = elev
        self.camera.azimuth = azim
        self.fit_view()
