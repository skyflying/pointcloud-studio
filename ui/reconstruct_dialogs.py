"""Reconstruct menu dialogs: background job runner, photos / video → point cloud, GCP marking, stereo rig."""
from __future__ import annotations

import os
import threading
import traceback

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGraphicsEllipseItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from ui.theme import dark_titlebar


def _filter(label, exts):
    """File-dialog filter with lower- and upper-case patterns (cameras write .JPG / .MOV; matching is
    case-sensitive on Linux and with some macOS dialogs)."""
    pats = []
    for e in exts:
        pats += [f"*.{e}", f"*.{e.upper()}"]
    return f"{label} ({' '.join(pats)})"


IMG_FILTER = _filter("Images", ("jpg", "jpeg", "png", "tif", "tiff", "bmp", "webp"))
VIDEO_FILTER = _filter("Video", ("mp4", "mov", "avi", "mkv", "m4v", "mts", "wmv"))


def _muted(t):
    l = QLabel(t)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


def _title(t):
    l = QLabel(t)
    l.setObjectName("title")
    return l


def _dsp(v, lo, hi, step, dec=2, suffix=""):
    s = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=dec, singleStep=step)
    s.setValue(v)
    if suffix:
        s.setSuffix(suffix)
    return s


def _isp(v, lo, hi, suffix=""):
    s = QSpinBox(minimum=lo, maximum=hi)
    s.setValue(v)
    if suffix:
        s.setSuffix(suffix)
    return s


class _Cancelled(Exception):
    pass


# ================================================================ job runner
class _JobWorker(QObject):
    log = Signal(str)
    progress = Signal(float)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, stop):
        super().__init__()
        self.fn = fn
        self.stop = stop

    @Slot()
    def run(self):
        try:
            res = self.fn(self.log.emit, self.progress.emit, self.stop.is_set)
            self.finished.emit(res)
        except Exception as e:  # noqa: BLE001
            if self.stop.is_set() or type(e).__name__ == "Cancelled":
                self.failed.emit("Cancelled")
            else:
                self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")


