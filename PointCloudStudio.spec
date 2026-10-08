# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller PointCloudStudio.spec --noconfirm
# Output: dist/PointCloudStudio/PointCloudStudio(.exe)   (one-folder build: fast start-up)
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

# ---- Guard 0: do not build with Anaconda / Miniconda Python.
# Its python3xx.dll adds anaconda3\Library\bin (which contains Anaconda's own Qt) to the DLL search path,
# and the packaged exe inherits that → "DLL load failed while importing QtCore: procedure could not be found".
if os.path.isdir(os.path.join(sys.base_prefix, "conda-meta")) and not os.environ.get("ALLOW_CONDA"):
    raise SystemExit("\n*** Building with Anaconda/Miniconda Python is not supported "
                     f"({sys.base_prefix}).\n*** Install Python 3.12 64-bit from python.org and run "
                     "build_windows.bat again (set ALLOW_CONDA=1 to override at your own risk).\n")

datas = [("ui/assets", "ui/assets")]
datas += collect_data_files("vispy")        # GLSL shaders, fonts, colormaps
datas += collect_data_files("pyproj")       # proj.db (EPSG definitions)
datas += collect_data_files("rasterio")     # bundled GDAL / PROJ data

binaries = collect_dynamic_libs("lazrs")    # LAZ codec (Rust extension)

# Qt's software OpenGL renderer (Mesa llvmpipe) — used by the automatic fallback / --software-gl
# when a graphics driver cannot provide OpenGL 2.1 (old drivers, remote desktop, VMs).
import glob as _glob
import os as _os
import PySide6 as _ps0
if sys.platform == "win32":
    for _f in (_glob.glob(_os.path.join(_os.path.dirname(_ps0.__file__), "opengl32sw.dll"))
               + _glob.glob(_os.path.join(_os.path.dirname(_ps0.__file__), "d3dcompiler_*.dll"))):
        binaries.append((_f, "PySide6"))

