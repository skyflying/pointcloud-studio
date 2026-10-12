"""macOS build fix: give same-named bundled libraries in different wheels distinct names.

Why: pyproj and rasterio each ship their own PROJ as `.dylibs/libproj.25.<ver>.dylib` — the same
file name, but rasterio's copy has its symbols renamed (`internal_proj_context_create`). PyInstaller
places both under one @rpath, so pyproj ends up loading rasterio's copy and the app fails with
    Symbol not found: _proj_context_create  (… rasterio/__dot__dylibs/libproj…)
The same can happen to libtiff, libjpeg, liblzma, libzstd, … shared by several wheels.

What: for every library name that occurs in the `.dylibs` folders of more than one package, the
copies of all packages except the first are renamed (the leading "lib" is replaced by a 3-letter
package tag, so the name keeps its length and no Mach-O load command has to grow), every reference
inside that package is rewritten, and the changed files are re-signed ad hoc (required on Apple
Silicon). Pure Python (macholib, installed with PyInstaller) + the system `codesign` tool —
no Xcode Command Line Tools needed. Safe to run more than once.

Run inside the build environment before PyInstaller (the spec does this automatically):
    python tools/mac_fix_dylibs.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import sysconfig

KEEP_FIRST = ("rasterio",)       # this package keeps its names; the others are renamed


def _dylib_dirs(site):
    out = {}
    for pkg in sorted(os.listdir(site)):
        for sub in (".dylibs", "__dot__dylibs"):
            d = os.path.join(site, pkg, sub)
            if os.path.isdir(d):
                out[pkg] = d
    return out


def _collisions(dirs):
    by_name = {}
    for pkg, d in dirs.items():
        for n in os.listdir(d):
            if n.endswith(".dylib"):
                by_name.setdefault(n, []).append(pkg)
    return {n: pkgs for n, pkgs in by_name.items() if len(pkgs) > 1}


def _tag(pkg):
    t = "".join(c for c in pkg.lower() if c.isalnum())[:3]
    return (t + "xxx")[:3]


def _macho_files(root):
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            if fn.endswith((".so", ".dylib")):
                yield os.path.join(dp, fn)


def _rewrite(path, mapping, log):
    """Replace referenced library basenames (old → new, same length) in one Mach-O file."""
    from macholib.mach_o import LC_ID_DYLIB
    from macholib.MachO import MachO
    try:
        m = MachO(path)
    except Exception:  # noqa: BLE001 — not a Mach-O file
        return False

    def change(ref):
        d, b = os.path.split(ref)
        if b in mapping:
            return os.path.join(d, mapping[b]) if d else mapping[b]
        return None

    changed = False
    for h in m.headers:
        if h.rewriteLoadCommands(change):
            changed = True
        for lc, cmd, data in h.commands:          # the library's own install name (LC_ID_DYLIB)
            if lc.cmd == LC_ID_DYLIB:
                old = data.rstrip(b"\0").decode()
                new = change(old)
                if new and h.rewriteInstallNameCommand(new.encode()):
                    changed = True
    if changed:
        with open(path, "rb+") as f:
            m.write(f)
        log(f"    rewrote {os.path.relpath(path)}")
    return changed


def _codesign(path, log):
    r = subprocess.run(["codesign", "--force", "--sign", "-", path], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"    *** codesign failed for {path}: {r.stderr.strip()}")


def fix(site=None, log=print):
    if sys.platform != "darwin" and site is None:
        return 0
    site = site or sysconfig.get_paths()["platlib"]
    dirs = _dylib_dirs(site)
    coll = _collisions(dirs)
    if not coll:
        log("Bundled libraries: no name collisions between packages.")
        return 0
    # which package renames which names
    plan = {}
    for name, pkgs in coll.items():
        keep = next((p for p in KEEP_FIRST if p in pkgs), pkgs[0])
        for p in pkgs:
            if p != keep:
                plan.setdefault(p, {})[name] = _tag(p) + name[3:] if name.startswith("lib") else _tag(p) + name
    n = 0
    for pkg, mapping in plan.items():
        log(f"Bundled libraries of '{pkg}' renamed to avoid clashes: "
            + ", ".join(f"{a} → {b}" for a, b in sorted(mapping.items())))
        d = dirs[pkg]
        for old, new in mapping.items():
            src, dst = os.path.join(d, old), os.path.join(d, new)
            if os.path.exists(src) and not os.path.exists(dst):
                os.rename(src, dst)
        for f in _macho_files(os.path.join(site, pkg)):
            if _rewrite(f, mapping, log):
                n += 1
                if sys.platform == "darwin":
                    _codesign(f, log)
    return n


if __name__ == "__main__":
    fix()
