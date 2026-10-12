#!/usr/bin/env bash
# ============================================================
#  PointCloud Studio - macOS build (PyInstaller → .app → .dmg)
#  Finds or installs a suitable Python (3.10–3.13, python.org preferred, never Anaconda),
#  installs every missing module in a clean build environment (one by one, with retry),
#  then builds, self-tests and packs a disk image.
#  The app is built for the architecture of that Python: Apple Silicon → arm64, Intel → x86_64.
#  Start it in Terminal:  bash build_mac.sh
#  Use another Python:   PYTHON_EXE=/path/to/python3 bash build_mac.sh
# ============================================================
cd "$(dirname "$0")"
source tools/mac_env.sh

say "== PointCloud Studio macOS build =="
mac_preflight || exit 1
ARCH="$("$PYEXE" -c 'import platform; print(platform.machine())')"
VERSION="$("$PYEXE" -c 'import sys; sys.path.insert(0,"."); from core.version import __version__; print(__version__)')"
say "Version : $VERSION ($ARCH)"

# ---- clean build environment + modules
rm -rf .venv-build build dist
make_venv .venv-build || exit 1
if ! install_deps --build; then
  fail "Required modules are missing — cannot build. See the messages above."
  exit 1
fi

# ---- build the .app
say ""
say "Building the app with PyInstaller…"
if ! pyinstaller PointCloudStudio.spec --noconfirm; then
  fail "PyInstaller failed — see the messages above."
  exit 1
fi
APP="dist/PointCloudStudio.app"
[[ -d "$APP" ]] || { fail "Build failed: $APP not found"; exit 1; }

# ---- self-test (report: ~/Library/Application Support/PointCloudStudio/selftest_report.txt)
say "Running self-test…"
"$APP/Contents/MacOS/PointCloudStudio" --selftest --quiet
ST=$?
REPORT="$HOME/Library/Application Support/PointCloudStudio/selftest_report.txt"
[[ -f "$REPORT" ]] && cat "$REPORT"
if [[ $ST -ne 0 ]]; then
  fail "SELF-TEST FAILED (exit $ST) — the app may not work. Check the [FAIL] lines above before using"
  fail "or sharing it, and send them for help. (The app and DMG were still created.)"
fi

# ---- disk image with an Applications shortcut
DMG="dist/PointCloudStudio-${VERSION}-macos-${ARCH}.dmg"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
if hdiutil create -volname "PointCloud Studio" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null; then
  DMG_OK=1
else
  warn "Could not create the disk image (the .app is still usable)."
fi
rm -rf "$STAGE" build
deactivate

say ""
ok "Done:"
say "  App: $APP"
[[ -n "${DMG_OK:-}" ]] && say "  DMG: $DMG"
MINOS="$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$APP/Contents/Info.plist" 2>/dev/null)"
[[ -n "$MINOS" ]] && say "  Runs on macOS $MINOS or later ($ARCH)"
say "Run from Terminal to see start-up messages:  $APP/Contents/MacOS/PointCloudStudio"
