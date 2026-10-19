# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller PointCloudStudio.spec --noconfirm
# Output: Windows  dist/PointCloudStudio/PointCloudStudio.exe   (one-folder build: fast start-up)
#         macOS    dist/PointCloudStudio.app                   (build on a Mac: build_mac.sh)
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

# Optional packages (lazrs, pyproj, rasterio, pyshp, contourpy) may be missing on a build machine:
# collect them only if installed, so the build still succeeds (those features are then unavailable).
import importlib.util as _iu


def _have(pkg):
    try:
        return _iu.find_spec(pkg) is not None
    except Exception:
        return False


_missing_optional = [p for p in ("lazrs", "pyproj", "rasterio", "shapefile", "contourpy", "openpyxl", "cv2")
                     if not _have(p)]
if _missing_optional:
    print("*** NOTE: optional packages not installed, related features will be unavailable:",
          ", ".join(_missing_optional))


def _safe(fn, pkg):
    return fn(pkg) if _have(pkg) else []


# ---- macOS: wheels that bundle a library under the same file name (pyproj and rasterio both ship
# libproj.25.x.dylib; rasterio's copy has renamed symbols) would share one copy inside the app →
# "Symbol not found: _proj_context_create". Give them distinct names first (see tools/mac_fix_dylibs.py).
if sys.platform == "darwin":
    sys.path.insert(0, os.path.abspath("tools"))
    import mac_fix_dylibs
    mac_fix_dylibs.fix()

datas = [("ui/assets", "ui/assets")]
datas += collect_data_files("vispy")               # GLSL shaders, fonts, colormaps
datas += _safe(collect_data_files, "pyproj")       # proj.db (EPSG definitions)
datas += _safe(collect_data_files, "rasterio")     # bundled GDAL / PROJ data

binaries = _safe(collect_dynamic_libs, "lazrs")    # LAZ codec (Rust extension)
binaries += _safe(collect_dynamic_libs, "cv2")     # OpenCV (photogrammetry) — use opencv-python-HEADLESS:
                                                   # the GUI wheel ships its own Qt, which clashes with PySide6
datas += _safe(collect_data_files, "cv2")
try:
    import importlib.metadata as _md0
    if _md0.version("opencv-python"):
        print("*** WARNING: opencv-python (with its own Qt) is installed — uninstall it and keep "
              "opencv-python-headless, or the app may fail to start")
except Exception:
    pass
# Open3D (Poisson / ball-pivoting meshes) is large and needs many data files: only bundled on request
# (set INCLUDE_OPEN3D=1). Without it the frozen app offers the 2.5D Delaunay and 3D alpha-shape meshes.
_with_o3d = bool(os.environ.get("INCLUDE_OPEN3D")) and _have("open3d")
if _with_o3d:
    datas += collect_data_files("open3d")
    binaries += collect_dynamic_libs("open3d")

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
    _safe(collect_submodules, "openpyxl")                 # Excel export (pandas loads it on demand)
    + _safe(collect_submodules, "rasterio")
    + collect_submodules("vispy.app.backends")
    + collect_submodules("vispy.visuals")
    + ["shapefile",                 # pyshp (route import / contour export)
       "contourpy",
       "PySide6.QtTest",            # required by VisPy's PySide6 backend — do not exclude
       "lazrs", "laspy.compression", "pyproj.database", "scipy.spatial._qhull",
       "scipy.ndimage", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvg",
       "scipy.sparse.csgraph", "scipy.optimize._lsq"]
    + (["cv2"] if _have("cv2") else [])
    + (collect_submodules("open3d") if _with_o3d else [])
)

# Optional packages VisPy / pandas may pick up if they are installed — not used by the app.
excludes = ["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "IPython", "jupyter", "notebook", "pytest",
            "imageio", "imageio_ffmpeg", "skimage", "PIL", "sklearn", "numba", "llvmlite",
            "sympy", "networkx", "dask", "pyarrow", "tables", "h5py", "sqlalchemy",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
            "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
            "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtPdf", "PySide6.QtNetwork",
            "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtXml"]
