"""Dependency check and installer (standard library only — works before any package is installed).

Command line (used by setup_mac.sh / build_mac.sh, works on Windows too):
    python core/deps.py check              # list required / optional modules, exit 1 if a required one is missing
    python core/deps.py install            # install every missing module, one by one, then re-check
    python core/deps.py install --build    # … plus PyInstaller (for building the app)

Each package is installed separately, so one failure does not stop the others. A package is
first installed from a pre-built wheel only (newest version that has a wheel for this Python
and CPU — avoids compiling, which fails without Xcode / GDAL), and only if no wheel exists,
from source.

In the app (running from source): main.py calls missing() before start-up and offers to
install what is missing.
"""
from __future__ import annotations

import os
import subprocess
import sys
import sysconfig

# (import name, pip requirement, required?, what it is used for)
PACKAGES = [
    ("PySide6", "PySide6>=6.5", True, "user interface (Qt)"),
    ("vispy", "vispy>=0.14", True, "3D view"),
    ("numpy", "numpy>=1.24", True, "all calculations"),
    ("pandas", "pandas>=2.0", True, "XYZ / CSV import, routes"),
    ("laspy", "laspy>=2.5", True, "LAS / LAZ files"),
    ("scipy", "scipy>=1.10", True, "TIN, DEM, filters, classification"),
    ("lazrs", "lazrs>=0.5", False, "compressed LAZ files"),
    ("pyproj", "pyproj>=3.4", False, "coordinate systems (EPSG), reprojection"),
    ("rasterio", "rasterio>=1.3", False, "GeoTIFF import / export"),
    ("shapefile", "pyshp>=2.3", False, "Shapefile routes, polygons and contours"),
    ("contourpy", "contourpy>=1.0", False, "contour lines"),
    ("openpyxl", "openpyxl>=3.1", False, "Excel workbook export"),
    ("cv2", "opencv-python-headless>=4.8", False, "photogrammetry (SfM-MVS from photos / video, stereo rigs)"),
    ("open3d", "open3d>=0.17", False, "Poisson and ball-pivoting meshes (other mesh methods work without it)"),
]
BUILD_PACKAGES = [("PyInstaller", "pyinstaller", True, "building the app")]

PY_MIN, PY_MAX = (3, 10), (3, 13)      # versions with pre-built wheels for every package


def _probe(module):
    """True if `module` imports cleanly in a fresh interpreter (catches broken binary installs too)."""
    code = f"import {module}"
    if module == "PySide6":
        code = "from PySide6 import QtCore, QtGui, QtWidgets"
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


# Optional packages that are NOT offered at start-up (large, not available for every Python / Mac):
# installed on demand from the message shown when their feature is used.
ON_DEMAND = {"open3d"}


def quick_missing(include_optional=True):
    """Fast check for start-up (no sub-process): packages that are not installed at all."""
    import importlib.util
    out = []
    for p in PACKAGES:
        if not include_optional and not p[2]:
            continue
        if p[0] in ON_DEMAND:
            continue
        try:
            found = importlib.util.find_spec(p[0]) is not None
        except Exception:
            found = False
        if not found:
            out.append(p)
    return out


def package_for_module(name):
    """PACKAGES entry for an import name such as 'rasterio' or 'rasterio.warp', or None."""
    top = (name or "").split(".")[0]
    for p in PACKAGES:
        if p[0] == top:
            return p
    return None


def module_from_error(err):
    """Import name of the missing package behind an error (exception or message text), or None.

    Covers 'No module named X' and laspy's LAZ errors when no LAZ backend (lazrs) is installed."""
    import re
    if isinstance(err, ModuleNotFoundError) and getattr(err, "name", None):
        name = err.name
    else:
        text = str(err)
        m = re.search(r"No module named '([\w.]+)'", text)
        if m:
            name = m.group(1)
        elif "LazBackend" in text or ("laz" in text.lower() and "backend" in text.lower()):
            name = "lazrs"
        else:
            return None
    return name if package_for_module(name) else None


