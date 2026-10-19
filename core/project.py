"""Project model: several datasets (point clouds, grids/DEMs, TINs) in one coordinate system.

All datasets share one display origin (project.origin) so they line up in the 3D view.
Editing (selection / delete / reject) works on ONE point-cloud dataset at a time — the edit target.
Rule: exactly one point cloud → it is the edit target automatically; several point clouds or none
→ editing is locked until the user picks an edit target.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import warnings

import numpy as np

from core.history import History
from core.pointcloud import PointCloud
from core.route import Route
from core.selection import Selection
from core.surface import Dem, Tin
from core.trash import Trash

KIND_LABEL = {"points": "PTS", "grid": "DEM", "tin": "TIN", "route": "RTE", "contour": "CNT", "structure": "STR",
              "vector": "VEC", "mesh": "MSH"}


@dataclass
class Dataset:
    name: str
    kind: str                       # "points" | "grid" | "tin" | "mesh" | "route" | …
    obj: object                     # PointCloud | Dem | Tin
    path: str | None = None
    crs: object = None              # pyproj CRS of the data as loaded in the project (= project CRS)
    source_crs: object = None       # CRS of the original file (exports of LAS keep this)
    visible: bool = True
    opacity: float = 1.0
    import_opts: dict = field(default_factory=dict)
    derived_from: int | None = None
    edit_version: int = 0           # points: bumped on every edit; tin/grid: version of the source when built
    z_flipped: bool = False         # Z sign flipped after loading (file stored depths, positive down)
    survey_date: str | None = None  # 'YYYY-MM-DD' — epochs for scour / sand-wave / multi-year analyses
    simulated: bool = False         # a modelled (predicted) seabed rather than a survey
    id: int = 0
    # editing state (point clouds only)
    sel: Selection | None = None
    trash: Trash | None = None
    history: History | None = None

    def __post_init__(self):
        if self.kind == "points" and self.sel is None:
            self.sel = Selection(len(self.obj))
            self.trash = Trash(self.obj)
            self.history = History()

    def bbox(self):
        o = self.obj
        if isinstance(o, PointCloud):
            act = o.deleted_batch == 0
            if act.all():
                return o.bbox_min.copy(), o.bbox_max.copy()
            p = o.xyz[act] if act.any() else o.xyz
            return p.min(axis=0), p.max(axis=0)
        if isinstance(o, Dem):
            nr, nc = o.z.shape
            z = o.z[np.isfinite(o.z)]
            zl, zh = (float(z.min()), float(z.max())) if len(z) else (0.0, 0.0)
            return (np.array([o.x0, o.ytop - nr * o.cell, zl]),
                    np.array([o.x0 + nc * o.cell, o.ytop, zh]))
        if isinstance(o, Tin):
            return o.vertices.min(axis=0), o.vertices.max(axis=0)
        if o.__class__.__name__ == "Mesh":
            return o.bbox()
        if o.__class__.__name__ == "ContourSet":
            return o.bbox()
        if o.__class__.__name__ == "VectorLayer":
            lo, hi = o.bounds()
            z0, z1 = o.z_range()
            return np.array([lo[0], lo[1], z0]), np.array([hi[0], hi[1], z1])
        if o.__class__.__name__ == "Structures":
            lo, hi = o.xy.min(axis=0) - o.diameter.max(), o.xy.max(axis=0) + o.diameter.max()
            return np.array([lo[0], lo[1], np.nan]), np.array([hi[0], hi[1], np.nan])
        if isinstance(o, Route):                     # no Z: NaN is ignored in the project bbox
            lo, hi = o.xy.min(axis=0), o.xy.max(axis=0)
            if o.z is not None:
                return (np.array([lo[0], lo[1], np.nanmin(o.z)]), np.array([hi[0], hi[1], np.nanmax(o.z)]))
            return np.array([lo[0], lo[1], np.nan]), np.array([hi[0], hi[1], np.nan])
        raise TypeError(type(o))

    @property
    def size_text(self):
        o = self.obj
        if isinstance(o, PointCloud):
            return f"{o.n_active:,} pts"
        if isinstance(o, Dem):
            nr, nc = o.z.shape
            return f"{o.cell:g} m · {nc:,}×{nr:,}"
        if isinstance(o, Tin):
            return f"{len(o.triangles):,} tri"
        if o.__class__.__name__ == "Mesh":
            return o.size_text()
        if o.__class__.__name__ == "ContourSet":
            return f"{len(o.lines):,} lines · {o.n_levels} levels · {o.interval:g} m"
        if o.__class__.__name__ == "VectorLayer":
            return o.size_text()
        if o.__class__.__name__ == "Structures":
            d = np.unique(np.round(o.diameter, 2))
            return f"{len(o)} structure(s) · Ø " + (f"{d[0]:g} m" if len(d) == 1 else f"{d.min():g}–{d.max():g} m")
        if isinstance(o, Route):
            k0, k1 = o.kp_range()
            return f"{o.length / 1000:,.3f} km · KP {k0:,.3f} – {k1:,.3f}" + (" · cable Z" if o.z is not None else "")
        return ""


class Project:
    def __init__(self):
        self.crs = None
        self.origin = None          # np.array(3) shared display origin
        self.datasets: list[Dataset] = []
        self.edit_id: int | None = None
        self._next = 1

    # ---------- datasets ----------
    def add(self, ds: Dataset):
        ds.id = self._next
        self._next += 1
        if self.origin is None:
            lo, hi = ds.bbox()
            self.origin = np.nan_to_num(np.round((lo + hi) / 2.0))
        if isinstance(ds.obj, PointCloud):
            ds.obj.offset = self.origin.copy()     # every cloud uses the project origin
        self.datasets.append(ds)
        return ds

    def move(self, ds_id, delta=0, to=None):
        """Change a dataset's place in the layer list (index 0 = top). delta −1 = up, +1 = down;
        to = 'top' | 'bottom' | an int gap index 0…n (insert before the layer now at that index —
        what a drag-and-drop gives). Returns True if the order changed."""
        i = next((k for k, d in enumerate(self.datasets) if d.id == ds_id), None)
        if i is None:
            return False
        n = len(self.datasets)
        if isinstance(to, int) and not isinstance(to, bool):
            g = max(0, min(n, to))
            j = g - 1 if g > i else g                     # the gap index shifts once the layer is taken out
        else:
            j = 0 if to == "top" else n - 1 if to == "bottom" else max(0, min(n - 1, i + int(delta)))
        if j == i:
            return False
        ds = self.datasets.pop(i)
        self.datasets.insert(j, ds)
        return True

    def remove(self, ds_id):
        self.datasets = [d for d in self.datasets if d.id != ds_id]
        if self.edit_id == ds_id:
            self.edit_id = None

    def get(self, ds_id):
        return next((d for d in self.datasets if d.id == ds_id), None)

    def of_kind(self, kind, visible_only=False):
        return [d for d in self.datasets if d.kind == kind and (d.visible or not visible_only)]

    @property
    def points(self):
        return self.of_kind("points")

    @property
    def edit_target(self):
        return self.get(self.edit_id) if self.edit_id is not None else None

    def apply_lock_rule(self):
        """Exactly one point cloud → editable; otherwise locked until chosen. Returns True if it changed."""
        before = self.edit_id
        pts = self.points
        if len(pts) == 1:
            self.edit_id = pts[0].id
        elif self.edit_id is not None and len(pts) >= 2:
            self.edit_id = None
        return before != self.edit_id

    def bbox(self, visible_only=False):
        boxes = [d.bbox() for d in self.datasets if d.visible or not visible_only]
        if not boxes:
            return None
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)     # Z of routes / vector layers only: NaN
            lo = np.nanmin([b[0] for b in boxes], axis=0)
            hi = np.nanmax([b[1] for b in boxes], axis=0)
        return np.nan_to_num(lo), np.nan_to_num(hi)

    def unique_name(self, name):
        names = {d.name for d in self.datasets}
        if name not in names:
            return name
        i = 2
        while f"{name} ({i})" in names:
            i += 1
        return f"{name} ({i})"
