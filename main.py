"""PointCloud Studio — entry point.

Usage:  PointCloudStudio [file] [--software-gl] [--selftest [--quiet]] [--diagnose]

Start-up is defensive because a windowed .exe has no console:
  * every stage is logged to startup.log (native crashes too, via faulthandler) in
    %LOCALAPPDATA%\\PointCloudStudio (Windows) or ~/Library/Application Support/PointCloudStudio (macOS)
  * a splash screen appears before the heavy libraries load
  * if the previous start never completed (e.g. a graphics-driver crash), this start uses
    Qt's software OpenGL automatically; --software-gl forces it
"""
import faulthandler
import os
import sys
import tempfile
import time
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)


# ------------------------------------------------------------------ logging / crash capture
def _data_dir():
    from core.platform_util import app_data_dir
    return app_data_dir()


DATA_DIR = _data_dir()
LOG_PATH = os.path.join(DATA_DIR, "startup.log")
from core.platform_util import startup_marker  # noqa: E402

MARKER = startup_marker()
try:
    _log_file = open(LOG_PATH, "w", encoding="utf-8", buffering=1)
except OSError:
    LOG_PATH = os.path.join(tempfile.gettempdir(), "PointCloudStudio_startup.log")
    _log_file = open(LOG_PATH, "w", encoding="utf-8", buffering=1)
faulthandler.enable(file=_log_file, all_threads=True)   # native crashes → traceback in the log
_T0 = time.perf_counter()


def log(msg):
    line = f"[{time.perf_counter() - _T0:7.2f}s] {msg}"
    try:
        _log_file.write(line + "\n")
    except Exception:
        pass
    if sys.stdout is not None:          # None in a windowed .exe
        try:
            print(line, flush=True)
        except Exception:
            pass


_in_hook = False

VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def _native_box(title, text):
    """Windows message box that works even when Qt cannot be loaded at all."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)   # MB_ICONERROR
    except Exception:
        pass


def _log_msvc_runtime():
    """Inventory of C/C++ runtime + Qt core DLLs Qt could pick up (mismatch → QtCore 'procedure not found')."""
    if sys.platform != "win32":
        return
    from core.winver import file_version, runtime_dlls, system32
    base = getattr(sys, "_MEIPASS", APP_DIR)
    for label, d in (("bundle", base), ("bundle\\PySide6", os.path.join(base, "PySide6"))):
        for name, v in runtime_dlls(d):
            log(f"  {label:<16} {name:<28} {v}")
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "ucrtbase.dll"):
        f = os.path.join(system32(), name)
        if os.path.isfile(f):
            log(f"  {'System32':<16} {name:<28} {file_version(f) or '?'}")


def _excepthook(tp, val, tb):
    global _in_hook
    text = "".join(traceback.format_exception(tp, val, tb))
    log("UNHANDLED EXCEPTION\n" + text)
    if _in_hook:
        return
    _in_hook = True
    try:
        qt_ok = "PySide6.QtWidgets" in sys.modules
        if qt_ok:
            from PySide6.QtWidgets import QApplication, QMessageBox
            qt_ok = QApplication.instance() is not None
        if qt_ok:
            try:
                from core.deps import module_from_error
                from ui.deps_dialog import missing_module_box
                mod = module_from_error(val)
                if mod and missing_module_box(mod, QApplication.activeWindow()):
                    return
            except Exception:
                pass
        if qt_ok:
            box = QMessageBox(QMessageBox.Critical, "PointCloud Studio — error",
                              f"{tp.__name__}: {val}\n\nDetails were saved to:\n{LOG_PATH}")
            box.setDetailedText(text)
            box.exec()
        else:
            _native_box("PointCloud Studio — error", f"{tp.__name__}: {val}\n\nDetails were saved to:\n{LOG_PATH}")
    except Exception:
        pass
    finally:
        _in_hook = False


sys.excepthook = _excepthook


def _clear_marker():
    try:
        if os.path.exists(MARKER):
            os.remove(MARKER)
    except OSError:
        pass


def _startup_failure(err):
    """Qt/OpenGL backend could not load: show a readable report instead of a bare traceback."""
    from core.diagnostics import report, write_report
    text = report(err) + f"\n\nStart-up log: {LOG_PATH}"
    path = write_report(text)
    log("BACKEND FAILURE\n" + text)
    _clear_marker()
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        _app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox(QMessageBox.Critical, "PointCloud Studio — cannot start",
                          "The 3D graphics backend (Qt OpenGL) could not be loaded.\n\n"
                          f"{err}\n\nA diagnostic report was saved to:\n{path}")
        box.setDetailedText(text)
        box.exec()
    except Exception:
        pass
    sys.exit(2)


def _splash(app):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
    from PySide6.QtWidgets import QSplashScreen

    from core.version import APP_NAME, __version__
    from ui.icons import point_cloud_pixmap
    pm = QPixmap(440, 220)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, 439, 219), 14, 14)
    p.fillPath(path, QColor("#16181d"))
    p.setPen(QColor("#2e333c"))
    p.drawPath(path)
    p.drawPixmap(24, 50, point_cloud_pixmap(120))
    f = QFont()
    f.setPointSize(17)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#e6e8eb"))
    p.drawText(QRectF(160, 70, 270, 30), Qt.AlignLeft | Qt.AlignVCenter, APP_NAME)
    f.setPointSize(10)
    f.setBold(False)
    p.setFont(f)
    p.setPen(QColor("#8b919c"))
    p.drawText(QRectF(160, 102, 270, 22), Qt.AlignLeft | Qt.AlignVCenter, f"Version {__version__}")
    p.end()
    sp = QSplashScreen(pm)
    sp.show()
    app.processEvents()
    return sp


def _say(splash, app, text):
    log(text)
    if splash is not None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        splash.showMessage(text, Qt.AlignBottom | Qt.AlignHCenter, QColor("#8b919c"))
        app.processEvents()


def _ensure_qt_installed():
    """Running from source without PySide6: no window can be shown, so ask in the terminal.
    Returns True when Qt is importable (start-up continues)."""
    from core import deps
    miss = deps.quick_missing()
    if not any(p[0] == "PySide6" for p in miss):
        return True
    text = deps.summary(miss)
    log("Qt (PySide6) is not installed\n" + text)
    print("\nPointCloud Studio cannot start:\n" + text)
    hint = ("bash setup_mac.sh" if sys.platform == "darwin" else
            f"{os.path.basename(sys.executable)} core/deps.py install")
    if deps.externally_managed():
        print(f"\nThis Python does not allow installing packages outside a virtual environment.\nRun:  {hint}")
        _native_box("PointCloud Studio — cannot start", text + f"\n\nRun:  {hint}")
        return False
    interactive = sys.stdin is not None and sys.stdin.isatty()
    if interactive:
        try:
            ans = input("\nInstall the missing modules now? [Y/n] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans in ("", "y", "yes"):
            failed = deps.install(miss, echo=print)
            if not any(p[2] for p in failed):
                print("\nModules installed — restarting…\n")
                _clear_marker()                       # a restart is not a failed start
                args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
                if sys.platform == "win32":
                    import subprocess
                    subprocess.Popen(args)
                    return False
                os.execv(sys.executable, args)
            print("\n" + deps.summary(failed))
            return False
    print(f"\nInstall them with:  {hint}")
    _native_box("PointCloud Studio — cannot start", text + f"\n\nInstall them with:  {hint}")
    return False


class _FileOpenHandler:
    """macOS: files opened from Finder (double-click, "Open With", drop on the Dock icon) arrive as
    QFileOpenEvent, not as arguments. The handler is installed right after QApplication exists —
    the launch event can be delivered by any processEvents() during start-up (splash screen,
    module dialog) — and keeps the paths until the main window is ready."""

    def __init__(self, app):
        from PySide6.QtCore import QEvent, QObject

        handler = self

        class _Filter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.FileOpen:
                    if event.file():
                        log(f"File open event: {event.file()}")
                        handler.open(event.file())
                    return True
                return False

        self.win = None
        self.pending = []
        self._filter = _Filter(app)
        app.installEventFilter(self._filter)

    def open(self, path):
        if self.win is None:
            self.pending.append(path)
            return
        w = self.win
        busy = (getattr(w, "_thread", None) is not None and w._thread.isRunning()) or bool(w._load_queue)
        if busy:
            w._load_queue.append(path)       # added to the project after the current load
        else:
            w.load_path(path)

    def attach(self, win):
        self.win = win
        paths, self.pending = self.pending, []
        for p in paths:                          # first file opens (if idle), the rest are queued and added
            self.open(p)


# ------------------------------------------------------------------ main
def main():
    import platform
    log(f"PointCloud Studio start · frozen={getattr(sys, 'frozen', False)} · Python {platform.python_version()} "
        f"· {platform.platform()} · args={sys.argv[1:]}")

    software = "--software-gl" in sys.argv or os.environ.get("QT_OPENGL", "").lower() == "software"
    if sys.platform == "darwin":
        # macOS always provides OpenGL 2.1 (Apple's own renderer) and Qt has no software-GL switch
        # there, so the Windows fallback does not apply.
        if software:
            log("--software-gl / QT_OPENGL=software ignored on macOS")
        software = False
    elif os.path.exists(MARKER) and not software and "--selftest" not in sys.argv:
        software = True
        log("Previous start did not complete → using software OpenGL this time")
    if software:
        os.environ["QT_OPENGL"] = "software"
    try:
        with open(MARKER, "w") as f:
            f.write(time.ctime())
    except OSError:
        pass

    _log_msvc_runtime()
    if not getattr(sys, "frozen", False) and not _ensure_qt_installed():
        _clear_marker()
        return 3
    log("Importing Qt…")
    try:
        from PySide6.QtCore import QCoreApplication, Qt, QTimer
    except ImportError as e:
        log(f"QtCore could not be loaded: {e}")
        _clear_marker()
        _native_box(
            "PointCloud Studio — cannot start",
            "Qt (QtCore) could not be loaded:\n"
            f"{e}\n\n"
            "A DLL inside the program folder does not match Qt (often an older C++ runtime).\n"
            "Please run check_env.bat in the project folder and send check_env_report.txt,\n"
            "together with this start-up log:\n"
            f"{LOG_PATH}\n\n"
            f"(If this PC has no recent Visual C++ Redistributable x64: {VC_REDIST_URL})")
        return 3
    if software:
        QCoreApplication.setAttribute(Qt.AA_UseSoftwareOpenGL)
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from core.version import APP_NAME, __version__
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(os.path.join(APP_DIR, "ui", "assets", "app.png")))
    app.aboutToQuit.connect(_clear_marker)
    file_open = _FileOpenHandler(app) if sys.platform == "darwin" else None
    from PySide6.QtCore import qVersion
    log(f"Qt {qVersion()} application created (software GL: {software})")

    if not getattr(sys, "frozen", False) and "--selftest" not in sys.argv and "--diagnose" not in sys.argv:
        from ui.theme import apply_theme as _theme
        _theme(app)
        from ui.deps_dialog import startup_check
        if not startup_check(log):
            _clear_marker()
            return 3

    if "--diagnose" in sys.argv:
        from core.diagnostics import report, write_report
        text = report() + f"\n\nStart-up log: {LOG_PATH}"
        path = write_report(text, "diagnose_report.txt")
        log("Diagnostics written to " + str(path))
        _clear_marker()
        if getattr(sys, "frozen", False) and "--quiet" not in sys.argv:
            from PySide6.QtWidgets import QMessageBox
            b = QMessageBox(QMessageBox.Information, "PointCloud Studio — diagnostics",
                            f"Diagnostic report saved to:\n{path}")
            b.setDetailedText(text)
            b.exec()
        else:
            print(text)
        return 0

    quiet = "--quiet" in sys.argv
    splash = None if quiet else _splash(app)

    _say(splash, app, "Starting 3D engine…")
    from vispy import app as vispy_app
    try:
        vispy_app.use_app("pyside6")
    except Exception as e:  # noqa: BLE001
        if splash:
            splash.close()
        _startup_failure(e)

    from ui.theme import apply_theme
    apply_theme(app)

    if "--selftest" in sys.argv:
        if splash:
            splash.close()
        from PySide6.QtWidgets import QMessageBox

        from core.selftest import run
        ok, _text, path = run(app)
        log(f"Self-test {'passed' if ok else 'FAILED'} → {path}")
        _clear_marker()
        if not quiet:
            QMessageBox.information(None, "Self-test", f"{'All checks passed' if ok else 'Some checks FAILED'}"
                                    f"\n\nReport saved to:\n{path}")
        return 0 if ok else 1

    _say(splash, app, "Loading modules…")
    from ui.main_window import MainWindow
    _say(splash, app, "Creating main window…")
    win = MainWindow()
    win.show()
    if splash:
        splash.finish(win)
    log("Main window shown")
    if software:
        win.statusBar().showMessage("Using software OpenGL (previous start failed or --software-gl). "
                                    "3D may be slower. Details: " + LOG_PATH, 20000)

    def _started():
        _clear_marker()
        log("Start-up complete")

    QTimer.singleShot(2500, _started)       # survive the first frames before declaring success
    args = [a for a in sys.argv[1:] if not a.startswith("--") and not a.startswith("-psn_")]
    if args:
        win.load_path(args[0])
    if file_open is not None:
        file_open.attach(win)
    return app.exec()


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit:
        raise
    except BaseException:
        _excepthook(*sys.exc_info())
        code = 1
    log(f"Exit code {code}")
    sys.exit(code)