def requirements_mismatch(path=None):
    """Packages listed in requirements.txt but not in PACKAGES (keeps the two lists in step)."""
    import re
    path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt")
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return []
    known = {re.split(r"[<>=\[ ;]", p[1])[0].lower() for p in PACKAGES}
    out = []
    for line in lines:
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=\[ ;]", line)[0].lower()
        if name and name not in known:
            out.append(name)
    return out


def missing(include_optional=True, build=False):
    """[(module, requirement, required, purpose)] of packages that cannot be imported."""
    pkgs = PACKAGES + (BUILD_PACKAGES if build else [])
    return [p for p in pkgs if (include_optional or p[2]) and p[0] not in ON_DEMAND and not _probe(p[0])]


def python_ok():
    return PY_MIN <= sys.version_info[:2] <= PY_MAX


def in_venv():
    return sys.prefix != sys.base_prefix


def externally_managed():
    """Homebrew / system Pythons refuse 'pip install' outside a virtual environment (PEP 668)."""
    if in_venv():
        return False
    try:
        return os.path.isfile(os.path.join(sysconfig.get_paths()["stdlib"], "EXTERNALLY-MANAGED"))
    except Exception:
        return False


def pip_command(requirement, binary_only=True):
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade-strategy",
           "only-if-needed"]
    if binary_only:
        cmd += ["--only-binary", ":all:"]
    else:
        cmd += ["--prefer-binary"]
    return cmd + [requirement]


def _run(cmd, echo):
    echo("  $ pip " + " ".join(cmd[4:]))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail = []
    for line in p.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-15:]
        if line.startswith(("Successfully", "ERROR", "Requirement already")):
            echo("    " + line)
    return p.wait(), tail


def ensure_pip(echo=print):
    if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode != 0:
        echo("pip is missing — installing it (ensurepip)…")
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"])
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                    "--upgrade", "pip", "setuptools", "wheel"])


def install(pkgs, echo=print):
    """Install each package separately. Returns the list of packages that still fail to import."""
    ensure_pip(echo)
    failed = []
    for i, (module, req, required, purpose) in enumerate(pkgs, 1):
        echo(f"\n[{i}/{len(pkgs)}] {req}  ({purpose})")
        code, tail = _run(pip_command(req, binary_only=True), echo)
        if code != 0:
            echo("    no pre-built package for this Python / Mac — trying to build from source…")
            code, tail = _run(pip_command(req, binary_only=False), echo)
        if code == 0 and _probe(module):
            echo("    OK")
        else:
            echo("    FAILED")
            for line in tail[-6:]:
                echo("      " + line)
            failed.append((module, req, required, purpose))
    return failed


def summary(pkgs_missing):
    req = [p for p in pkgs_missing if p[2]]
    opt = [p for p in pkgs_missing if not p[2]]
    lines = []
    if req:
        lines.append("Required modules missing (the program cannot start):")
        lines += [f"  - {p[1]:<18} {p[3]}" for p in req]
    if opt:
        lines.append("Optional modules missing (these features are unavailable):")
        lines += [f"  - {p[1]:<18} {p[3]}" for p in opt]
    return "\n".join(lines) if lines else "All modules are installed."


def _cli(argv):
    build = "--build" in argv
    print(f"Python {sys.version.split()[0]} ({sys.executable})")
    if not python_ok():
        print(f"*** Python {PY_MIN[0]}.{PY_MIN[1]}–{PY_MAX[0]}.{PY_MAX[1]} is needed "
              f"(this is {sys.version_info[0]}.{sys.version_info[1]}): some packages have no pre-built version for it.")
    extra = requirements_mismatch()
    if extra:
        print("*** NOTE: in requirements.txt but not checked by core/deps.py: " + ", ".join(extra))
    miss = missing(build=build)
    if argv and argv[0] == "install" and miss:
        if externally_managed():
            print("*** This Python does not allow installing packages outside a virtual environment.\n"
                  "*** macOS: bash setup_mac.sh   ·   elsewhere: python3 -m venv .venv, activate it, run this again")
            return 2
        print(f"Installing {len(miss)} missing module(s)…")
        install(miss)
        miss = missing(build=build)
    print("\n" + summary(miss))
    return 1 if any(p[2] for p in miss) else 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
