"""Project files (.pcsproj): JSON description + <name>.pcsproj_data/ with edits and generated surfaces.

Original data files are never modified or copied; they are re-read on open and the saved edits
(delete / trash batches) are re-applied. Generated TIN / DEM datasets are stored in the data folder.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import numpy as np

from core import crs as crsmod
from core.surface import Dem, Tin
from core.trash import Batch

FORMAT = "PointCloud Studio project"
FORMAT_VERSION = 1


def data_dir(path):
    return path + "_data"


def _file_sig(path):
    try:
        st = os.stat(path)
        return {"size": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return {}


# ---------------------------------------------------------------- save
def save_project(path, project, sections=(), view=None, subsets=None):
    ddir = data_dir(path)
    os.makedirs(ddir, exist_ok=True)
    base = os.path.dirname(os.path.abspath(path))
    keep = set()
    out = []
    for d in project.datasets:
        entry = {"id": d.id, "name": d.name, "kind": d.kind, "visible": d.visible, "opacity": d.opacity,
                 "z_flipped": bool(getattr(d, "z_flipped", False)),
                 "crs": crsmod.to_text(d.crs), "source_crs": crsmod.to_text(d.source_crs),
                 "import_opts": d.import_opts, "derived_from": d.derived_from, "edit_version": d.edit_version}
        if d.path:
            entry["path"] = os.path.abspath(d.path)
            try:
                entry["rel_path"] = os.path.relpath(os.path.abspath(d.path), base)
            except ValueError:            # different drive on Windows
                entry["rel_path"] = None
            entry["file"] = _file_sig(d.path)
        if d.kind == "route":                               # small: stored inline
            r = d.obj
            entry["route"] = {"xy": r.xy.tolist(), "kp": None if r.kp is None else r.kp.tolist(),
                              "start_kp": r.start_kp, "name": r.name}
            out.append(entry)
            continue
        if d.kind == "contour":
            cs = d.obj
            fname = f"ds{d.id}.npz"
            lens = np.array([len(l) for l in cs.lines], dtype=np.int64)
            np.savez_compressed(os.path.join(ddir, fname), xyz=np.vstack(cs.lines), lens=lens,
                                levels=cs.levels, major=cs.major)
            entry["contour"] = {"interval": cs.interval, "base": cs.base, "index_every": cs.index_every,
                                "meta": {k: float(v) for k, v in cs.meta.items() if v is not None}}
            entry["data"] = fname
            keep.add(fname)
            out.append(entry)
            continue
        fname = f"ds{d.id}.npz"
        fpath = os.path.join(ddir, fname)
        if d.kind == "points":
            pc = d.obj
            batches = [{"id": b.id, "count": b.count, "note": b.note, "created": b.created.isoformat()}
                       for b in d.trash.sorted_batches()]
            entry["points"] = len(pc)
            entry["trash"] = {"batches": batches, "next_id": d.trash._next_id}
            arrays = {"deleted_batch": pc.deleted_batch}
            if pc.orig_class is not None:                   # class 7 rejects etc.
                arrays.update(classification=pc.attrs["Classification"], orig_class=pc.orig_class)
            entry["hide_noise"] = bool(pc.hide_noise)
            entry["reject_classes"] = sorted(int(c) for c in pc.reject_classes)
            np.savez_compressed(fpath, **arrays)
            entry["data"] = fname
        elif d.derived_from is not None or not d.path:      # generated surface → store it
            o = d.obj
            if isinstance(o, Dem):
                arrays = {"z": o.z}
                if o.has_true_position:
                    arrays.update(tx=o.tx, ty=o.ty)
                if o.cvals is not None:
                    arrays.update(cvals=o.cvals)
                np.savez_compressed(fpath, **arrays)
                entry["grid"] = {"x0": o.x0, "ytop": o.ytop, "cell": o.cell, "method": o.method,
                                 "crange": [float(v) for v in o.crange] if o.crange else None,
                                 "cpalette": o.cpalette,
                                 "meta": {k: v for k, v in o.meta.items() if isinstance(v, (int, float, str, bool))}}
            elif isinstance(o, Tin):
                np.savez_compressed(fpath, vertices=o.vertices, triangles=o.triangles)
                entry["tin"] = {"max_edge": o.max_edge, "source_count": o.source_count, "thin_cell": o.thin_cell}
            entry["data"] = fname
        if "data" in entry:
            keep.add(entry["data"])
        out.append(entry)
    for f in os.listdir(ddir):                           # remove stale files of deleted datasets
        if f.endswith(".npz") and f not in keep:
            try:
                os.remove(os.path.join(ddir, f))
            except OSError:
                pass
    doc = {"format": FORMAT, "format_version": FORMAT_VERSION, "saved": datetime.now().isoformat(timespec="seconds"),
           "crs": crsmod.to_text(project.crs),
           "origin": None if project.origin is None else [float(v) for v in project.origin],
           "edit_id": project.edit_id, "datasets": out,
           "sections": [{"name": s.name, "xy": s.xy.tolist(), "width": s.width, "start": s.start,
                         "kp": None if s.kp is None else s.kp.tolist(), "visible": s.visible} for s in sections],
           "view": view or {}, "subsets": subsets or {}}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)                               # atomic: never leave a half-written project


# ---------------------------------------------------------------- open
def read_project(path):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if doc.get("format") != FORMAT:
        raise ValueError("Not a PointCloud Studio project file")
    if doc.get("format_version", 1) > FORMAT_VERSION:
        raise ValueError("This project was saved by a newer version of PointCloud Studio")
    return doc


def resolve_source(entry, project_path):
    """Existing path of a dataset's original file (absolute, then relative to the project), or None."""
    cands = [entry.get("path")]
    if entry.get("rel_path"):
        cands.append(os.path.join(os.path.dirname(os.path.abspath(project_path)), entry["rel_path"]))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def load_generated(entry, project_path):
    f = os.path.join(data_dir(project_path), entry["data"])
    with np.load(f) as z:
        if "contour" in entry:
            from core.contours import ContourSet
            c = entry["contour"]
            lines = np.split(z["xyz"], np.cumsum(z["lens"])[:-1])
            return ContourSet(list(lines), z["levels"], z["major"].astype(bool), c["interval"], c["base"],
                              c["index_every"], meta=c.get("meta", {}))
        if "grid" in entry:
            g = entry["grid"]
            return Dem(z["z"], g["x0"], g["ytop"], g["cell"], g["method"], meta=dict(g.get("meta", {})),
                       tx=z["tx"] if "tx" in z else None, ty=z["ty"] if "ty" in z else None,
                       cvals=z["cvals"] if "cvals" in z else None,
                       crange=tuple(g["crange"]) if g.get("crange") else None,
                       cpalette=g.get("cpalette", "Diverging"))
        t = entry["tin"]
        return Tin(z["vertices"], z["triangles"], t["max_edge"], t["source_count"], t.get("thin_cell", 0.0))


