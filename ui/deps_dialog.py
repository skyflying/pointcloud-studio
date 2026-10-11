"""Missing-module handling inside the app (running from source).

* startup_check(): before the main window opens, lists required / optional modules that are not
  installed and offers to install them (pip, one by one), then restarts the program.
* missing_module_box(): when a feature fails with ModuleNotFoundError (e.g. GeoTIFF export without
  rasterio), explains which package is needed and offers the same installer.

A packaged app (.exe / .app) cannot install packages; there the message says to rebuild it.
Only PySide6 is used here (vispy etc. may be the missing ones).
"""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QProcess, QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from core import deps

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS_SCRIPT = os.path.join(APP_DIR, "core", "deps.py")
FROZEN = getattr(sys, "frozen", False)


def _settings():
    return QSettings("PointCloudStudio", "PointCloudStudio")


def _setup_hint():
    if sys.platform == "darwin":
        return "run  bash setup_mac.sh  in the program folder (Terminal)"
    if sys.platform == "win32":
        return "run:  python core\\deps.py install   (inside the program's virtual environment)"
    return "run:  python core/deps.py install   (inside a virtual environment)"


def restart():
    """Start the program again (new modules are only importable in a fresh process)."""
    from core.platform_util import clear_startup_marker
    clear_startup_marker()          # otherwise the next start would look like a crashed one
    args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
    if sys.platform == "win32":
        subprocess.Popen(args, cwd=APP_DIR)
        os._exit(0)
    os.execv(sys.executable, args)


class InstallDialog(QDialog):
    """Lists missing modules, installs them with pip (live log) and offers a restart."""

    def __init__(self, pkgs, parent=None, intro=None, allow_skip=True):
        super().__init__(parent)
        self.setWindowTitle("PointCloud Studio — missing modules")
        self.setMinimumSize(620, 420)
        self.pkgs = pkgs
        self.proc = None
        self.installed_ok = False
        required = [p for p in pkgs if p[2]]

        v = QVBoxLayout(self)
        head = QLabel(intro or ("Some Python modules are not installed:" if required else
                                "Some optional Python modules are not installed:"))
        head.setWordWrap(True)
        v.addWidget(head)
        lst = QLabel("\n".join(f"•  {p[1]}  —  {p[3]}" + ("" if p[2] else "  (optional)") for p in pkgs))
        lst.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(lst)

        managed = deps.externally_managed()
        if FROZEN:
            note = "This packaged program cannot install modules. Rebuild it with the build script."
        elif managed:
            note = ("This Python does not allow installing packages outside a virtual environment.\n"
                    f"To set one up, {_setup_hint()}.")
        else:
            note = "Click Install to download and install them now (internet connection needed)."
        self.note = QLabel(note)
        self.note.setWordWrap(True)
        v.addWidget(self.note)

        self.log = QPlainTextEdit(readOnly=True)
        f = QFont("Menlo" if sys.platform == "darwin" else "Consolas")
        f.setStyleHint(QFont.Monospace)
        self.log.setFont(f)
        self.log.setVisible(False)
        v.addWidget(self.log, 1)

        self.chk_skip = QCheckBox("Don't ask again for these optional modules")
        self.chk_skip.setVisible(allow_skip and not required)
        v.addWidget(self.chk_skip)

        row = QHBoxLayout()
        row.addStretch()
        self.btn_install = QPushButton("Install")
        self.btn_install.setProperty("primary", True)
        self.btn_install.setEnabled(not FROZEN and not managed)
        self.btn_install.clicked.connect(self._install)
        self.btn_restart = QPushButton("Restart now")
        self.btn_restart.setProperty("primary", True)
        self.btn_restart.setVisible(False)
        self.btn_restart.clicked.connect(restart)
        self.btn_close = QPushButton("Quit" if required else "Continue without")
        self.btn_close.clicked.connect(self._close)
        for b in (self.btn_install, self.btn_restart, self.btn_close):
            row.addWidget(b)
        v.addLayout(row)

    def _close(self):
        if self.chk_skip.isVisible() and self.chk_skip.isChecked():
            s = _settings()
            skip = set(filter(None, str(s.value("deps/skip_optional", "")).split(",")))
            skip |= {p[0] for p in self.pkgs if not p[2]}
            s.setValue("deps/skip_optional", ",".join(sorted(skip)))
        self.reject()

    def _install(self):
        self.btn_install.setEnabled(False)
        self.btn_close.setEnabled(False)
        self.log.setVisible(True)
        self.note.setText("Installing… (this can take a few minutes)")
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.setWorkingDirectory(APP_DIR)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._done)
        self.proc.start(sys.executable, ["-u", DEPS_SCRIPT, "install"])

    def _read(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        self.log.appendPlainText(text.rstrip("\n"))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _done(self, code, _status):
        import importlib
        importlib.invalidate_caches()          # make newly installed packages visible to this process
        still = [p for p in self.pkgs if not deps._probe(p[0])]
        self.btn_close.setEnabled(True)
        if not still:
            self.installed_ok = True
            self.note.setText("All modules installed. Restart the program to use them.")
            self.btn_restart.setVisible(True)
            # required modules only become usable after a restart; optional ones: continue now
            self.btn_close.setText("Quit" if any(p[2] for p in self.pkgs) else "Continue")
        else:
            req = any(p[2] for p in still)
            self.note.setText("Could not install: " + ", ".join(p[1] for p in still)
                              + (". The program cannot start without them — see the log above."
                                 if req else ". The program works without them (those features are unavailable)."))
            self.btn_install.setText("Try again")
            self.btn_install.setEnabled(True)
            if any(p[2] for p in self.pkgs) and not req:
                self.btn_restart.setVisible(True)


def startup_check(log=print):
    """Return True to continue starting, False to quit. Call after QApplication exists."""
    if FROZEN:
        return True
    miss = deps.quick_missing()
    if not miss:
        return True
    required = [p for p in miss if p[2]]
    log("Missing modules: " + ", ".join(p[1] for p in miss))
    if not required:
        skip = set(filter(None, str(_settings().value("deps/skip_optional", "")).split(",")))
        if all(p[0] in skip for p in miss):
            return True
    dlg = InstallDialog(miss)
    dlg.exec()
    # optional only: continue (whether installed or skipped); required: only after a restart
    return not required


def missing_module_box(module_name, parent=None):
    """Friendly message for a ModuleNotFoundError raised by a feature. True if it was handled."""
    pkg = deps.package_for_module(module_name)
    if pkg is None:
        return False
    if FROZEN:
        QMessageBox.warning(parent, "PointCloud Studio — module missing",
                            f"This feature needs the module '{pkg[1]}' ({pkg[3]}), which is not part of "
                            "this build.\n\nRebuild the program with the build script (build_windows.bat / "
                            "build_mac.sh), which installs it automatically.")
        return True
    InstallDialog([pkg], parent, intro="This feature needs a module that is not installed:",
                  allow_skip=False).exec()
    return True
