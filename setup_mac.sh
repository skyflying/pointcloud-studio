#!/usr/bin/env bash
# ============================================================
#  PointCloud Studio - macOS setup (run from source)
#  Checks the Mac, finds or installs Python, creates .venv, installs every missing module
#  (one by one; a failing optional module does not stop the rest), then creates a
#  double-clickable launcher "PointCloud Studio.command".
#  Run again at any time: only what is missing gets installed.
#  Start it in Terminal:   bash setup_mac.sh     (works even without the execute permission)
# ============================================================
cd "$(dirname "$0")"
source tools/mac_env.sh

say "== PointCloud Studio setup =="
mac_preflight || exit 1
make_venv .venv || exit 1

if ! install_deps; then
  fail "Required modules could not be installed — the program cannot start yet."
  say  "Send the messages above (or the output of:  .venv/bin/python core/deps.py check) for help."
  exit 1
fi

LAUNCHER="PointCloud Studio.command"
cat > "$LAUNCHER" <<'L'
#!/usr/bin/env bash
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then bash setup_mac.sh || exit 1; fi
exec .venv/bin/python main.py "$@"
L
chmod +x "$LAUNCHER"

ok "Setup complete."
say "Start the program: double-click \"$LAUNCHER\" in Finder, or run  ./\"$LAUNCHER\""
if ask_yes "Start PointCloud Studio now?"; then
  exec .venv/bin/python main.py
fi