hiddenimports = (
    collect_submodules("rasterio")
    + collect_submodules("vispy.app.backends")
    + collect_submodules("vispy.visuals")
    + ["PySide6.QtTest",            # required by VisPy's PySide6 backend — do not exclude
       "lazrs", "laspy.compression", "pyproj.database", "scipy.spatial._qhull",
       "scipy.ndimage", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvg"]
)

# Optional packages VisPy / pandas may pick up if they are installed — not used by the app.
excludes = ["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "IPython", "jupyter", "notebook", "pytest",
            "cv2", "imageio", "imageio_ffmpeg", "skimage", "PIL", "sklearn", "numba", "llvmlite",
            "sympy", "networkx", "dask", "pyarrow", "tables", "h5py", "openpyxl", "sqlalchemy",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
            "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
            "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtPdf", "PySide6.QtNetwork",
            "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtXml"]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
# ---- Guard 1: every Qt / PySide6 / shiboken6 binary must come from the pip PySide6 packages.
# PyInstaller searches PATH for DLL dependencies; another Qt on PATH (Anaconda's Library\bin,
# GIS software, ...) can otherwise get bundled → "DLL load failed ... procedure could not be found".
import os
import importlib.metadata as _md
import PySide6 as _ps
import shiboken6 as _sb

_roots = tuple(os.path.normcase(os.path.dirname(m.__file__)) for m in (_ps, _sb))


def _is_qt(dest):
    n = os.path.basename(dest).lower()
    return n.startswith(("qt6", "qt5", "pyside6", "shiboken6", "libqt6", "libpyside6", "libshiboken6"))


_foreign = [b for b in a.binaries if _is_qt(b[0]) and not os.path.normcase(b[1]).startswith(_roots)]
if _foreign:
    print("\n*** WARNING: dropping Qt libraries that do NOT come from the PySide6 package:")
    for b in _foreign:
        print("      ", b[1])
    print("*** (another Qt is on PATH — build from a clean environment, see BUILD.md)\n")
    a.binaries = [b for b in a.binaries if b not in _foreign]

# ---- Guard 1b (Windows): use the Microsoft C++ runtime that ships WITH PySide6.
# PyInstaller tends to bundle msvcp140.dll / vcruntime140*.dll from C:\Windows\System32; if that
# copy is older than what Qt was built with, loading QtCore fails with
#   "DLL load failed while importing QtCore: The specified procedure could not be found" (0xc0000139).
if sys.platform == "win32":
    import glob as _g
    _psdir = os.path.dirname(_ps.__file__)
    _rt_prefix = ("msvcp140", "vcruntime140", "concrt140")
    _ps_rt = {os.path.basename(f).lower(): f for f in _g.glob(os.path.join(_psdir, "*.dll"))
              if os.path.basename(f).lower().startswith(_rt_prefix)}
    if _ps_rt:
        _replaced = sorted({b[1] for b in a.binaries if os.path.basename(b[0]).lower() in _ps_rt
                            and os.path.normcase(b[1]) != os.path.normcase(_ps_rt[os.path.basename(b[0]).lower()])})
        a.binaries = [b for b in a.binaries if os.path.basename(b[0]).lower() not in _ps_rt]
        for _name, _src in _ps_rt.items():
            a.binaries.append((_name, _src, "BINARY"))               # _internal (found first)
            a.binaries.append((f"PySide6/{_name}", _src, "BINARY"))
        print("MSVC runtime taken from PySide6:", ", ".join(sorted(_ps_rt)))
        for _r in _replaced:
            print("   replaced:", _r)
    else:
        print("*** NOTE: PySide6 ships no MSVC runtime here — target PCs need the latest "
              "Visual C++ Redistributable x64 (https://aka.ms/vs/17/release/vc_redist.x64.exe)")

# ---- Guard 1c (Windows): never bundle the Universal C Runtime (ucrtbase / api-ms-win-crt-*).
# It is part of Windows 10/11; copies from Anaconda / old SDKs are older and must not override it.
if sys.platform == "win32":
    _ucrt = [b for b in a.binaries
             if os.path.basename(b[0]).lower().startswith(("ucrtbase", "api-ms-win-crt-", "api-ms-win-core-"))]
    if _ucrt:
        print(f"Dropping {len(_ucrt)} bundled UCRT files (Windows provides them)")
        a.binaries = [b for b in a.binaries if b not in _ucrt]

# ---- Guard 2: PySide6 sub-packages must all be the same version
_vers = {}
for _d in ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6"):
    try:
        _vers[_d] = _md.version(_d)
    except _md.PackageNotFoundError:
        pass
if len(set(_vers.values())) > 1:
    raise SystemExit(f"\n*** PySide6 packages have different versions: {_vers}\n"
                     f"*** Fix: pip install --force-reinstall PySide6=={_vers.get('PySide6', '')}\n")
print(f"Qt check OK — PySide6 {_vers}, Qt binaries from: {', '.join(_roots)}")

# Drop Qt libraries only pulled in by the on-screen virtual-keyboard plugin (QML / Quick / PDF).
_DROP = ("Qt6Quick", "Qt6Qml", "Qt6Pdf", "Qt6VirtualKeyboard", "qtvirtualkeyboard",
         "Qt6WaylandEgl", "virtualkeyboard")
a.binaries = [b for b in a.binaries if not any(d in b[0] for d in _DROP)]
a.datas = [d for d in a.datas if not any(x in d[0] for x in ("qml/", "qml\\"))]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PointCloudStudio",
    console=False,                       # GUI app: no console window
    icon="ui/assets/app.ico",
    upx=False,
)
# Same program with a console window: shows start-up messages and errors (troubleshooting).
exe_debug = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PointCloudStudio_debug",
    console=True,
    icon="ui/assets/app.ico",
    upx=False,
)
coll = COLLECT(exe, exe_debug, a.binaries, a.datas, strip=False, upx=False, name="PointCloudStudio")