class JobDialog(QDialog):
    """Runs fn(log, progress, cancel) in a thread with a live log, a progress bar and Cancel.
    on_done(result) runs on the GUI thread; the dialog stays open with the log until closed."""

    def __init__(self, title, fn, on_done=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 460)
        self.on_done = on_done
        self.result = None
        self.error = None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.addWidget(_title(title))
        self.bar = QProgressBar(minimum=0, maximum=1000)
        v.addWidget(self.bar)
        self.txt = QPlainTextEdit(readOnly=True)
        self.txt.setLineWrapMode(QPlainTextEdit.NoWrap)
        v.addWidget(self.txt, 1)
        row = QHBoxLayout()
        self.btn_save = QPushButton("Save Log…")
        self.btn_save.clicked.connect(self._save_log)
        row.addWidget(self.btn_save)
        row.addStretch(1)
        self.btn = QPushButton("Cancel")
        self.btn.clicked.connect(self._on_button)
        row.addWidget(self.btn)
        v.addLayout(row)
        self.stop = threading.Event()
        self.thread = QThread(self)
        self.worker = _JobWorker(fn, self.stop)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(lambda f: self.bar.setValue(int(max(0.0, min(1.0, f)) * 1000)))
        self.worker.finished.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        # results are handed over only once the thread has really stopped (no work left in it)
        self.thread.finished.connect(self._thread_done)
        self.running = True
        self._handed = False
        self.thread.start()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _log(self, s):
        self.txt.appendPlainText(s)

    def _done(self, res):
        self.result = res
        self.bar.setValue(1000)

    def _thread_done(self):
        self.running = False
        self.btn.setEnabled(True)
        self.btn.setText("Close")
        if self.result is not None and self.error is None and not self._handed:
            self._handed = True
            if self.on_done is not None:
                try:
                    self.on_done(self.result)
                    self._log("\nResult added to the project.")
                except Exception as e:  # noqa: BLE001
                    self._log(f"\n{type(e).__name__}: {e}")
                    self._log(traceback.format_exc())

    def _wait_thread(self):
        if self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(5000)

    def accept(self):
        self._wait_thread()
        super().accept()

    def done(self, r):
        self._wait_thread()
        super().done(r)

    def _failed(self, msg):
        self.error = msg
        self._log("\n" + ("Cancelled." if msg == "Cancelled" else "FAILED — " + msg.split("\n\n")[0]))
        if msg != "Cancelled":
            from core.deps import module_from_error
            mod = module_from_error(msg)
            if mod:
                from ui.deps_dialog import missing_module_box
                missing_module_box(mod, self)
            elif len(msg.split("\n\n")) > 1:
                self._log(msg.split("\n\n", 1)[1])

    def _on_button(self):
        if self.running:
            self.stop.set()
            self.btn.setEnabled(False)
            self._log("Cancelling… (finishes the current step)")
        else:
            self.accept()

    def closeEvent(self, e):
        if self.running:
            self.stop.set()
            e.ignore()
            self._log("Cancelling… (finishes the current step)")
            return
        super().closeEvent(e)

    def reject(self):
        if self.running:
            self._on_button()
            return
        super().reject()

    def _save_log(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save log", "log.txt", "Text (*.txt)")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.txt.toPlainText())


# ================================================================ photos / video → point cloud
QUALITY = {"Low (fast)": (1200, 4000, 0.5, 3), "Medium": (2000, 8000, 0.5, 2), "High": (3000, 12000, 0.5, 1),
           "Ultra (slow)": (4000, 16000, 1.0, 1)}


class PhotoDialog(QDialog):
    def __init__(self, project_crs=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Photogrammetry — photos / video to point cloud")
        self.resize(640, 820)
        self.project_crs = project_crs
        self.files = []
        self.gcp_obs = []
        self.epsg = None
        try:
            from core import crs as crsmod
            self.epsg = crsmod.epsg_of(project_crs) if project_crs is not None else None
        except Exception:  # noqa: BLE001
            self.epsg = None
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 14, 20, 14)
        v.setSpacing(6)
        v.addWidget(_title("Photos / video → point cloud"))
        v.addWidget(_muted("Classic structure from motion + multi-view stereo (no AI models). Photos need ≥ 60 % "
                           "overlap, sharp texture and varied viewpoints; avoid pure rotation from one spot."))
        # ---- input
        g = QGroupBox("Input")
        gv = QVBoxLayout(g)
        r = QHBoxLayout()
        self.rb_photos = QRadioButton("Photos", checked=True)
        self.rb_video = QRadioButton("Video")
        r.addWidget(self.rb_photos)
        r.addWidget(self.rb_video)
        r.addStretch(1)
        gv.addLayout(r)
        self.in_stack = QStackedWidget()
        w1 = QWidget()
        h1 = QHBoxLayout(w1)
        h1.setContentsMargins(0, 0, 0, 0)
        b = QPushButton("Add Photos…")
        b.clicked.connect(self._add_photos)
        h1.addWidget(b)
        b = QPushButton("Add Folder…")
        b.clicked.connect(self._add_folder)
        h1.addWidget(b)
        b = QPushButton("Clear")
        b.clicked.connect(self._clear)
        h1.addWidget(b)
        self.lbl_files = _muted("No photos")
        h1.addWidget(self.lbl_files, 1)
        self.in_stack.addWidget(w1)
        w2 = QWidget()
        f2 = QFormLayout(w2)
        f2.setContentsMargins(0, 0, 0, 0)
        vr = QHBoxLayout()
        self.ed_video = QLineEdit()
        self.ed_video.setReadOnly(True)
        b = QPushButton("…")
        b.setFixedWidth(34)
        b.clicked.connect(self._pick_video)
        vr.addWidget(self.ed_video, 1)
        vr.addWidget(b)
        f2.addRow("Video file", vr)
        self.lbl_video = _muted("")
        f2.addRow("", self.lbl_video)
        self.sp_every = _dsp(1.0, 0.05, 60, 0.25, 2, " s")
        self.sp_every.setToolTip("One frame per interval — the sharpest frame of each interval is kept")
        self.sp_maxf = _isp(250, 5, 3000)
        self.sp_start = _dsp(0, 0, 1e6, 1, 1, " s")
        self.sp_end = _dsp(0, 0, 1e6, 1, 1, " s")
        self.sp_end.setSpecialValueText("end")
        f2.addRow("Frame every", self.sp_every)
        f2.addRow("Max. frames", self.sp_maxf)
        se = QHBoxLayout()
        se.addWidget(self.sp_start)
        se.addWidget(QLabel("to"))
        se.addWidget(self.sp_end)
        f2.addRow("Use from", se)
        self.in_stack.addWidget(w2)
        gv.addWidget(self.in_stack)
        self.rb_photos.toggled.connect(lambda on: self.in_stack.setCurrentIndex(0 if on else 1))
        v.addWidget(g)
        # ---- camera / quality
        g = QGroupBox("Camera and quality")
        f = QFormLayout(g)
        self.cmb_quality = QComboBox()
        self.cmb_quality.addItems(list(QUALITY))
        self.cmb_quality.setCurrentText("Medium")
        f.addRow("Quality", self.cmb_quality)
        self.cmb_focal = QComboBox()
        self.cmb_focal.addItems(["From EXIF (automatic)", "Focal length (mm) + sensor width (mm)",
                                 "Focal length (pixels)"])
        f.addRow("Focal length", self.cmb_focal)
        fr = QHBoxLayout()
        self.sp_fmm = _dsp(24, 1, 2000, 0.5, 2, " mm")
        self.sp_sensor = _dsp(13.2, 1, 100, 0.1, 2, " mm")
        self.sp_fpx = _dsp(3000, 50, 100000, 10, 1, " px")
        for w_ in (self.sp_fmm, self.sp_sensor, self.sp_fpx):
            fr.addWidget(w_)
        f.addRow("", fr)
        self.cmb_focal.currentIndexChanged.connect(self._focal_vis)
        self._focal_vis()
        self.chk_one = QCheckBox("All images from one camera (shared calibration)", checked=True)
        self.chk_refine = QCheckBox("Self-calibrate focal length and lens distortion", checked=True)
        f.addRow(self.chk_one)
        f.addRow(self.chk_refine)
        self.cmb_pairs = QComboBox()
        for k, t in (("auto", "Automatic"), ("exhaustive", "All pairs (≤ ~80 images)"),
                     ("sequential", "Sequential (video, strips)"), ("gps", "Nearest by GPS")):
            self.cmb_pairs.addItem(t, k)
        f.addRow("Image pairs", self.cmb_pairs)
        v.addWidget(g)
        # ---- georeferencing
        g = QGroupBox("Georeferencing")
        f = QFormLayout(g)
        self.cmb_geo = QComboBox()
        for k, t in (("auto", "Automatic (GCPs, else camera GPS, else level)"), ("gps", "Camera GPS (EXIF)"),
                     ("gcp", "Ground control points"), ("level", "Level only — aerial / nadir"),
                     ("upright", "Level only — ground / handheld"), ("none", "None (model frame)")):
            self.cmb_geo.addItem(t, k)
        f.addRow("Method", self.cmb_geo)
        er = QHBoxLayout()
        self.lbl_epsg = QLabel()
        b = QPushButton("Choose…")
        b.clicked.connect(self._pick_crs)
        er.addWidget(self.lbl_epsg, 1)
        er.addWidget(b)
        f.addRow("Output CRS", er)
        self._update_epsg()
        gr = QHBoxLayout()
        self.ed_gcp = QLineEdit()
        self.ed_gcp.setPlaceholderText("CSV: name, X, Y, Z")
        b = QPushButton("…")
        b.setFixedWidth(34)
        b.clicked.connect(self._pick_gcp)
        gr.addWidget(self.ed_gcp, 1)
        gr.addWidget(b)
        f.addRow("GCP coordinates", gr)
        orow = QHBoxLayout()
        b = QPushButton("Mark GCPs in Photos…")
        b.clicked.connect(self._mark_gcps)
        orow.addWidget(b)
        b = QPushButton("Load Marks…")
        b.clicked.connect(self._load_marks)
        orow.addWidget(b)
        self.lbl_obs = _muted("0 marks")
        orow.addWidget(self.lbl_obs, 1)
        f.addRow("Image marks", orow)
        self.ed_check = QLineEdit()
        self.ed_check.setPlaceholderText("names used as check points only, e.g. CP1, CP2")
        f.addRow("Check points", self.ed_check)
        v.addWidget(g)
        # ---- dense
        g = QGroupBox("Dense point cloud")
        f = QFormLayout(g)
        self.chk_dense = QCheckBox("Dense matching (SGBM) and multi-view fusion", checked=True)
        f.addRow(self.chk_dense)
        self.cmb_density = QComboBox()
        for t, s_, st in (("Lowest", 0.25, 2), ("Low", 0.5, 2), ("Medium", 0.5, 1), ("High", 1.0, 1)):
            self.cmb_density.addItem(t, (s_, st))
        self.cmb_density.setCurrentIndex(1)
        f.addRow("Density", self.cmb_density)
        self.sp_minv = _isp(2, 1, 10, " pair(s)")
        self.sp_minv.setToolTip("A dense point is kept only where this many image pairs agree (noise filter)")
        f.addRow("Keep where seen by ≥", self.sp_minv)
        self.chk_sparse = QCheckBox("Also add the sparse tie points", checked=False)
        self.chk_cams = QCheckBox("Add camera positions as a vector layer", checked=True)
        f.addRow(self.chk_sparse)
        f.addRow(self.chk_cams)
        v.addWidget(g)
        # ---- output
        orow = QHBoxLayout()
        self.ed_out = QLineEdit()
        self.ed_out.setPlaceholderText("Output folder (LAS, cameras CSV, report)")
        b = QPushButton("…")
        b.setFixedWidth(34)
        b.clicked.connect(self._pick_out)
        orow.addWidget(QLabel("Output"))
        orow.addWidget(self.ed_out, 1)
        orow.addWidget(b)
        v.addLayout(orow)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Run")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    # ---- inputs
    def _set_files(self, files):
        from core.sfm.images import IMAGE_EXT, read_exif
        self.files = sorted({f for f in files if f.lower().endswith(IMAGE_EXT)},
                            key=lambda p: os.path.basename(p).lower())
        if not self.files:
            self.lbl_files.setText("No photos")
            return
        ex = [read_exif(p) for p in self.files[:200]]
        gps = sum("lat" in e for e in ex)
        f35 = sum(bool(e.get("focal35")) for e in ex)
        cam = next((f"{e.get('make', '')} {e.get('model', '')}".strip() for e in ex if e.get("model")), "")
        self.lbl_files.setText(f"{len(self.files)} photos · {gps} with GPS · {f35} with 35 mm focal"
                               + (f" · {cam}" if cam else ""))
        if not self.ed_out.text():
            self.ed_out.setText(os.path.join(os.path.dirname(self.files[0]), "pcs_photogrammetry"))

    def _add_photos(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Photos", "", IMG_FILTER)
        if fs:
            self._set_files(self.files + fs)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder with photos")
        if d:
            from core.sfm.images import list_images
            self._set_files(self.files + list_images(d))

    def _clear(self):
        self.files = []
        self.lbl_files.setText("No photos")

    def _pick_video(self):
        p, _ = QFileDialog.getOpenFileName(self, "Video", "", VIDEO_FILTER)
        if not p:
            return
        self.ed_video.setText(p)
        try:
            from core.sfm.images import video_info
            inf = video_info(p)
            self.lbl_video.setText(f"{inf['width']}×{inf['height']} · {inf['fps']:.2f} fps · "
                                   f"{inf['duration']:.1f} s ({inf['frames']:,} frames)")
            if inf["duration"] > 0:
                self.sp_every.setValue(max(0.2, round(inf["duration"] / 150, 2)))
        except Exception as e:  # noqa: BLE001
            self.lbl_video.setText(f"{type(e).__name__}: {e}")
        if not self.ed_out.text():
            self.ed_out.setText(os.path.splitext(p)[0] + "_pcs")

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder")
        if d:
            self.ed_out.setText(d)

    def _focal_vis(self):
        k = self.cmb_focal.currentIndex()
        self.sp_fmm.setVisible(k == 1)
        self.sp_sensor.setVisible(k == 1)
        self.sp_fpx.setVisible(k == 2)

    def _update_epsg(self):
        self.lbl_epsg.setText(f"EPSG:{self.epsg}" if self.epsg else "not set (needed for GPS)")

    def _pick_crs(self):
        from ui.crs_dialog import CrsDialog
        from core import crs as crsmod
        ok, c = CrsDialog.ask(self, "Output coordinate system", "Projected system (metres) for the result.",
                              initial=self.project_crs, allow_unknown=True, warn_geographic=True)
        if ok:
            self.epsg = crsmod.epsg_of(c) if c is not None else None
            self._update_epsg()

    def _pick_gcp(self):
        p, _ = QFileDialog.getOpenFileName(self, "GCP coordinates", "", "Text (*.csv *.txt);;All files (*)")
        if p:
            self.ed_gcp.setText(p)

    def _mark_gcps(self):
        if not self.files and not self.ed_video.text():
            QMessageBox.information(self, "Mark GCPs", "Add the photos first.")
            return
        if not self.files:
            QMessageBox.information(self, "Mark GCPs", "For video, run once without GCPs, or extract the frames "
                                    "first — GCPs are marked on photos.")
            return
        names = []
        if self.ed_gcp.text():
            try:
                from core.sfm.georef import read_gcp_coordinates
                names = sorted(read_gcp_coordinates(self.ed_gcp.text()))
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "GCP file", f"{type(e).__name__}: {e}")
        dlg = GcpMarkDialog(self.files, names, self.gcp_obs, self)
        if dlg.exec() == QDialog.Accepted:
            self.gcp_obs = dlg.observations()
            self.lbl_obs.setText(f"{len(self.gcp_obs)} marks on {len({o[1] for o in self.gcp_obs})} GCPs")

    def _load_marks(self):
        p, _ = QFileDialog.getOpenFileName(self, "GCP image marks", "", "CSV (*.csv *.txt)")
        if p:
            from core.sfm.georef import read_gcp_observations
            self.gcp_obs = read_gcp_observations(p)
            self.lbl_obs.setText(f"{len(self.gcp_obs)} marks on {len({o[1] for o in self.gcp_obs})} GCPs")

    def _accept(self):
        if self.rb_photos.isChecked() and len(self.files) < 2:
            QMessageBox.warning(self, "Photogrammetry", "Add at least 2 photos (typically 20–500).")
            return
        if self.rb_video.isChecked() and not self.ed_video.text():
            QMessageBox.warning(self, "Photogrammetry", "Choose a video file.")
            return
        if not self.ed_out.text():
            QMessageBox.warning(self, "Photogrammetry", "Choose an output folder.")
            return
        geo = self.cmb_geo.currentData()
        if geo == "gps" and not self.epsg:
            QMessageBox.warning(self, "Photogrammetry", "Choose the output coordinate system (EPSG) for GPS.")
            return
        if geo == "gcp" and (not self.ed_gcp.text() or len(self.gcp_obs) < 6):
            QMessageBox.warning(self, "Photogrammetry", "GCPs need the coordinate file and marks of ≥ 3 GCPs, "
                                "each in ≥ 2 photos.")
            return
        self.accept()

    def settings(self):
        from core.sfm.pipeline import PhotoParams
        md, nf, _, _ = QUALITY[self.cmb_quality.currentText()]
        scale, step = self.cmb_density.currentData()
        geo = self.cmb_geo.currentData()
        p = PhotoParams(images=list(self.files), out_dir=self.ed_out.text(), max_dim=md, n_features=nf,
                        pair_mode=self.cmb_pairs.currentData(), one_camera=self.chk_one.isChecked(),
                        refine_intrinsics=self.chk_refine.isChecked(),
                        georef="level" if geo == "upright" else geo, level="upright" if geo == "upright" else "nadir",
                        epsg=int(self.epsg or 0), gcp_coords=self.ed_gcp.text(), gcp_obs=list(self.gcp_obs),
                        gcp_check=[s.strip() for s in self.ed_check.text().split(",") if s.strip()],
                        dense=self.chk_dense.isChecked(), dense_scale=scale, dense_step=step,
                        min_views=self.sp_minv.value(), workers=max(2, min(8, os.cpu_count() or 4)))
        k = self.cmb_focal.currentIndex()
        if k == 1:
            p.focal_mm, p.sensor_mm = self.sp_fmm.value(), self.sp_sensor.value()
        elif k == 2:
            p.focal_px = self.sp_fpx.value()
        video = None
        if self.rb_video.isChecked():
            video = dict(path=self.ed_video.text(), every_s=self.sp_every.value(), max_frames=self.sp_maxf.value(),
                         start_s=self.sp_start.value(), end_s=self.sp_end.value())
            p.name = os.path.splitext(os.path.basename(self.ed_video.text()))[0]
            if p.pair_mode == "auto":
                p.pair_mode = "sequential"
        else:
            p.name = os.path.basename(os.path.dirname(self.files[0])) or "photogrammetry"
        return p, video, dict(sparse=self.chk_sparse.isChecked(), cameras=self.chk_cams.isChecked())


# ================================================================ GCP marking
class _ImageView(QGraphicsView):
    clicked = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.pix = None
        self._press = None

    def set_image(self, path):
        from core.sfm.images import imread
        img = imread(path)
        h, w = img.shape[:2]
        rgb = np.ascontiguousarray(img[:, :, ::-1])
        qi = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.scene().clear()
        self.pix = QGraphicsPixmapItem(QPixmap.fromImage(qi))
        self.scene().addItem(self.pix)
        self.scene().setSceneRect(QRectF(0, 0, w, h))
        self.fitInView(self.pix, Qt.KeepAspectRatio)

    def wheelEvent(self, e):
        # proportional to the wheel / trackpad delta (macOS trackpads send many small steps)
        d = e.angleDelta().y() or e.pixelDelta().y()
        if not d:
            return
        f = float(np.clip(1.0015 ** d, 0.5, 2.0))
        cur = self.transform().m11()
        if (cur > 40 and f > 1) or (cur < 0.02 and f < 1):
            return
        self.scale(f, f)

    def mousePressEvent(self, e):
        self._press = e.position()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self._press is not None and (e.position() - self._press).manhattanLength() < 4 and \
                e.button() == Qt.LeftButton and self.pix is not None:
            p = self.mapToScene(e.position().toPoint())
            self.clicked.emit(p.x(), p.y())
        self._press = None

    def mark(self, x, y, name, current=False):
        r = 9 / max(self.transform().m11(), 1e-6)
        pen = QPen(QColor("#ffd166" if current else "#ff5d73"))
        pen.setCosmetic(True)
        pen.setWidth(2)
        el = QGraphicsEllipseItem(x - r, y - r, 2 * r, 2 * r)
        el.setPen(pen)
        self.scene().addItem(el)
        for dx, dy in ((r * 1.6, 0), (0, r * 1.6)):
            ln = self.scene().addLine(x - dx, y - dy, x + dx, y + dy, pen)
            ln.setZValue(2)
        t = QGraphicsSimpleTextItem(name)
        t.setBrush(QBrush(QColor("#ffffff")))
        t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
        t.setPos(QPointF(x + r, y + r))
        self.scene().addItem(t)


class GcpMarkDialog(QDialog):
    """Click each GCP in the photos where it is visible (≥ 2 photos per GCP, ≥ 3 GCPs)."""

    def __init__(self, files, gcp_names, obs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mark ground control points")
        self.resize(1200, 760)
        self.files = files
        self.obs = {(o[0], o[1]): (o[2], o[3]) for o in obs}
        v = QVBoxLayout(self)
        v.addWidget(_muted("Choose a GCP, then click its centre in every photo where it is visible (wheel = zoom, "
                           "drag = pan). Right-click a mark in the table to delete it. Coordinates are in original "
                           "image pixels."))
        sp = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("GCP"))
        self.lst_gcp = QListWidget()
        for n in gcp_names or ["GCP1", "GCP2", "GCP3"]:
            self.lst_gcp.addItem(n)
        self.lst_gcp.setCurrentRow(0)
        lv.addWidget(self.lst_gcp, 1)
        r = QHBoxLayout()
        self.ed_new = QLineEdit()
        self.ed_new.setPlaceholderText("new GCP name")
        b = QPushButton("Add")
        b.clicked.connect(self._add_gcp)
        r.addWidget(self.ed_new, 1)
        r.addWidget(b)
        lv.addLayout(r)
        lv.addWidget(QLabel("Photos"))
        self.lst_img = QListWidget()
        for f in files:
            self.lst_img.addItem(os.path.basename(f))
        lv.addWidget(self.lst_img, 2)
        sp.addWidget(left)
        self.view = _ImageView()
        self.view.clicked.connect(self._on_click)
        sp.addWidget(self.view)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("Marks"))
        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["Photo", "GCP", "x", "y"])
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl.customContextMenuRequested.connect(self._delete_row)
        rv.addWidget(self.tbl, 1)
        r = QHBoxLayout()
        b = QPushButton("Save Marks…")
        b.clicked.connect(self._save)
        r.addWidget(b)
        rv.addLayout(r)
        sp.addWidget(right)
        sp.setSizes([220, 760, 260])
        v.addWidget(sp, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.lst_img.currentRowChanged.connect(self._show)
        self.lst_gcp.currentRowChanged.connect(lambda _: self._redraw())
        self._scale = 1.0
        if files:
            self.lst_img.setCurrentRow(0)
        self._fill()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _add_gcp(self):
        n = self.ed_new.text().strip()
        if n:
            self.lst_gcp.addItem(n)
            self.lst_gcp.setCurrentRow(self.lst_gcp.count() - 1)
            self.ed_new.clear()

    def _cur_img(self):
        i = self.lst_img.currentRow()
        return os.path.basename(self.files[i]) if 0 <= i < len(self.files) else None

    def _show(self, i):
        if 0 <= i < len(self.files):
            self.view.set_image(self.files[i])
            self._redraw()

    def _redraw(self):
        img = self._cur_img()
        if img is None or self.view.pix is None:
            return
        for it in list(self.view.scene().items()):
            if it is not self.view.pix:
                self.view.scene().removeItem(it)
        cur = self.lst_gcp.currentItem().text() if self.lst_gcp.currentItem() else None
        for (im, g), (x, y) in self.obs.items():
            if im == img:
                self.view.mark(x, y, g, g == cur)

    def _on_click(self, x, y):
        img = self._cur_img()
        it = self.lst_gcp.currentItem()
        if img is None or it is None:
            return
        self.obs[(img, it.text())] = (float(x), float(y))
        self._redraw()
        self._fill()

    def _fill(self):
        rows = sorted(self.obs.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        self.tbl.setRowCount(len(rows))
        for r, ((im, g), (x, y)) in enumerate(rows):
            for c, val in enumerate((im, g, f"{x:.1f}", f"{y:.1f}")):
                self.tbl.setItem(r, c, QTableWidgetItem(val))

    def _delete_row(self, pos):
        r = self.tbl.rowAt(pos.y())
        if r < 0:
            return
        key = (self.tbl.item(r, 0).text(), self.tbl.item(r, 1).text())
        self.obs.pop(key, None)
        self._fill()
        self._redraw()

    def _save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save marks", "gcp_marks.csv", "CSV (*.csv)")
        if p:
            from core.sfm.georef import write_gcp_observations
            write_gcp_observations(p, self.observations())

    def observations(self):
        return [(im, g, x, y) for (im, g), (x, y) in self.obs.items()]


# ================================================================ stereo rig
class RigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stereo camera rig → point cloud")
        self.resize(620, 720)
        self.left, self.right = [], []
        self.board_left, self.board_right = [], []
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 14, 20, 14)
        v.addWidget(_title("Stereo rig → point cloud"))
        v.addWidget(_muted("Synchronised left / right images give metric depth directly from the rig baseline. "
                           "Several pairs are tied together with structure from motion on the left images."))
        g = QGroupBox("Input")
        f = QFormLayout(g)
        self.cmb_in = QComboBox()
        self.cmb_in.addItems(["Two folders (left / right photos)", "Side-by-side photos (left | right)",
                              "Two videos (left / right)", "Side-by-side video (left | right)"])
        f.addRow("Format", self.cmb_in)
        r = QHBoxLayout()
        self.btn_a = QPushButton("Left…")
        self.btn_b = QPushButton("Right…")
        self.btn_a.clicked.connect(lambda: self._pick(0))
        self.btn_b.clicked.connect(lambda: self._pick(1))
        r.addWidget(self.btn_a)
        r.addWidget(self.btn_b)
        f.addRow("Files", r)
        self.lbl_in = _muted("Nothing chosen")
        f.addRow("", self.lbl_in)
        self.sp_every = _dsp(1.0, 0.05, 60, 0.25, 2, " s")
        self.sp_maxf = _isp(120, 1, 3000)
        f.addRow("Video: frame every", self.sp_every)
        f.addRow("Video: max. frames", self.sp_maxf)
        self.cmb_in.currentIndexChanged.connect(self._in_changed)
        self.paths = ["", ""]
        v.addWidget(g)
        g = QGroupBox("Calibration")
        f = QFormLayout(g)
        self.cmb_cal = QComboBox()
        self.cmb_cal.addItems(["Manual (rectified rig: focal, principal point, baseline)",
                               "Calibration file (JSON / OpenCV YAML / XML)", "Chessboard images (calibrate now)"])
        f.addRow("Source", self.cmb_cal)
        self.cal_stack = QStackedWidget()
        w = QWidget()
        mf = QFormLayout(w)
        mf.setContentsMargins(0, 0, 0, 0)
        self.sp_f = _dsp(1000, 10, 100000, 10, 1, " px")
        self.sp_cx = _dsp(0, 0, 100000, 1, 1, " px")
        self.sp_cy = _dsp(0, 0, 100000, 1, 1, " px")
        self.sp_cx.setSpecialValueText("image centre")
        self.sp_cy.setSpecialValueText("image centre")
        self.sp_b = _dsp(0.12, 0.001, 100, 0.01, 4, " m")
        mf.addRow("Focal length", self.sp_f)
        mf.addRow("cx", self.sp_cx)
        mf.addRow("cy", self.sp_cy)
        mf.addRow("Baseline", self.sp_b)
        mf.addRow(_muted("Values refer to the full-size images."))
        self.cal_stack.addWidget(w)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self.ed_cal = QLineEdit()
        b = QPushButton("…")
        b.setFixedWidth(34)
        b.clicked.connect(self._pick_cal)
        h.addWidget(self.ed_cal, 1)
        h.addWidget(b)
        self.cal_stack.addWidget(w)
        w = QWidget()
        cf = QFormLayout(w)
        cf.setContentsMargins(0, 0, 0, 0)
        r = QHBoxLayout()
        b1 = QPushButton("Board Left…")
        b2 = QPushButton("Board Right…")
        b1.clicked.connect(lambda: self._pick_board(0))
        b2.clicked.connect(lambda: self._pick_board(1))
        r.addWidget(b1)
        r.addWidget(b2)
        cf.addRow("Images", r)
        self.lbl_board = _muted("")
        cf.addRow("", self.lbl_board)
        self.sp_cols = _isp(9, 3, 50)
        self.sp_rows = _isp(6, 3, 50)
        self.sp_sq = _dsp(0.025, 0.001, 2, 0.001, 4, " m")
        cf.addRow("Inner corners (columns)", self.sp_cols)
        cf.addRow("Inner corners (rows)", self.sp_rows)
        cf.addRow("Square size", self.sp_sq)
        self.cal_stack.addWidget(w)
        f.addRow(self.cal_stack)
        self.cmb_cal.currentIndexChanged.connect(self.cal_stack.setCurrentIndex)
        v.addWidget(g)
        g = QGroupBox("Processing")
        f = QFormLayout(g)
        self.cmb_or = QComboBox()
        for k, t in (("forward", "Camera looks ahead (horizontal)"), ("down", "Camera looks down (nadir)"),
                     ("camera", "Camera frame (x right, y down, z forward)")):
            self.cmb_or.addItem(t, k)
        f.addRow("Orientation", self.cmb_or)
        self.sp_dim = _isp(1600, 400, 6000, " px")
        self.sp_step = _isp(2, 1, 8, " px")
        f.addRow("Working size", self.sp_dim)
        f.addRow("Point every", self.sp_step)
        v.addWidget(g)
        orow = QHBoxLayout()
        self.ed_out = QLineEdit()
        b = QPushButton("…")
        b.setFixedWidth(34)
        b.clicked.connect(lambda: self.ed_out.setText(QFileDialog.getExistingDirectory(self, "Output") or
                                                      self.ed_out.text()))
        orow.addWidget(QLabel("Output"))
        orow.addWidget(self.ed_out, 1)
        orow.addWidget(b)
        v.addLayout(orow)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Run")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._in_changed()

    def showEvent(self, e):
        super().showEvent(e)
        dark_titlebar(self)

    def _in_changed(self):
        k = self.cmb_in.currentIndex()
        two = k in (0, 2)
        self.btn_b.setVisible(two)
        self.btn_a.setText("Left…" if two else ("Photos…" if k == 1 else "Video…"))
        video = k >= 2
        self.sp_every.setEnabled(video)
        self.sp_maxf.setEnabled(video)
        self.paths = ["", ""]
        self.left, self.right = [], []
        self.lbl_in.setText("Nothing chosen")

    def _pick(self, side):
        from core.sfm.images import list_images
        k = self.cmb_in.currentIndex()
        if k in (0, 1):
            d = QFileDialog.getExistingDirectory(self, "Folder with photos")
            if not d:
                return
            files = list_images(d)
            if k == 0:
                (self.left if side == 0 else self.right)[:] = files
            else:
                self.left = files
            self.paths[side] = d
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Video", "", VIDEO_FILTER)
            if not p:
                return
            self.paths[side] = p
        if not self.ed_out.text() and self.paths[side]:
            base = self.paths[side]
            self.ed_out.setText((os.path.splitext(base)[0] if os.path.isfile(base) else base) + "_pcs_stereo")
        if k == 0:
            self.lbl_in.setText(f"left {len(self.left)} · right {len(self.right)} photos")
        elif k == 1:
            self.lbl_in.setText(f"{len(self.left)} side-by-side photos")
        else:
            self.lbl_in.setText(" · ".join(os.path.basename(p) for p in self.paths if p))

    def _pick_cal(self):
        p, _ = QFileDialog.getOpenFileName(self, "Calibration", "", "Calibration (*.json *.yml *.yaml *.xml)")
        if p:
            self.ed_cal.setText(p)

    def _pick_board(self, side):
        from core.sfm.images import list_images
        d = QFileDialog.getExistingDirectory(self, "Chessboard photos")
        if d:
            (self.board_left if side == 0 else self.board_right)[:] = list_images(d)
            self.lbl_board.setText(f"left {len(self.board_left)} · right {len(self.board_right)}")

    def _accept(self):
        k = self.cmb_in.currentIndex()
        if (k == 0 and (not self.left or len(self.left) != len(self.right))) or (k == 1 and not self.left) or \
                (k == 2 and not all(self.paths)) or (k == 3 and not self.paths[0]):
            QMessageBox.warning(self, "Stereo rig", "Choose the input (left and right must have the same number "
                                "of photos, sorted in the same order).")
            return
        if not self.ed_out.text():
            QMessageBox.warning(self, "Stereo rig", "Choose an output folder.")
            return
        c = self.cmb_cal.currentIndex()
        if c == 1 and not self.ed_cal.text():
            QMessageBox.warning(self, "Stereo rig", "Choose the calibration file.")
            return
        if c == 2 and (len(self.board_left) < 5 or len(self.board_left) != len(self.board_right)):
            QMessageBox.warning(self, "Stereo rig", "Chessboard calibration needs ≥ 5 left / right image pairs.")
            return
        self.accept()

    def settings(self):
        from core.sfm.pipeline import RigParams
        c = self.cmb_cal.currentIndex()
        p = RigParams(out_dir=self.ed_out.text(), calib_mode=("manual", "file", "chessboard")[c],
                      calib_file=self.ed_cal.text(), f_px=self.sp_f.value(), cx=self.sp_cx.value(),
                      cy=self.sp_cy.value(), baseline_m=self.sp_b.value(), board_left=list(self.board_left),
                      board_right=list(self.board_right), board_cols=self.sp_cols.value(),
                      board_rows=self.sp_rows.value(), board_square_m=self.sp_sq.value(), max_dim=self.sp_dim.value(),
                      step=self.sp_step.value(), orientation=self.cmb_or.currentData())
        p.left, p.right = list(self.left), list(self.right)
        src = dict(kind=self.cmb_in.currentIndex(), paths=list(self.paths), every_s=self.sp_every.value(),
                   max_frames=self.sp_maxf.value())
        p.name = "stereo"
        return p, src


def prepare_rig_input(p, src, log=print, progress=None, cancel=None):
    """Video / side-by-side input → left / right file lists (written into the output folder)."""
    from core.sfm.images import extract_frames, split_side_by_side
    k = src["kind"]
    work = os.path.join(p.out_dir, "frames")
    if k == 1:
        log("Splitting side-by-side photos…")
        pairs = split_side_by_side(p.left, work)
        p.left, p.right = [a for a, _ in pairs], [b for _, b in pairs]
    elif k == 2:
        log("Extracting frames from the two videos…")
        p.left = extract_frames(src["paths"][0], os.path.join(work, "left"), src["every_s"], src["max_frames"],
                                sharpest=False, prefix="L", cancel=cancel)
        p.right = extract_frames(src["paths"][1], os.path.join(work, "right"), src["every_s"], src["max_frames"],
                                 sharpest=False, prefix="R", cancel=cancel)
        n = min(len(p.left), len(p.right))
        p.left, p.right = p.left[:n], p.right[:n]
    elif k == 3:
        log("Extracting frames from the side-by-side video…")
        pairs = extract_frames(src["paths"][0], work, src["every_s"], src["max_frames"], sharpest=True,
                               prefix="SBS", split="lr", cancel=cancel)
        p.left, p.right = [a for a, _ in pairs], [b for _, b in pairs]
    log(f"{len(p.left)} stereo pairs")
    return p
