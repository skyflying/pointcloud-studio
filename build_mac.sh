#!/usr/bin/env bash
# ============================================================
#  PointCloud Studio - macOS build (PyInstaller → .app → .dmg)
#  Needs Python 3.10 - 3.13 from python.org (not Anaconda).
#  The app is built for the architecture of that Python:
#    Apple Silicon Mac → arm64,  Intel Mac → x86_64.
#  Use another Python:   PYTHON_EXE=/path/to/python3 ./build_mac.sh
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

# ---- 1) find Python: explicit override, then python.org framework installs, then PATH
PYEXE="${PYTHON_EXE:-}"
if [[ -z "$PYEXE" ]]; then
  for v in 3.12 3.13 3.11 3.10; do
    f="/Library/Frameworks/Python.framework/Versions/$v/bin/python3"
    if [[ -x "$f" ]]; then PYEXE="$f"; break; fi
  done
fi
[[ -z "$PYEXE" ]] && PYEXE="$(command -v python3 || true)"
if [[ -z "$PYEXE" ]]; then
  echo "*** Python 3 not found. Install Python 3.12 from https://www.python.org/downloads/macos/"
  exit 1
fi
if "$PYEXE" -c 'import os,sys; sys.exit(0 if os.path.isdir(os.path.join(sys.base_prefix,"conda-meta")) else 1)'; then
  echo "*** $PYEXE is an Anaconda / Miniconda Python — not supported for building."
  echo "*** Install Python 3.12 from python.org, or set PYTHON_EXE to another Python."
  exit 1
fi
ARCH="$("$PYEXE" -c 'import platform; print(platform.machine())')"
VERSION="$("$PYEXE" -c 'import sys; sys.path.insert(0,"."); from core.version import __version__; print(__version__)')"
echo "Python : $PYEXE ($("$PYEXE" -V 2>&1), $ARCH)"
echo "Version: $VERSION"

# ---- 2) clean virtual environment
rm -rf .venv-build build dist
"$PYEXE" -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

# ---- 3) build the .app
pyinstaller PointCloudStudio.spec --noconfirm
APP="dist/PointCloudStudio.app"
[[ -d "$APP" ]] || { echo "*** Build failed: $APP not found"; exit 1; }

# ---- 4) self-test (report: ~/Library/Application Support/PointCloudStudio/selftest_report.txt)
echo "Running self-test…"
set +e
"$APP/Contents/MacOS/PointCloudStudio" --selftest --quiet
ST=$?
set -e
REPORT="$HOME/Library/Application Support/PointCloudStudio/selftest_report.txt"
[[ -f "$REPORT" ]] && cat "$REPORT"
if [[ $ST -ne 0 ]]; then echo "*** Self-test reported problems (exit $ST) — see above"; fi

# ---- 5) disk image with an Applications shortcut
DMG="dist/PointCloudStudio-${VERSION}-macos-${ARCH}.dmg"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "PointCloud Studio" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
deactivate
rm -rf build

echo
echo "Done:"
echo "  App: $APP"
echo "  DMG: $DMG"
echo "Run from Terminal to see start-up messages:  $APP/Contents/MacOS/PointCloudStudio"
