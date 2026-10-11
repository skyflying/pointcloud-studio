"""Platform helpers (Windows / macOS / Linux) — safe to import before Qt."""
from __future__ import annotations

import os
import sys
import tempfile

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

APP_DIR_NAME = "PointCloudStudio"


def app_data_dir():
    """Per-user folder for logs and reports.

    Windows: %LOCALAPPDATA%\\PointCloudStudio
    macOS:   ~/Library/Application Support/PointCloudStudio
    Linux:   ~/.local/share/PointCloudStudio
    """
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif IS_MAC:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, APP_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        return tempfile.gettempdir()


def report_dir():
    """Where --selftest / --diagnose reports are written.

    Next to the executable on Windows (as before). On macOS a frozen app must not write
    inside its own .app bundle (that breaks the code signature), so reports go to the
    per-user data folder instead. Running from source: the current folder.
    """
    if getattr(sys, "frozen", False):
        if IS_MAC:
            return app_data_dir()
        return os.path.dirname(sys.executable)
    return os.getcwd()


_MAC_KEYS = (("Ctrl+Shift+", "⇧⌘"), ("Ctrl+", "⌘"), ("Ctrl", "⌘"), ("Alt+", "⌥"), ("Alt", "⌥"))


def key_text(text):
    """Shortcut labels for display: 'Ctrl+Z' → '⌘Z' on macOS (Qt maps Ctrl to Command there)."""
    if not IS_MAC or not text:
        return text
    for a, b in _MAC_KEYS:
        text = text.replace(a, b)
    return text
