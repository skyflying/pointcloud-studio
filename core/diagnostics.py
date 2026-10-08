"""Qt / OpenGL start-up diagnostics (used when the VisPy backend fails to load, and by --diagnose)."""
from __future__ import annotations

import os
import platform
import sys


def _loaded_module_path(name):
    """Windows: full path of an already-loaded DLL (e.g. which Qt6Core.dll is really in use)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetModuleHandleW.restype = wintypes.HMODULE
        k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        h = k32.GetModuleHandleW(name)
        if not h:
            return "(not loaded)"
        buf = ctypes.create_unicode_buffer(1024)
        k32.GetModuleFileNameW(h, buf, 1024)
        return buf.value
    except Exception as e:  # pragma: no cover
        return f"(unknown: {e})"


def report(error=None):
    lines = ["PointCloud Studio — Qt start-up diagnostics", ""]
    if error is not None:
        lines += [f"Error: {error}", ""]
    try:
        from core.version import __version__
        lines.append(f"App version: {__version__}")
    except Exception:
        pass
    lines += [f"Frozen (exe): {getattr(sys, 'frozen', False)}",
              f"Executable: {sys.executable}",
              f"Python: {sys.version.split()[0]} ({platform.architecture()[0]})",
              f"OS: {platform.platform()}", ""]
    try:
        import PySide6
        lines.append(f"PySide6 package: {PySide6.__version__}  ({os.path.dirname(PySide6.__file__)})")
        try:
            import shiboken6
            lines.append(f"shiboken6 package: {shiboken6.__version__}")
        except Exception as e:
            lines.append(f"shiboken6: import failed — {e}")
        from PySide6 import QtCore
        lines.append(f"Qt runtime (qVersion): {QtCore.qVersion()}   compiled for: {QtCore.__version__}")
        if QtCore.qVersion() != PySide6.__version__.split("+")[0]:
            lines.append("  !! Qt runtime version differs from PySide6 → a different Qt6Core is being loaded")
    except Exception as e:
        lines.append(f"PySide6 / QtCore import failed: {e}")
    for dll in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6OpenGL.dll", "opengl32.dll"):
        p = _loaded_module_path(dll)
        if p is not None:
            lines.append(f"Loaded {dll}: {p}")
    try:
        from PySide6 import QtOpenGL  # noqa: F401
        lines.append("PySide6.QtOpenGL: import OK")
    except Exception as e:
        lines.append(f"PySide6.QtOpenGL: import FAILED — {e}")

    # other Qt installations on PATH (classic cause of "procedure could not be found")
    hits = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for name in ("Qt6Core.dll", "Qt5Core.dll", "libQt6Core.so.6"):
            if d and os.path.isfile(os.path.join(d, name)):
                hits.append(os.path.join(d, name))
    lines += ["", "Qt libraries found on PATH:" if hits else "Qt libraries found on PATH: none"]
    lines += [f"  {h}" for h in hits]
    lines += ["", "Common fixes:",
              "  1. Rebuild with build_windows.bat (clean venv, PATH isolated) — do not build from Anaconda base.",
              "  2. Delete the whole old dist\\PointCloudStudio folder before copying a new build (never mix builds).",
              "  3. Running from source: pip install --force-reinstall PySide6  (all PySide6 parts same version).",
              "  4. On remote desktop / VMs without OpenGL 2.1: set QT_OPENGL=software before starting."]
    return "\n".join(lines)


def write_report(text, name="startup_error.txt"):
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()
    for folder in (base, os.path.expanduser("~")):
        try:
            path = os.path.join(folder, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            return path
        except OSError:
            continue
    return None
