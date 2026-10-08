"""Environment check for PointCloud Studio builds (run via check_env.bat).

Test A: can this Python import Qt (source)?   Test B: can the packaged exe start Qt?
Plus a version inventory of every C/C++ runtime and Qt core DLL involved.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from core.winver import file_version, runtime_dlls, system32  # noqa: E402

problems = []


def h(title):
    print(f"\n=== {title} " + "=" * max(3, 70 - len(title)))


def vtuple(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return None


def inventory(label, folder):
    rows = runtime_dlls(folder)
    print(f"[{label}] {folder}")
    if not rows:
        print("    (none)")
    for n, v in rows:
        print(f"    {n:<30} {v}")
    return dict((n.lower(), v) for n, v in rows)


def run(cmd, timeout=90):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=HERE)
        code = r.returncode
        if code < 0:
            code &= 0xFFFFFFFF
        return code, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return "timeout", ""
    except Exception as e:
        return "error", str(e)


def code_text(code):
    if code == 0:
        return "OK"
    if isinstance(code, int) and (code & 0xFFFFFFFF) == 0xC0000139:
        return "CRASH 0xC0000139 (entry point not found → mismatched DLL)"
    if isinstance(code, int) and (code & 0xFFFFFFFF) == 0xC0000005:
        return "CRASH 0xC0000005 (access violation)"
    return f"exit code {code if not isinstance(code, int) else hex(code & 0xFFFFFFFF)}"


h("System")
print(f"OS:      {platform.platform()}")
print(f"Python:  {sys.version.split()[0]} {platform.architecture()[0]}  ({sys.executable})")
print(f"Base:    {sys.base_prefix}")

h("Packages")
try:
    import importlib.metadata as md
    for d in ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6", "pyinstaller", "vispy", "numpy"):
        try:
            print(f"    {d:<22} {md.version(d)}")
        except md.PackageNotFoundError:
            print(f"    {d:<22} (not installed)")
except Exception as e:
    print("    ", e)

h("Runtime / Qt DLL inventory")
sys32 = {}
for n in ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll", "ucrtbase.dll"):
    f = os.path.join(system32(), n)
    if os.path.isfile(f):
        sys32[n] = file_version(f) or "?"
print(f"[System32] {system32()}")
for n, v in sys32.items():
    print(f"    {n:<30} {v}")
inventory("Python base", sys.base_prefix)
try:
    import importlib.util
    for pkg in ("PySide6", "shiboken6"):
        spec = importlib.util.find_spec(pkg)
        if spec and spec.submodule_search_locations:
            inventory(pkg, list(spec.submodule_search_locations)[0])
except Exception as e:
    print("    ", e)

bundle = os.path.join(HERE, "dist", "PointCloudStudio", "_internal")
bundle_rows = {}
if os.path.isdir(bundle):
    bundle_rows = inventory("bundle _internal", bundle)
    bundle_rows.update({f"pyside6/{k}": v for k, v in inventory("bundle _internal\\PySide6",
                                                               os.path.join(bundle, "PySide6")).items()})
    # UCRT is part of Windows 10/11 — a bundled copy should not exist
    for n, v in bundle_rows.items():
        if n.split("/")[-1].startswith(("ucrtbase", "api-ms-win-crt")):
            problems.append(f"bundle contains Windows UCRT file {n} ({v}) — should come from Windows")
            break

h("DLLs on PATH that could interfere")
seen = False
for d in os.environ.get("PATH", "").split(os.pathsep):
    for n in ("msvcp140.dll", "vcruntime140_1.dll", "Qt6Core.dll", "Qt5Core.dll"):
        f = os.path.join(d, n) if d else ""
        if f and os.path.isfile(f):
            seen = True
            print(f"    {f}   {file_version(f) or '?'}")
if not seen:
    print("    none")

if os.path.isdir(os.path.join(sys.base_prefix, "conda-meta")):
    problems.append(f"this environment is based on Anaconda/Miniconda ({sys.base_prefix}) — its Python adds "
                    "Library\\bin to the DLL search path; build with python.org Python instead")
try:
    import importlib.metadata as _md
    _psv = _md.version("PySide6")
    for d in os.environ.get("PATH", "").split(os.pathsep):
        f = os.path.join(d, "Qt6Core.dll") if d else ""
        if f and os.path.isfile(f):
            v = file_version(f) or "?"
            if not v.startswith(_psv):
                problems.append(f"another Qt on PATH: {f} ({v}) ≠ PySide6 {_psv} → likely conflict")
except Exception:
    pass

h("Test A — import Qt with this Python (source)")
for mod in ("PySide6.QtCore", "PySide6.QtWidgets", "PySide6.QtOpenGL"):
    code, out = run([sys.executable, "-c", f"import {mod}, sys; from PySide6.QtCore import qVersion; print(qVersion())"])
    print(f"    {mod:<20} {code_text(code)}   {out.splitlines()[-1] if out else ''}")
    if code != 0:
        problems.append(f"source import of {mod} failed: {code_text(code)}")

h("Test B — packaged exe")
exe = os.path.join(HERE, "dist", "PointCloudStudio",
                   "PointCloudStudio_debug.exe" if sys.platform == "win32" else "PointCloudStudio_debug")
if os.path.isfile(exe):
    code, out = run([exe, "--diagnose", "--quiet"], timeout=120)
    print(f"    {os.path.basename(exe)} --diagnose  →  {code_text(code)}")
    for line in out.splitlines()[-25:]:
        print("      | " + line)
    if code != 0:
        problems.append(f"packaged exe failed: {code_text(code)}")
else:
    print("    (no build found — run build_windows.bat first)")

h("Summary")
if problems:
    for p in problems:
        print("  !! " + p)
else:
    print("  No problems detected.")