if not _with_o3d:
    excludes += ["open3d", "dash", "plotly"]

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

if sys.platform == "darwin":
    # ---- macOS: one .app bundle. No separate debug executable — start-up messages appear when the
    # app is run from Terminal:  dist/PointCloudStudio.app/Contents/MacOS/PointCloudStudio
    sys.path.insert(0, os.path.abspath("."))
    from core.version import __version__ as _app_version

    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="PointCloudStudio",
        console=False,
        argv_emulation=False,            # files opened from Finder are handled via QFileOpenEvent
        target_arch=None,                # = architecture of the Python used (arm64 or x86_64)
        icon="ui/assets/app.icns",
        upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PointCloudStudio")
    _doc_types = [
        {"CFBundleTypeName": "Point cloud", "CFBundleTypeRole": "Editor", "LSHandlerRank": "Alternate",
         "CFBundleTypeExtensions": ["las", "laz", "xyz", "asc", "pts"]},
        {"CFBundleTypeName": "PointCloud Studio project", "CFBundleTypeRole": "Editor", "LSHandlerRank": "Owner",
         "CFBundleTypeExtensions": ["pcsproj"]},
    ]
    def _min_macos(binaries):
        """Highest minimum-macOS version among the bundled libraries (LC_BUILD_VERSION minos /
        LC_VERSION_MIN_MACOSX). Wheels built for newer macOS (e.g. numpy/scipy built for macOS 14)
        raise the app's real minimum; Info.plist must state it so older Macs get a clear message
        instead of a crash. Read with macholib (installed with PyInstaller on macOS) — `otool`
        would need the Xcode Command Line Tools."""
        try:
            from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX
            from macholib.MachO import MachO
        except Exception as e:  # noqa: BLE001
            print(f"*** NOTE: macholib unavailable ({e}) — minimum macOS left at 11.0")
            return "11.0"
        best, worst_lib = (11, 0), None
        for entry in list(binaries) + [(None, os.path.abspath(sys.executable), None)]:
            src = entry[1]
            if not src or not os.path.isfile(src):
                continue
            try:
                headers = MachO(src).headers
            except Exception:  # noqa: BLE001  (not a Mach-O file)
                continue
            for h in headers:
                for lc, cmd, _data in h.commands:
                    if lc.cmd == LC_BUILD_VERSION and getattr(cmd, "platform", 1) == 1:   # 1 = macOS
                        raw = cmd.minos
                    elif lc.cmd == LC_VERSION_MIN_MACOSX:
                        raw = cmd.version
                    else:
                        continue
                    v = (raw >> 16, (raw >> 8) & 0xFF)
                    if v > best:
                        best, worst_lib = v, os.path.basename(src)
        print(f"Minimum macOS for this build: {best[0]}.{best[1]}"
              + (f"  (set by {worst_lib})" if worst_lib else ""))
        return f"{best[0]}.{best[1]}"

    _min_os = _min_macos(a.binaries)
    app = BUNDLE(
        coll,
        name="PointCloudStudio.app",
        icon="ui/assets/app.icns",
        bundle_identifier="io.github.skyflying.pointcloudstudio",
        version=_app_version,
        info_plist={
            "CFBundleName": "PointCloud Studio",
            "CFBundleDisplayName": "PointCloud Studio",
            "CFBundleShortVersionString": _app_version,
            "CFBundleVersion": _app_version,
            "NSHighResolutionCapable": True,          # Retina rendering
            "NSRequiresAquaSystemAppearance": False,  # allow dark title bar
            "LSMinimumSystemVersion": _min_os,   # from the bundled libraries, see _min_macos
            "LSApplicationCategoryType": "public.app-category.productivity",
            "CFBundleDocumentTypes": _doc_types,
        },
    )
else:
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
