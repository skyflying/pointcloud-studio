"""VisPy 3D point cloud view.

Navigate mode: left drag rotate · right drag / wheel zoom · Shift+left or middle drag pan.
Selection modes: left button is reserved for selecting; wheel, right and middle still work.
"""
from __future__ import annotations

import numpy as np
from vispy import scene
from vispy.visuals.transforms import STTransform

from render.colormap import (
    AUTO_PERCENTILES, symmetric_range, DEFAULT_PALETTE, SCALAR_MODES, ColorScale, auto_range, compute_colors,
    full_range, map_scalar, scalar_values,
)
from render.surfaces import SurfaceLayer, dem_mesh, mesh_mesh, tin_mesh
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

        self.sub_lines = scene.visuals.Line(parent=self.view.scene, method="gl", width=2.0)
        self.sub_lines.set_gl_state("translucent", depth_test=False)
        self.sub_lines.order = 5
        self.sub_lines.visible = False
        self.sub_fill = scene.visuals.Mesh(parent=self.view.scene)
        self.sub_fill.set_gl_state("translucent", depth_test=False)
        self.sub_fill.order = 3
        self.sub_fill.visible = False

        self.route_lines = scene.visuals.Line(parent=self.view.scene, method="gl", width=2.5)
        self.route_lines.set_gl_state("translucent", depth_test=False)
        self.route_lines.order = 4
        self.route_lines.visible = False
        self.route_text = scene.visuals.Text(parent=self.view.scene, color=(0.30, 0.79, 0.94, 1.0),
                                             font_size=9, anchor_x="left", anchor_y="bottom")
        self.route_text.set_gl_state("translucent", depth_test=False)
        self.route_text.order = 6
        self.route_text.visible = False
        # structures (WTG / OSS): circle of the foundation diameter + name
        self.str_lines = scene.visuals.Line(parent=self.view.scene, method="gl", width=2.0)
        self.str_lines.set_gl_state("translucent", depth_test=False)
        self.str_lines.order = 4
        self.str_lines.visible = False
        self.str_text = scene.visuals.Text(parent=self.view.scene, color=(1.0, 0.62, 0.26, 1.0), font_size=9,
                                           anchor_x="left", anchor_y="bottom")
        self.str_text.set_gl_state("translucent", depth_test=False)
        self.str_text.order = 6
        self.str_text.visible = False
        # vector reference layers (GIS / CAD): violet lines, polygon outlines, point crosses
        self.vec_lines = scene.visuals.Line(parent=self.view.scene, method="gl", width=1.6)
        self.vec_lines.set_gl_state("translucent", depth_test=False)
        self.vec_lines.order = 4
        self.vec_lines.visible = False
        # analysis results along routes (DoB / free spans / berm): coloured polylines + labels
        self.ana_lines = scene.visuals.Line(parent=self.view.scene, method="gl", width=5.0)
        self.ana_lines.set_gl_state("translucent", depth_test=False)
        self.ana_lines.order = 7
        self.ana_lines.visible = False
        self.ana_text = scene.visuals.Text(parent=self.view.scene, color=(1.0, 1.0, 1.0, 1.0), font_size=9,
                                           anchor_x="left", anchor_y="bottom")
        self.ana_text.set_gl_state("translucent", depth_test=False)
        self.ana_text.order = 8
        self.ana_text.visible = False

        self.surfaces = {"tin": SurfaceLayer(self.view.scene, "tin"),
                         "dem": SurfaceLayer(self.view.scene, "dem")}

        self.pc = None                  # the bound (editable / primary) point cloud, may be None
        self.origin = None              # shared project display origin (world coords)
        self.bbox = None                # project bounding box (lo, hi) for fitting the view
        self.refs = {}                  # reference point clouds: id → dict(pc, markers, idx, vals)
        self.contours = {}              # contour layers: id → Line visual
        self.contour_labels = {}        # id → Text visual (depth / elevation values)
        self._hgrid = None              # cached height map of everything displayed (for picking / draping)
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
    def set_pointcloud(self, pc, reset=True):
        """Bind the primary (editable) cloud. reset=True starts a new project view."""
        self._invalidate_height()
        self.pc = pc
        if reset:
            for sc in self.scales.values():
                sc.auto, sc.vmin, sc.vmax, sc.amin, sc.amax = True, None, None, None, None
            for layer in self.surfaces.values():
                layer.clear()
            for rid in list(self.refs):
                self.remove_reference(rid)
        if pc is not None and self.origin is not None:
            pc.offset = self.origin.copy()
        self.set_selection(np.empty(0, dtype=np.int64))
        self.set_trash_view(np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64))
        if pc is None:
            self.markers.visible = False
            self.display_idx = np.empty(0, dtype=np.int64)
            self._pos = self._cvals = None
            if reset:
                self.fit_view()
            self._notify()
            return
        self.refresh(reset_camera=reset)

    def set_origin(self, origin, bbox=None):
        self.origin = None if origin is None else np.asarray(origin, dtype=np.float64)
        self.bbox = bbox

    # ---------- reference point clouds (read-only layers) ----------
    def add_reference(self, rid, pc):
        self._invalidate_height()
        self.remove_reference(rid)
        m = scene.visuals.Markers(parent=self.view.scene)
        m.set_gl_state("opaque", depth_test=True, depth_range=self._depth_slice(rid))
        m.order = self._draw_order(rid, 0)
        try:
            m.antialias = 0
        except Exception:
            pass
        m.transform = STTransform(scale=(1.0, 1.0, self.z_scale))
        pc.offset = self.origin.copy()
        idx = self._decimate(pc.active_indices())
        self.refs[rid] = dict(pc=pc, markers=m, idx=idx, pos=pc.local_xyz(idx),
                              vals=pc.xyz[idx, 2].astype(np.float32), visible=True)
        self._recolor_ref(rid)
        self.canvas.update()
        self._notify()

    def remove_reference(self, rid):
        self._invalidate_height()
        r = self.refs.pop(rid, None)
        if r is not None:
            r["markers"].parent = None
            self.canvas.update()

    # ---------- layer order (Data panel: top of the list is drawn in front) ----------
    def _depth_slice(self, key):
        """Depth-buffer slice of a layer: the top layer gets the nearest slice, so it is drawn in front of
        the layers below it; inside a layer normal depth testing still applies."""
        ranks = getattr(self, "_layer_ranks", None) or {}
        if not getattr(self, "_layer_order_on", False) or key not in ranks:
            return (0.0, 1.0)
        n = max(len(ranks), 1)
        r = ranks[key]
        return (r / n, (r + 1) / n)

    def _draw_order(self, key, default):
        ranks = getattr(self, "_layer_ranks", None) or {}
        if key not in ranks:
            return default
        return -1 - ranks[key]          # bottom layer first (translucent layers blend over the ones below)

    def set_layer_order(self, ranks, bound_key=None, enabled=True):
        """ranks: {layer key: rank} for drawable layers (0 = top). bound_key: key of the edit-target cloud
        (drawn by self.markers). enabled False: plain 3D depth (layer order ignored)."""
        self._layer_ranks = dict(ranks)
        self._layer_order_on = bool(enabled)
        self._bound_key = bound_key
        self.markers.set_gl_state("opaque", depth_test=True, depth_range=self._depth_slice(bound_key))
        self.markers.order = self._draw_order(bound_key, 0)
        for rid, r in self.refs.items():
            r["markers"].set_gl_state("opaque", depth_test=True, depth_range=self._depth_slice(rid))
            r["markers"].order = self._draw_order(rid, 0)
        for key, layer in self.surfaces.items():
            layer.depth_range = self._depth_slice(key)
            layer.mesh.order = self._draw_order(key, -1)
            layer._set_gl()
        self.canvas.update()

    def set_reference_visible(self, rid, on):
        self._invalidate_height()
        if rid in self.refs:
            self.refs[rid]["visible"] = bool(on)
            self.refs[rid]["markers"].visible = bool(on)
            self.canvas.update()
            self._notify()

    def _recolor_ref(self, rid):
        r = self.refs[rid]
        c = map_scalar(r["vals"], self._ensure_elev_scale())
        r["markers"].set_data(r["pos"], face_color=c, size=self.point_size, edge_width=0, edge_color=c)
        r["markers"].visible = r["visible"]

    def _decimate(self, idx):
        if len(idx) <= self.max_display:
            return idx
        rng = np.random.default_rng(0)
        return idx[rng.random(len(idx), dtype=np.float32) < self.max_display / len(idx)]

    def refresh(self, reset_camera=False):
        self._invalidate_height()
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
        return "elevation" if (self.any_surface_visible() or self.any_reference_visible()) else None

    @property
    def scale(self):
        return self.scales.get(self.scale_mode)

    def any_reference_visible(self):
        return any(r["visible"] for r in self.refs.values())

    def _active_values(self, mode=None):
        mode = mode or self.scale_mode
        if self.pc is not None and (mode != "elevation" or self.color_mode == "elevation"
                                    or not (self.refs or self.any_surface_visible())):
            v = scalar_values(self.pc, self.pc.active_indices(), mode)
            if v is not None:
                return v
        # elevation of everything shown: bound cloud, reference clouds, surfaces
        parts = []
        if self.pc is not None:
            parts.append(self.pc.xyz[self.pc.active_indices()[::max(1, self.pc.n_active // 500_000)], 2])
        parts += [r["vals"][::max(1, len(r["vals"]) // 500_000)] for r in self.refs.values()]
        parts += [l.z[::max(1, len(l.z) // 500_000)] for l in self.surfaces.values() if l.z is not None]
        parts = [np.asarray(p, np.float32) for p in parts if len(p)]
        return np.concatenate(parts) if parts else np.array([0.0, 1.0], np.float32)

    def _ensure_elev_scale(self):
        sc = self.scales["elevation"]
        if sc.vmin is None and (self.pc is not None or self.refs
                                or any(l.z is not None for l in self.surfaces.values())):
            vals = self._active_values("elevation")
            self._apply_axis(sc, *auto_range(vals, *AUTO_PERCENTILES["elevation"]))
            self._auto_palette(sc, vals)
        return sc

    @staticmethod
    def _auto_palette(sc, vals):
        """Land + sea data (spanning 0 m): Topo-bathy, unless the user chose a palette."""
        if getattr(sc, "user_palette", False) or len(vals) == 0:
            return
        lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
        if lo < 0 < hi and sc.palette in ("Rainbow", "Topo-bathy"):
            sc.palette = "Topo-bathy"

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
                rng = symmetric_range if self.color_mode == "deviation" else auto_range
                vals = self._active_values(self.color_mode)
                self._apply_axis(sc, *rng(vals, *AUTO_PERCENTILES[self.color_mode]))
                if self.color_mode == "elevation":
                    self._auto_palette(sc, vals)
            self._colors = map_scalar(self._cvals, sc)
        else:
            self._colors = compute_colors(self.pc, self.display_idx, self.color_mode)
        self._mark_noise()

    NOISE_RGBA = (1.0, 0.33, 0.22, 1.0)

    def _mark_noise(self):
        pc = self.pc
        if pc is not None and pc.can_classify and not pc.hide_noise and self._colors is not None:
            nm = pc.noise_mask()[self.display_idx]
            if nm.any():
                self._colors[nm] = self.NOISE_RGBA

    def recolor(self):
        """Fast path: re-map cached scalar values after a range / palette change."""
        if self._cvals is not None and self._pos is not None and self.color_mode in SCALAR_MODES:
            self._colors = map_scalar(self._cvals, self.scale)
            self._mark_noise()
            self._upload()
        if self.scale_mode == "elevation":
            self.recolor_surfaces()
        self.canvas.update()

    # ---------- surface layers ----------
    def any_surface_visible(self):
        return any(l.V is not None and l.mesh.visible for l in self.surfaces.values())

    def set_surface(self, key, obj):
        """Show a Tin or Dem under `key` (any hashable, e.g. a dataset id); obj=None removes it."""
        self._invalidate_height()
        from core.surface import Tin
        if key not in self.surfaces:
            if obj is None:
                return
            self.surfaces[key] = SurfaceLayer(self.view.scene, str(key))
            self.surfaces[key].depth_range = self._depth_slice(key)
            self.surfaces[key].mesh.order = self._draw_order(key, -1)
            self.surfaces[key]._set_gl()
        layer = self.surfaces[key]
        if obj is None:
            layer.clear()
        else:
            rgb = None
            if obj.__class__.__name__ == "Mesh":
                V, F, z, rgb = mesh_mesh(obj, self.origin)
            else:
                V, F, z = tin_mesh(obj, self.origin) if isinstance(obj, Tin) else dem_mesh(obj, self.origin)
            layer.rgb = rgb
            layer.two_sided = obj.__class__.__name__ == "Mesh" and obj.meta.get("method") != "delaunay25d"
            if getattr(obj, "cvals", None) is not None:
                from render.colormap import ColorScale
                layer.cvals = obj.cvals.ravel().astype(np.float32)
                lo, hi = obj.crange
                layer.own_scale = ColorScale(palette=getattr(obj, "cpalette", "Diverging"), vmin=lo, vmax=hi,
                                             amin=lo, amax=hi, auto=False)
            else:
                layer.cvals = layer.own_scale = None
            layer.mesh.transform = STTransform(scale=(1.0, 1.0, self.z_scale))
            sc = self._ensure_elev_scale()
            if sc.vmin is None:
                # first data of the project is this surface (no point cloud yet): range from its heights
                zz = np.asarray(z, float)
                zz = zz[np.isfinite(zz)]
                if len(zz):
                    self._apply_axis(sc, *auto_range(zz, *AUTO_PERCENTILES["elevation"]))
                    self._auto_palette(sc, zz)
                else:
                    self._apply_axis(sc, 0.0, 1.0)
            layer.set_data(V, F, z, sc, self.z_scale)
            layer.mesh.visible = True
        self.canvas.update()
        self._notify()

    def recolor_surfaces(self):
        sc = self._ensure_elev_scale()
        for layer in self.surfaces.values():
            if layer.V is not None:
                layer.recolor(sc)
        for rid in self.refs:
            self._recolor_ref(rid)

    def set_layer_visible(self, name, on):
        self._invalidate_height()
        if name == "points":
            self.markers.visible = bool(on) and len(self.display_idx) > 0
        elif name in self.refs:
            self.set_reference_visible(name, on)
            return
        elif name in self.surfaces:
            self.surfaces[name].mesh.visible = bool(on) and self.surfaces[name].V is not None
        self.canvas.update()
        self._notify()

    def set_layer_opacity(self, name, a):
        self.surfaces[name].set_opacity(a, self._ensure_elev_scale())
        self.canvas.update()

    def set_wireframe(self, name, on):
        if name in self.surfaces:
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
        if "palette" in changes:
            sc.user_palette = True
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
        if kind == "auto":
            rng = symmetric_range if self.scale_mode == "deviation" else auto_range
            lo, hi = rng(vals, *AUTO_PERCENTILES[self.scale_mode])
        else:
            lo, hi = full_range(vals)
        sc.amin, sc.amax, sc.vmin, sc.vmax = lo, hi, lo, hi
        sc.auto = kind == "auto"
        self.recolor()
        self._notify()

    def data_range(self):
        return full_range(self._active_values()) if self.scale is not None else (0.0, 1.0)

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
        for rid in self.refs:
            self._recolor_ref(rid)
        self.canvas.update()

    def set_z_scale(self, z):
        self.z_scale = float(z)
        for m in ([self.markers, self.trash_markers, self.sel_markers, self.sec_lines, self.sec_verts]
                  + [l.mesh for l in self.surfaces.values()] + [r["markers"] for r in self.refs.values()]
                  + list(self.contours.values()) + list(self.contour_labels.values())
                  + [self.route_lines, self.route_text, self.ana_lines, self.ana_text, self.str_lines,
                     self.str_text, self.vec_lines]):
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
        if self.bbox is not None and self.origin is not None:
            return float(self.bbox[1][2] - self.origin[2])
        return float(self.pc.bbox_max[2] - self.pc.offset[2]) if self.pc is not None else 0.0

    # ---------- terrain height (picking + draping of drawn lines) ----------
    def _invalidate_height(self):
        self._hgrid = None

    def _height_grid(self):
        """Coarse height map (local coords) of everything shown: points, reference clouds, surfaces."""
        if self._hgrid is not None:
            return self._hgrid or None
        parts = []
        if self._pos is not None and self.markers.visible and len(self._pos):
            parts.append(np.asarray(self._pos[::max(1, len(self._pos) // 600_000)], np.float64))
        for r in self.refs.values():
            if r["visible"] and len(r["pos"]):
                parts.append(np.asarray(r["pos"][::max(1, len(r["pos"]) // 300_000)], np.float64))
        for l in self.surfaces.values():
            if l.V is not None and l.mesh.visible:
                parts.append(np.asarray(l.V[::max(1, len(l.V) // 300_000)], np.float64))
        if not parts:
            self._hgrid = False
            return None
        Pt = np.vstack(parts)
        lo, hi = Pt[:, :2].min(0), Pt[:, :2].max(0)
        cell = max(float((hi - lo).max()) / 600.0, 1e-6)
        nx, ny = int((hi[0] - lo[0]) / cell) + 1, int((hi[1] - lo[1]) / cell) + 1
        ix = ((Pt[:, 0] - lo[0]) / cell).astype(np.int64)
        iy = ((Pt[:, 1] - lo[1]) / cell).astype(np.int64)
        k = iy * nx + ix
        ssum = np.bincount(k, Pt[:, 2], nx * ny)
        cnt = np.bincount(k, None, nx * ny)
        g = np.where(cnt > 0, ssum / np.maximum(cnt, 1), np.nan).reshape(ny, nx)
        # fill small gaps from the nearest cell (≤ 3 cells: areas outside the data stay empty), then a light
        # NaN-aware 3×3 smoothing so draped lines do not pick up point noise
        from scipy import ndimage
        ok = np.isfinite(g)
        if ok.any() and not ok.all():
            d, (iy2, ix2) = ndimage.distance_transform_edt(~ok, return_indices=True)
            fill = (~ok) & (d <= 3)
            g[fill] = g[iy2[fill], ix2[fill]]
            ok = np.isfinite(g)
        num = ndimage.uniform_filter(np.where(ok, g, 0.0), 3)
        den = ndimage.uniform_filter(ok.astype(float), 3)
        g = np.where(ok, num / np.maximum(den, 1e-9), np.nan)
        self._hgrid = (lo, cell, g)
        return self._hgrid

    def _ground_local(self, x, y):
        """Height (local z) at local XY, NaN where nothing is displayed."""
        hg = self._height_grid()
        x, y = np.asarray(x, float), np.asarray(y, float)
        if hg is None:
            return np.full(np.shape(x), np.nan)
        lo, cell, g = hg
        ix = np.floor((x - lo[0]) / cell).astype(np.int64)
        iy = np.floor((y - lo[1]) / cell).astype(np.int64)
        ok = (ix >= 0) & (iy >= 0) & (ix < g.shape[1]) & (iy < g.shape[0])
        out = np.full(np.shape(x), np.nan)
        out[ok] = g[iy[ok], ix[ok]]
        return out

    def _fallback_z(self):
        """Plane used where nothing is displayed: middle of the data (same as screen_to_xy)."""
        hg = self._height_grid()
        return float(np.nanmedian(hg[2])) if hg is not None else self.overlay_z

    def _drape_z(self, x, y):
        """Overlay height: on the displayed terrain, else the fallback plane."""
        z = self._ground_local(x, y)
        return np.where(np.isfinite(z), z, self._fallback_z())

    def _drape_pairs(self, pairs_world):
        """Segment pairs (world XY, flat list a,b,a,b…) → densified local XYZ pairs following the terrain."""
        P = np.asarray(pairs_world, float).reshape(-1, 2, 2) - self.origin[:2]
        hg = self._height_grid()
        step = hg[1] if hg is not None else None
        out = []
        for a, b in P:
            n = 1 if step is None else int(min(max(np.hypot(*(b - a)) / step, 1), 400))
            t = np.linspace(0, 1, n + 1)
            seg = a + (b - a) * t[:, None]
            z = self._drape_z(seg[:, 0], seg[:, 1])
            q = np.column_stack([seg, z])
            out.append(np.repeat(q, 2, axis=0)[1:-1])
        return np.vstack(out) if out else np.empty((0, 3))

    def screen_to_xy(self, pos):
        """World XY of the terrain point under a canvas position (ray traced into the displayed data);
        falls back to a horizontal plane through the middle of the data."""
        if self.origin is None:
            return None
        tr = self.markers.get_transform("visual", "canvas")
        a = np.asarray(tr.imap([pos[0], pos[1], -0.5, 1.0]), dtype=np.float64)
        b = np.asarray(tr.imap([pos[0], pos[1], 0.5, 1.0]), dtype=np.float64)
        a, b = a[:3] / a[3], b[:3] / b[3]
        if abs(b[2] - a[2]) < 1e-12:
            return None
        hg = self._height_grid()
        if hg is not None:
            g = hg[2]
            zmin, zmax = float(np.nanmin(g)), float(np.nanmax(g))
            t0, t1 = sorted(((zmax - a[2]) / (b[2] - a[2]), (zmin - a[2]) / (b[2] - a[2])))
            ts = np.linspace(t0, t1, 2000)
            Q = a + ts[:, None] * (b - a)
            h = self._ground_local(Q[:, 0], Q[:, 1])
            f = Q[:, 2] - h                                  # > 0 above the terrain
            hit = np.flatnonzero(np.isfinite(f) & (f <= 0))
            if len(hit):
                i = hit[0]
                if i > 0 and np.isfinite(f[i - 1]) and f[i - 1] > 0:
                    w = f[i - 1] / (f[i - 1] - f[i])
                    q = Q[i - 1] + w * (Q[i] - Q[i - 1])
                else:
                    q = Q[i]
                return np.array([q[0] + self.origin[0], q[1] + self.origin[1]])
            zp = self._fallback_z()
        else:
            zp = self.overlay_z
        t = (zp - a[2]) / (b[2] - a[2])
        q = a + t * (b - a)
        return np.array([q[0] + self.origin[0], q[1] + self.origin[1]])

    def xy_to_screen(self, xy):
        xy = np.atleast_2d(xy)
        tr = self.markers.get_transform("visual", "canvas")
        lx, ly = xy[:, 0] - self.origin[0], xy[:, 1] - self.origin[1]
        pts = np.column_stack([lx, ly, self._drape_z(lx, ly)])
        m = np.asarray(tr.map(pts))
        return m[:, :2] / m[:, 3:4]

    def set_section_overlay(self, sections, active, draft=None, cursor=None):
        """sections: list of (xy, width). Active one drawn with its corridor; draft = in-progress line."""
        if self.origin is None:
            return
        off = self.origin[:2]
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
            P3, C3 = [], []
            for k in range(0, len(pos), 2):
                seg = self._drape_pairs([pos[k], pos[k + 1]])
                P3.append(seg)
                C3.append(np.tile(col[k], (len(seg), 1)))
            self.sec_lines.set_data(pos=np.vstack(P3).astype(np.float32), color=np.vstack(C3).astype(np.float32),
                                    connect="segments")
            self.sec_lines.visible = True
        else:
            self.sec_lines.visible = False
        if verts:
            V = np.asarray(verts, dtype=np.float64)
            vx, vy = V[:, 0] - off[0], V[:, 1] - off[1]
            V3 = np.column_stack([vx, vy, self._drape_z(vx, vy)]).astype(np.float32)
            self.sec_verts.set_data(V3, face_color=(1, 1, 1, 1), edge_color=(1.0, 0.62, 0.26, 1),
                                    edge_width=2, size=9)
            self.sec_verts.visible = True
        else:
            self.sec_verts.visible = False
        self.canvas.update()

    # ---------- contour layers ----------
    def set_contours(self, cid, cs, depth=False):
        old = self.contours.pop(cid, None)
        if old is not None:
            old.parent = None
        oldt = self.contour_labels.pop(cid, None)
        if oldt is not None:
            oldt.parent = None
        if cs is None or self.origin is None or not cs.lines:
            self.canvas.update()
            return
        pos, col = [], []
        for ln, mj in zip(cs.lines, cs.major):
            p = ln - self.origin
            pos.append(np.repeat(p, 2, axis=0)[1:-1])                 # segments
            zero = abs(float(ln[0, 2])) < 1e-9
            c = (0.30, 0.86, 1.0, 1.0) if zero else ((0.97, 0.97, 0.99, 0.95) if mj else (0.62, 0.66, 0.72, 0.75))
            col.append(np.tile(c, (2 * (len(p) - 1), 1)))
        line = scene.visuals.Line(pos=np.vstack(pos).astype(np.float32), color=np.vstack(col).astype(np.float32),
                                  connect="segments", method="gl", width=1.5, parent=self.view.scene)
        # contours lie exactly on the seabed: drawn as an overlay so points / surfaces never hide them
        line.set_gl_state("translucent", depth_test=False)
        line.order = 6
        line.transform = STTransform(scale=(1.0, 1.0, self.z_scale))
        self.contours[cid] = line
        # value labels: major contours (all contours if there are no majors)
        from fileio.contour_io import label_positions
        lo, hi = cs.bbox()
        spacing = max(float(max(hi[0] - lo[0], hi[1] - lo[1])) / 3.0, 1.0)
        import math
        dec = 0 if cs.interval >= 1 else min(3, max(1, -math.floor(math.log10(cs.interval) + 1e-9) +
                                                   (0 if round(cs.interval * 10 ** -math.floor(math.log10(cs.interval)), 6)
                                                    in (1.0, 2.0, 5.0) else 1)))
        use_major = bool(cs.major.any())
        texts, pts = [], []
        for ln, mj in zip(cs.lines, cs.major):
            if use_major and not mj:
                continue
            z = float(ln[0, 2])
            for x, y, _ang in label_positions(ln, spacing, spacing * 0.3):
                texts.append(f"{(-z if depth else z):.{dec}f}")
                pts.append((x - self.origin[0], y - self.origin[1], z - self.origin[2]))
                if len(texts) >= 400:
                    break
        if texts:
            t = scene.visuals.Text(text=texts, pos=np.asarray(pts, np.float32), color=(1.0, 0.86, 0.35, 1.0),
                                   font_size=8, anchor_x="center", anchor_y="center", parent=self.view.scene)
            t.set_gl_state("translucent", depth_test=False)
            t.order = 7
            t.transform = STTransform(scale=(1.0, 1.0, self.z_scale))
            self.contour_labels[cid] = t
        self.canvas.update()

    def set_contours_visible(self, cid, on):
        for d in (self.contours, self.contour_labels):
            if cid in d:
                d[cid].visible = bool(on)
        self.canvas.update()

    # ---------- region polygon (volumes) ----------
    def set_region(self, poly):
        if not hasattr(self, "region_lines"):
            self.region_lines = scene.visuals.Line(parent=self.view.scene, method="gl", width=2.0)
            self.region_lines.set_gl_state("translucent", depth_test=False)
            self.region_lines.order = 5
        if poly is None or self.origin is None or len(poly) < 3:
            self.region_lines.visible = False
            self.canvas.update()
            return
        P = np.asarray(poly, float)
        P = np.vstack([P, P[:1]])
        pairs = np.repeat(P, 2, axis=0)[1:-1]
        self.region_lines.set_data(pos=self._drape_pairs(pairs).astype(np.float32), color=(1.0, 0.82, 0.40, 1.0),
                                   connect="segments")
        self.region_lines.transform = STTransform(scale=(1.0, 1.0, self.z_scale))
        self.region_lines.visible = True
        self.canvas.update()

    # ---------- routes ----------
    def _z_pairs(self, xy, z):
        """Polyline (world XY + world Z per vertex) → local segment pairs; NaN Z → draped."""
        o = self.origin
        loc = np.column_stack([xy[:, 0] - o[0], xy[:, 1] - o[1], np.asarray(z, float) - o[2]])
        miss = ~np.isfinite(loc[:, 2])
        if miss.any():
            loc[miss, 2] = self._drape_z(loc[miss, 0], loc[miss, 1])
        return np.repeat(loc, 2, axis=0)[1:-1]

    def _z_pairs_many(self, items):
        """[(xy (N, 2) world, z (N,) world or NaN)] → one array of local segment pairs. All vertices that
        need draping are looked up in a single call (one call per feature is far too slow for 10⁴ features)."""
        if not items:
            return np.empty((0, 3))
        o = self.origin
        xy = np.vstack([np.asarray(a, float)[:, :2] for a, _z in items])
        z = np.concatenate([np.asarray(zz, float) for _a, zz in items])
        loc = np.column_stack([xy[:, 0] - o[0], xy[:, 1] - o[1], z - o[2]])
        miss = ~np.isfinite(loc[:, 2])
        if miss.any():
            loc[miss, 2] = self._drape_z(loc[miss, 0], loc[miss, 1])
        n = np.array([len(a) for a, _z in items])
        start = np.r_[0, np.cumsum(n)[:-1]]
        # segment pairs inside each polyline: (k, k+1) for k in [start, start + n − 2]
        first = np.concatenate([np.arange(s0, s0 + m - 1) for s0, m in zip(start, n) if m >= 2])
        out = np.empty((2 * len(first), 3))
        out[0::2] = loc[first]
        out[1::2] = loc[first + 1]
        return out

    def set_routes(self, routes):
        """routes: list of Route objects (visible ones). Cyan polylines draped on the terrain + KP labels at
        round values; cable profiles (routes with cable-top Z) are drawn orange at their own level."""
        from core.route import dist_label
        from render.colormap import nice_ticks
        if self.origin is None or not routes:
            self.route_lines.visible = self.route_text.visible = False
            self.canvas.update()
            return
        off = self.origin[:2]
        pos, cols, labels, lpos, lz = [], [], [], [], []
        drape = []
        for r in routes:
            if r.z is not None:
                q = self._z_pairs(r.xy, r.z)
                pos.append(q)
                cols.append(np.tile((1.0, 0.62, 0.26, 1.0), (len(q), 1)))
            else:
                for a, b in zip(r.xy[:-1], r.xy[1:]):
                    drape.extend([a, b])
            k0, k1 = r.kp_range()
            ticks, dec = nice_ticks(k0, k1, target=8)
            for k in ticks:
                c = r.chainage_at_kp(k)
                lpos.append(r.xy_at(c))
                lz.append(np.interp(c, r.cum, r.z) if r.z is not None else np.nan)
                labels.append(f"{dist_label()} {k:.{max(dec, 1)}f}")
            labels.append(r.name)
            lpos.append(r.xy[0])
            lz.append(r.z[0] if r.z is not None else np.nan)
        if drape:
            q = self._drape_pairs(drape)
            pos.append(q)
            cols.append(np.tile((0.30, 0.79, 0.94, 1.0), (len(q), 1)))
        tr = STTransform(scale=(1.0, 1.0, self.z_scale))
        self.route_lines.set_data(pos=np.vstack(pos).astype(np.float32), color=np.vstack(cols).astype(np.float32),
                                  connect="segments")
        self.route_lines.transform = tr
        self.route_lines.visible = True
        L = np.asarray(lpos, float)
        lx, ly = L[:, 0] - off[0], L[:, 1] - off[1]
        z = np.asarray(lz, float) - self.origin[2]
        z = np.where(np.isfinite(z), z, self._drape_z(lx, ly))
        self.route_text.text = labels
        self.route_text.pos = np.column_stack([lx, ly, z]).astype(np.float32)
        self.route_text.transform = tr
        self.route_text.visible = True
        self.canvas.update()

    def set_structures(self, groups):
        """groups: list of Structures (visible ones) → orange circles (foundation diameter, at least 3 m drawn)
        on the terrain, with names."""
        if self.origin is None or not groups or not any(len(g) for g in groups):
            self.str_lines.visible = self.str_text.visible = False
            self.canvas.update()
            return
        t = np.linspace(0, 2 * np.pi, 33)
        pos, labels, lp = [], [], []
        o = self.origin
        for g in groups:
            for name, (x, y), d in zip(g.names, g.xy, g.diameter):
                r = max(d / 2.0, 1.5)
                lx, ly = x - o[0] + r * np.cos(t), y - o[1] + r * np.sin(t)
                z = self._drape_z(lx, ly)
                q = np.column_stack([lx, ly, z])
                pos.append(np.repeat(q, 2, axis=0)[1:-1])
                labels.append(name)
                lp.append((x - o[0] + r, y - o[1], float(np.nanmax(z))))
        tr = STTransform(scale=(1.0, 1.0, self.z_scale))
        self.str_lines.set_data(pos=np.vstack(pos).astype(np.float32), color=(1.0, 0.62, 0.26, 1.0),
                                connect="segments")
        self.str_lines.transform = tr
        self.str_lines.visible = True
        self.str_text.text = labels
        self.str_text.pos = np.asarray(lp, np.float32)
        self.str_text.transform = tr
        self.str_text.visible = True
        self.canvas.update()

    def set_vectors(self, layers, color=(0.78, 0.49, 1.0, 0.95)):
        """layers: VectorLayer objects → lines and polygon outlines (3D lines at their Z, others draped),
        points as small crosses."""
        if self.origin is None or not layers or not any(len(l) for l in layers):
            self.vec_lines.visible = False
            self.canvas.update()
            return
        items = []
        span = float(np.max(self.bbox[1][:2] - self.bbox[0][:2])) if self.bbox is not None else 100.0
        arm = max(span / 400.0, 0.5)
        for lay in layers:
            for f in lay.features:
                for p in f["parts"]:
                    if f["type"] == "Point":
                        x, y = p[0, 0], p[0, 1]
                        z = p[0, 2] if p.shape[1] >= 3 and abs(p[0, 2]) > 1e-9 else np.nan
                        items.append((np.array([(x - arm, y), (x + arm, y)]), np.full(2, z)))
                        items.append((np.array([(x, y - arm), (x, y + arm)]), np.full(2, z)))
                        continue
                    q = p if f["type"] != "Polygon" or np.array_equal(p[0, :2], p[-1, :2]) else np.vstack([p, p[:1]])
                    if len(q) < 2:
                        continue
                    z = q[:, 2] if q.shape[1] >= 3 and np.any(np.abs(q[:, 2]) > 1e-9) else np.full(len(q), np.nan)
                    items.append((q[:, :2], z))
        if not items:
            self.vec_lines.visible = False
            self.canvas.update()
            return
        self.vec_lines.set_data(pos=self._z_pairs_many(items).astype(np.float32), color=color, connect="segments")
        self.vec_lines.transform = STTransform(scale=(1.0, 1.0, self.z_scale))
        self.vec_lines.visible = True
        self.canvas.update()

    def set_analysis_overlay(self, lines=(), labels=()):
        """lines: [{xy (N,2) world, z (N,) world or None (draped), color rgba}];
        labels: [(x, y, z or None, text)]. Empty → hidden."""
        if self.origin is None or not lines:
            self.ana_lines.visible = self.ana_text.visible = False
            self.canvas.update()
            return
        pos, cols, items = [], [], []
        for ln in lines:
            xy = np.asarray(ln["xy"], float)
            if len(xy) < 2:
                continue
            z = ln.get("z")
            items.append((xy, np.full(len(xy), np.nan) if z is None else np.asarray(z, float)))
            cols.append(np.tile(ln["color"], (2 * (len(xy) - 1), 1)))
        if items:
            pos.append(self._z_pairs_many(items))
        tr = STTransform(scale=(1.0, 1.0, self.z_scale))
        if pos:
            self.ana_lines.set_data(pos=np.vstack(pos).astype(np.float32), color=np.vstack(cols).astype(np.float32),
                                    connect="segments")
            self.ana_lines.transform = tr
            self.ana_lines.visible = True
        if labels:
            o = self.origin
            P = np.array([[x - o[0], y - o[1], np.nan if z is None else z - o[2]] for x, y, z, _t in labels], float)
            miss = ~np.isfinite(P[:, 2])
            if miss.any():
                P[miss, 2] = self._drape_z(P[miss, 0], P[miss, 1])
            self.ana_text.text = [t for *_r, t in labels]
            self.ana_text.pos = P.astype(np.float32)
            self.ana_text.transform = tr
            self.ana_text.visible = True
        else:
            self.ana_text.visible = False
        self.canvas.update()

    # ---------- subset boxes ----------
    def set_subset_overlay(self, current=None, draft=None, done=()):
        """current / draft: 4×2 corner arrays (world XY); done: list of corner arrays (shaded green)."""
        if self.origin is None:
            return
        off = self.origin[:2]
        z = self.overlay_z
        pos, col = [], []

        def outline(c, color):
            for k in range(4):
                pos.extend([c[k], c[(k + 1) % 4]])
                col.extend([color, color])

        for c in done:
            outline(c, (0.24, 0.81, 0.56, 0.9))
        if current is not None:
            outline(current, (1.0, 0.62, 0.26, 1.0))
        if draft is not None:
            outline(draft, (0.30, 0.55, 1.0, 1.0))
        tr = STTransform(scale=(1.0, 1.0, self.z_scale))
        if pos:
            P3, C3 = [], []
            for k in range(0, len(pos), 2):
                seg = self._drape_pairs([pos[k], pos[k + 1]])
                P3.append(seg)
                C3.append(np.tile(col[k], (len(seg), 1)))
            self.sub_lines.set_data(pos=np.vstack(P3).astype(np.float32), color=np.vstack(C3).astype(np.float32),
                                    connect="segments")
            self.sub_lines.transform = tr
            self.sub_lines.visible = True
        else:
            self.sub_lines.visible = False
        if done:
            V = np.vstack([np.column_stack([c[:, 0] - off[0], c[:, 1] - off[1],
                                            self._drape_z(c[:, 0] - off[0], c[:, 1] - off[1])]) for c in done])
            F = np.vstack([[4 * k, 4 * k + 1, 4 * k + 2] for k in range(len(done))]
                          + [[4 * k, 4 * k + 2, 4 * k + 3] for k in range(len(done))])
            self.sub_fill.set_data(vertices=V.astype(np.float32), faces=F.astype(np.int32),
                                   color=(0.24, 0.81, 0.56, 0.16))
            self.sub_fill.transform = tr
            self.sub_fill.visible = True
        else:
            self.sub_fill.visible = False
        self.canvas.update()

    # ---------- camera ----------
    def fit_view(self):
        if self.origin is None:
            return
        if self.bbox is not None:
            lo, hi = self.bbox[0] - self.origin, self.bbox[1] - self.origin
        elif self.pc is not None:
            lo, hi = self.pc.bbox_min - self.origin, self.pc.bbox_max - self.origin
        else:
            return
        center = (lo + hi) / 2.0
        ext = hi - lo
        center[2] *= self.z_scale
        ext[2] *= self.z_scale
        self.camera.center = tuple(float(v) for v in center)
        self.camera.scale_factor = float(max(np.linalg.norm(ext), 1e-3))
        self.canvas.update()

    def look_at(self, x, y, z=None, extent=50.0):
        """Centre the camera on a world position (z None → terrain height there)."""
        if self.origin is None:
            return
        lx, ly = x - self.origin[0], y - self.origin[1]
        lz = (z - self.origin[2]) if z is not None and np.isfinite(z) else float(self._drape_z(np.array([lx]),
                                                                                              np.array([ly]))[0])
        self.camera.center = (float(lx), float(ly), float(lz * self.z_scale))
        self.camera.scale_factor = float(max(extent, 1.0))
        self.canvas.update()

    def set_view(self, name):
        elev, azim = VIEW_PRESETS[name]
        self.camera.elevation = elev
        self.camera.azimuth = azim
        self.fit_view()
