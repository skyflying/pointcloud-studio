"""Reconstruct menu: photogrammetry (photos / video, stereo rig), meshes and geometric models."""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox


class ReconstructMixin:
    def _init_reconstruct_state(self):
        self._recon_result = None

    # ================================================================ helpers
    def _recon_source(self, title):
        src = self._pick_source_points(title)
        if src is None:
            return None, None, None
        pc = src.obj
        counts = self._counts_of(src)
        return src, pc, counts

    def _recon_indices(self, src, dlg):
        pc = src.obj
        if dlg.source() == "selected" and src.id == self._bound_id:
            idx = np.flatnonzero(src.sel.mask & pc.active_mask())
        else:
            idx = pc.active_indices()
        cls = dlg.classes()
        if cls:
            idx = idx[np.isin(pc.attrs["Classification"][idx], cls)]
        return idx, (f" (class {', '.join(map(str, cls))})" if cls else "")

    def _add_mesh_dataset(self, mesh, name, src=None):
        from core.project import Dataset
        ds = Dataset(self.project.unique_name(name), "mesh", mesh, crs=self.project.crs,
                     source_crs=self.project.crs, derived_from=src.id if src is not None else None,
                     edit_version=src.edit_version if src is not None else 0)
        self.project.add(ds)
        self._project_changed(fit=len(self.project.datasets) == 1)
        self._set_dirty(True)
        return ds

    def _add_vector(self, feats, name, src_epsg=None):
        from core import crs as crsmod
        from core.project import Dataset
        from core.vector_layer import VectorLayer
        if not feats:
            return None
        proj = self.project
        if src_epsg and proj.crs is not None:
            try:
                src = crsmod.from_epsg(src_epsg)
                if crsmod.same(src, proj.crs) is False:
                    from fileio import vector_io
                    feats = vector_io.reproject(feats, src, proj.crs)
            except Exception:  # noqa: BLE001
                pass
        ds = Dataset(proj.unique_name(name), "vector", VectorLayer(feats, meta={"info": name}), None,
                     crs=proj.crs, source_crs=proj.crs)
        proj.add(ds)
        self._project_changed()
        self._set_dirty(True)
        return ds

    # ================================================================ photogrammetry
    def open_photo_dialog(self):
        from ui.reconstruct_dialogs import JobDialog, PhotoDialog
        try:
            import cv2  # noqa: F401
        except Exception:  # noqa: BLE001
            from ui.deps_dialog import missing_module_box
            missing_module_box("cv2", self)
            return
        dlg = PhotoDialog(self.project.crs if self.project.datasets else None, self)
        if dlg.exec() != QDialog.Accepted:
            return
        p, video, load = dlg.settings()

        def job(log, progress, cancel):
            from core.sfm.images import extract_frames
            from core.sfm.pipeline import run_photogrammetry
            if video is not None:
                log(f"Extracting frames from {os.path.basename(video['path'])}…")
                p.images = extract_frames(video["path"], os.path.join(p.out_dir, "frames"), video["every_s"],
                                          video["max_frames"], video["start_s"], video["end_s"], True,
                                          progress=lambda f: progress(0.05 * f), cancel=cancel)
                log(f"  {len(p.images)} frames")
            return run_photogrammetry(p, log, (lambda f: progress(0.05 + 0.95 * f)) if video else progress, cancel)

        JobDialog("Photogrammetry", job, lambda r: self._load_recon(r, load), self).exec()

    def open_rig_dialog(self):
        from ui.reconstruct_dialogs import JobDialog, RigDialog, prepare_rig_input
        try:
            import cv2  # noqa: F401
        except Exception:  # noqa: BLE001
            from ui.deps_dialog import missing_module_box
            missing_module_box("cv2", self)
            return
        dlg = RigDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        p, srcin = dlg.settings()

        def job(log, progress, cancel):
            from core.sfm.pipeline import run_stereo_rig
            prepare_rig_input(p, srcin, log, cancel=cancel)
            res = run_stereo_rig(p, log, progress, cancel)
            res.pop("xyz", None)
            res.pop("rgb", None)
            return res

        JobDialog("Stereo rig", job, lambda r: self._load_recon(r, {"sparse": False, "cameras": True}, rig=True),
                  self).exec()

    def _load_recon(self, res, load, rig=False):
        paths = [res["dense_path"]] if res.get("dense_path") else []
        if load.get("sparse") and res.get("sparse_path"):
            paths.append(res["sparse_path"])
        if not paths and res.get("sparse_path"):
            paths = [res["sparse_path"]]
        if not paths:
            return
        cams = res.get("cameras")
        if load.get("cameras") and cams is not None and len(cams):
            names = res.get("camera_names") or [f"pair {i + 1}" for i in range(len(cams))]
            epsg = res.get("epsg")

            want = os.path.normcase(os.path.abspath(paths[0]))

            def hook(ds, cams=np.asarray(cams, float), names=names, epsg=epsg):
                if not ds.path or os.path.normcase(os.path.abspath(ds.path)) != want:
                    return                       # the load of the result was cancelled / failed: not ours
                feats = [{"type": "Point", "parts": [c[None, :]], "attrs": {"name": n}} for c, n in zip(cams, names)]
                self._add_vector(feats, "Cameras · " + ("stereo rig" if rig else os.path.basename(paths[0])),
                                 epsg)
            self._after_ingest = hook
        empty = not self.project.datasets
        self._load_queue = paths[1:]
        self.load_path(paths[0], add=not empty)

    # ================================================================ mesh
    def open_mesh_dialog(self):
        from core.mesh import build_mesh, colors_of, mean_spacing
        from ui.model_dialogs import MeshDialog
        from ui.reconstruct_dialogs import JobDialog
        src, pc, counts = self._recon_source("Mesh from point cloud")
        if src is None:
            return
        idx0 = pc.active_indices()
        rgb = colors_of(pc)
        dlg = MeshDialog(counts, mean_spacing(pc.xyz, idx0), rgb is not None, self)
        if dlg.exec() != QDialog.Accepted:
            return
        prm = dlg.params()
        idx, suffix = self._recon_indices(src, dlg)
        if len(idx) < 4:
            QMessageBox.warning(self, "Mesh", "Not enough points in the source.")
            return
        use_rgb = prm.pop("use_rgb")
        method = prm.pop("method")

        def job(log, progress, cancel):
            log(f"{method}: {len(idx):,} points…")
            m = build_mesh(pc.xyz, idx, method, rgb=rgb if use_rgb else None, progress=progress, **prm)
            log(m.size_text())
            return m

        def done(m):
            from core.mesh import METHODS
            self._add_mesh_dataset(m, f"Mesh · {METHODS[method].split(' (')[0]} · {src.name}{suffix}", src)
            self.statusBar().showMessage(f"Mesh: {m.size_text()}", 10000)

        JobDialog("Mesh from point cloud", job, done, self).exec()

    # ================================================================ models
    def open_model_dialog(self, kind="planes"):
        from core import modeling as md
        from core.mesh import mean_spacing
        from ui.model_dialogs import MODEL_KINDS, ModelDialog
        from ui.reconstruct_dialogs import JobDialog
        src, pc, counts = self._recon_source("Model point cloud")
        if src is None:
            return
        dlg = ModelDialog(counts, mean_spacing(pc.xyz, pc.active_indices()), self, kind)
        if dlg.exec() != QDialog.Accepted:
            return
        kind, prm = dlg.params()
        idx, suffix = self._recon_indices(src, dlg)
        if len(idx) < 10:
            QMessageBox.warning(self, "Model", "Not enough points in the source.")
            return
        if kind == "buildings" and not pc.has("Classification"):
            QMessageBox.warning(self, "Model", "Buildings need a classified cloud (Edit → Classify Points).")
            return

        def job(log, progress, cancel):
            log(f"{MODEL_KINDS[kind].split(' (')[0]}: {len(idx):,} points…")
            if kind == "planes":
                r = md.detect_planes(pc.xyz, idx, progress=progress, cancel=cancel, **prm)
                rows = r["segments"]
            elif kind == "cylinders":
                r = md.detect_cylinders(pc.xyz, idx, progress=progress, cancel=cancel, **prm)
                rows = r["cylinders"]
            else:
                r = md.building_models(pc.xyz, pc.attrs["Classification"], idx, progress=progress, cancel=cancel,
                                       **prm)
                rows = r["buildings"]
            log(f"{len(rows)} model(s) found")
            for row in rows[:200]:
                log("  " + " · ".join(f"{k} {v:,.3f}" if isinstance(v, float) else f"{k} {v}"
                                      for k, v in row.items() if not isinstance(v, (list, tuple))))
            return r, rows

        def done(out):
            r, rows = out
            if r.get("mesh") is None or not rows:
                QMessageBox.information(self, "Model", "No models were found — try a larger tolerance, fewer "
                                        "minimum points or select the object first.")
                return
            m = r["mesh"]
            m.meta.update(kind=kind, table=rows, **{f"param_{k}": v for k, v in prm.items()
                                                   if isinstance(v, (int, float, str))})
            label = {"planes": "Planes", "cylinders": "Cylinders", "buildings": f"Buildings LOD{prm.get('lod', 2)}"}
            self._add_mesh_dataset(m, f"{label[kind]} · {src.name}{suffix}", src)
            if kind == "buildings" and r.get("footprints"):
                feats = [{"type": "Polygon", "parts": [np.vstack([f_["ring"], f_["ring"][:1]])],
                          "attrs": f_["attrs"]} for f_ in r["footprints"]]
                self._add_vector(feats, f"Building footprints · {src.name}")
            self.statusBar().showMessage(f"{len(rows)} {label[kind].lower()} modelled", 10000)

        JobDialog("Model point cloud", job, done, self).exec()

    # ================================================================ mesh datasets
    def export_mesh(self, ds=None):
        from core.mesh import EXPORT_FORMATS, export_mesh
        if ds is None:
            meshes = self.project.of_kind("mesh")
            if not meshes:
                QMessageBox.information(self, "Export mesh", "The project has no mesh.")
                return
            ds = meshes[-1]
        base = "".join(c if c.isalnum() or c in "-_ " else "_" for c in ds.name).strip()
        p, flt = QFileDialog.getSaveFileName(self, f"Export {ds.name}", base + ".obj", ";;".join(EXPORT_FORMATS.values()))
        if not p:
            return
        ext = os.path.splitext(p)[1].lower()
        if ext not in EXPORT_FORMATS:
            ext = next((e for e, f in EXPORT_FORMATS.items() if f == flt), ".obj")
            p += ext
        local = None
        if ext in (".obj", ".ply", ".stl"):
            lo = ds.obj.vertices.min(axis=0)
            if np.abs(lo[:2]).max() > 1e5:
                r = QMessageBox.question(self, "Export mesh",
                                         "The coordinates are large (projected system). Write them relative to a "
                                         "local origin? (Recommended for STL and for 3D / CAD viewers; the offset is "
                                         "written in the file header.)\n\nNo = full coordinates.",
                                         QMessageBox.Yes | QMessageBox.No)
                local = r == QMessageBox.Yes
        try:
            o = export_mesh(ds.obj, p, local)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", f"{type(e).__name__}: {e}")
            return
        msg = f"Exported {os.path.basename(p)}"
        if o is not None and np.any(o):
            msg += f" (offset {o[0]:.0f}, {o[1]:.0f}, {o[2]:.0f})"
        self.statusBar().showMessage(msg, 10000)

    def mesh_info(self, ds_id):
        from ui.model_dialogs import MeshInfoDialog
        ds = self.project.get(ds_id)
        if ds is None:
            return

        def zoom(row):
            c = row.get("centre") or row.get("centroid") or row.get("base")
            if c is None:
                return
            c = np.asarray(c, float)
            if len(c) == 2:
                c = np.r_[c, ds.obj.vertices[:, 2].mean()]
            size = max(row.get("length", 0) or 0, np.sqrt(row.get("area_m2", 0) or row.get("footprint_m2", 0) or 0),
                       5.0)
            keep = self.viewer.bbox
            self.viewer.bbox = (c - size, c + size)
            self.viewer.fit_view()
            self.viewer.bbox = keep

        MeshInfoDialog(ds.name, ds.obj, lambda: self.export_mesh(ds), zoom, self).exec()
