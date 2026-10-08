"""Windows DLL version helpers (pure ctypes — safe to use before Qt is imported)."""
from __future__ import annotations

import os
import sys

RUNTIME_PREFIXES = ("msvcp", "vcruntime", "concrt", "ucrtbase", "api-ms-win-crt", "qt6core", "pyside6", "shiboken6")


def file_version(path):
    """File version like '14.50.35719.0', or None (non-Windows / no version resource)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.WinDLL("version")
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr, length = ctypes.c_void_p(), wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        info = ctypes.cast(ptr, ctypes.POINTER(wintypes.DWORD * 4)).contents   # signature, struct ver, MS, LS
        ms, ls = info[2], info[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def runtime_dlls(folder):
    """[(name, version)] of C/C++ runtime and Qt core DLLs directly inside a folder."""
    out = []
    try:
        for n in sorted(os.listdir(folder)):
            if n.lower().endswith(".dll") and n.lower().startswith(RUNTIME_PREFIXES):
                out.append((n, file_version(os.path.join(folder, n)) or "?"))
    except OSError:
        pass
    return out


def system32():
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