def apply_edits(ds, entry, project_path):
    """Restore deleted_batch + trash batches. Returns a warning string or ''."""
    if "data" not in entry:
        return ""
    f = os.path.join(data_dir(project_path), entry["data"])
    if not os.path.isfile(f):
        return f"{ds.name}: saved edits not found"
    with np.load(f) as z:
        db = z["deleted_batch"]
        cls = z["classification"] if "classification" in z else None
        orig = z["orig_class"] if "orig_class" in z else None
    if len(db) != len(ds.obj):
        return (f"{ds.name}: the file now has {len(ds.obj):,} points (saved: {len(db):,}) — "
                "its saved edits were NOT applied")
    ds.obj.deleted_batch[:] = db
    if cls is not None and ds.obj.can_classify and len(cls) == len(ds.obj):
        changed = np.flatnonzero(cls != ds.obj.attrs["Classification"])
        if len(changed):
            ds.obj.orig_class = orig.copy()
            ds.obj.set_class(changed, cls[changed])
            ds.obj.orig_class = orig.copy()
    ds.obj.hide_noise = bool(entry.get("hide_noise", True))
    ds.obj.reject_classes = set(entry.get("reject_classes", [7, 18]))
    t = entry.get("trash", {})
    ds.trash.batches = {}
    for b in t.get("batches", []):
        ds.trash.batches[b["id"]] = Batch(b["id"], b["count"], b["note"], datetime.fromisoformat(b["created"]))
    ds.trash._next_id = t.get("next_id", max(ds.trash.batches, default=0) + 1)
    sig = entry.get("file", {})
    if sig and ds.path and _file_sig(ds.path).get("mtime") != sig.get("mtime"):
        return f"{ds.name}: the file was modified after the project was saved (same point count — edits applied)"
    return ""
