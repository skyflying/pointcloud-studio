#!/usr/bin/env bash
# Shared macOS preflight for setup_mac.sh and build_mac.sh (sourced, not run directly).
# Finds — or installs — everything that has to exist before pip can do its work:
#   macOS 11+, network access to PyPI, a suitable Python 3.10–3.13, Command Line Tools (only when
#   a package must be compiled), SSL certificates for python.org Python.
# Provides:  PYEXE  (path of the Python to use)

PY_ORG_VERSION="3.12.10"     # last python.org 3.12 release with a macOS installer
PY_ORG_PKG_URL="https://www.python.org/ftp/python/${PY_ORG_VERSION}/python-${PY_ORG_VERSION}-macos11.pkg"

say()  { printf '%s\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m*** %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }

ask_yes() {   # ask_yes "Question" → 0 for yes (Enter = yes). Without a terminal → no: never start
  local a      # installers needing a password or a GUI window when nobody can answer
  if [[ ! -t 0 ]]; then say "$1 → no (not running in a terminal)"; return 1; fi
  read -r -p "$1 [Y/n] " a
  [[ -z "$a" || "$a" =~ ^[Yy] ]]
}

check_macos() {
  local v major
  v="$(sw_vers -productVersion 2>/dev/null || echo 0)"
  major="${v%%.*}"
  say "macOS   : $v ($(uname -m))"
  if (( major < 11 )); then
    fail "macOS 11 (Big Sur) or later is needed (this Mac: $v)."
    return 1
  fi
  if [[ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" == "1" ]]; then
    warn "This Terminal runs under Rosetta (Intel emulation) on an Apple Silicon Mac."
    warn "Packages would be installed for Intel. Better: Terminal → Get Info → untick 'Open using Rosetta'."
  fi
}

check_network() {
  if ! curl -sSfI --max-time 15 https://pypi.org/simple/pip/ >/dev/null 2>&1; then
    fail "Cannot reach pypi.org — an internet connection is needed to download the Python packages."
    say  "    (Behind a company proxy? Set it first, e.g.  export HTTPS_PROXY=http://proxy:8080)"
    return 1
  fi
}

have_clt() { xcode-select -p >/dev/null 2>&1; }

ensure_clt() {   # Command Line Tools: only needed when a package has to be compiled from source
  have_clt && return 0
  warn "Apple Command Line Tools are not installed (needed only if a package must be compiled)."
  if ask_yes "Install them now? (a macOS window will open)"; then
    xcode-select --install >/dev/null 2>&1 || true
    say "Waiting for the Command Line Tools installation to finish (close the window when done)…"
    local i
    for ((i = 0; i < 180; i++)); do have_clt && break; sleep 10; done
    if have_clt; then ok "Command Line Tools installed."; else warn "Continuing without Command Line Tools."; fi
  fi
}

_py_usable() {   # _py_usable /path/to/python → prints version if 3.10–3.13, not conda
  local p="$1"
  [[ -x "$p" ]] || return 1
  # Apple's /usr/bin/python3 is a stub that pops up an installer when the CLT are missing
  if [[ "$p" == "/usr/bin/python3" ]] && ! have_clt; then return 1; fi
  "$p" - <<'PY' 2>/dev/null
import os, sys
ok = (3, 10) <= sys.version_info[:2] <= (3, 13)
conda = os.path.isdir(os.path.join(sys.base_prefix, "conda-meta"))
try:
    import venv  # noqa: F401
except Exception:
    ok = False
if ok and not conda:
    print("%d.%d.%d" % sys.version_info[:3])
else:
    sys.exit(1)
PY
}

find_python() {
  local c v
  local cands=()
  [[ -n "${PYTHON_EXE:-}" ]] && cands+=("$PYTHON_EXE")
  for v in 3.12 3.13 3.11 3.10; do cands+=("/Library/Frameworks/Python.framework/Versions/$v/bin/python3"); done
  for v in 3.12 3.13 3.11 3.10; do
    cands+=("/opt/homebrew/opt/python@$v/bin/python$v" "/usr/local/opt/python@$v/bin/python$v")
  done
  for v in python3.12 python3.13 python3.11 python3.10 python3; do
    c="$(command -v "$v" 2>/dev/null)" && cands+=("$c")
  done
  for c in "${cands[@]}"; do
    if v="$(_py_usable "$c")"; then PYEXE="$c"; say "Python  : $PYEXE ($v)"; return 0; fi
  done
  return 1
}

install_python_org() {
  warn "No suitable Python (3.10–3.13) found."
  say  "(Note: the python3 that comes with macOS is 3.9 — too old.)"
  if ! ask_yes "Download and install Python ${PY_ORG_VERSION} from python.org now? (asks for your Mac password)"; then
    fail "Install Python 3.12 from https://www.python.org/downloads/macos/ and run this script again."
    return 1
  fi
  local pkg
  pkg="$(mktemp -d)/python-${PY_ORG_VERSION}.pkg"
  say "Downloading $PY_ORG_PKG_URL …"
  curl -fL --progress-bar -o "$pkg" "$PY_ORG_PKG_URL" || { fail "Download failed."; return 1; }
  sudo installer -pkg "$pkg" -target / || { fail "Python installation failed."; return 1; }
  rm -f "$pkg"
  # python.org Python ships without SSL root certificates until this has run
  local certs="/Applications/Python ${PY_ORG_VERSION%.*}/Install Certificates.command"
  [[ -x "$certs" ]] && "$certs" >/dev/null 2>&1 || true
  find_python
}

fix_certificates() {   # python.org Python: "CERTIFICATE_VERIFY_FAILED" until certificates are installed
  if ! "$PYEXE" -c 'import ssl, urllib.request; urllib.request.urlopen("https://pypi.org/simple/pip/", timeout=15)' \
       >/dev/null 2>&1; then
    local mm certs
    mm="$("$PYEXE" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    certs="/Applications/Python ${mm}/Install Certificates.command"
    if [[ -x "$certs" ]]; then
      say "Installing SSL certificates for Python ${mm}…"
      "$certs" >/dev/null 2>&1 || true
    fi
  fi
}

# make_venv DIR → creates (or re-creates, if broken / made by another Python) and activates it
make_venv() {
  local dir="$1"
  if [[ -x "$dir/bin/python" ]] && "$dir/bin/python" -c 'import sys' >/dev/null 2>&1; then
    local want have
    want="$("$PYEXE" -c 'import sys; print(sys.version.split()[0])')"
    have="$("$dir/bin/python" -c 'import sys; print(sys.version.split()[0])')"
    [[ "$want" != "$have" ]] && { say "Re-creating $dir (Python $have → $want)"; rm -rf "$dir"; }
  else
    rm -rf "$dir"
  fi
  if [[ ! -d "$dir" ]]; then
    "$PYEXE" -m venv "$dir" || { fail "Could not create the virtual environment $dir"; return 1; }
  fi
  # shellcheck disable=SC1091
  source "$dir/bin/activate"
}

# An active Anaconda / Miniconda environment ("(base)" in the prompt) puts its own python3, pip
# and Qt first on PATH. Hide it for the rest of this script — the user's shell is not changed.
isolate_from_conda() {
  local dir newpath="" removed=0 IFS=:
  for dir in $PATH; do
    if [[ -x "$dir/conda" || -d "$dir/../conda-meta" || "$dir" == *conda*/bin || "$dir" == *conda*/condabin ]]; then
      removed=1
      continue
    fi
    newpath="${newpath:+$newpath:}$dir"
  done
  if (( removed )) || [[ -n "${CONDA_PREFIX:-}" ]]; then
    warn "Anaconda / Miniconda is active (${CONDA_DEFAULT_ENV:-conda}) — ignoring it for this script."
    say  "    (Its Python cannot be used: its Qt conflicts with PySide6. Your conda setup is not changed.)"
    export PATH="$newpath"
    unset CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PYTHON_EXE CONDA_SHLVL PYTHONHOME PYTHONPATH
  fi
}

# GDAL / PROJ data paths from conda, QGIS or Homebrew GDAL make rasterio read another PROJ's
# proj.db (EPSG codes then fail). The wheels bring their own data — ignore these for this script.
isolate_geo_env() {
  local v
  for v in PROJ_DATA PROJ_LIB GDAL_DATA GDAL_DRIVER_PATH GEOTIFF_CSV; do
    if [[ -n "${!v:-}" ]]; then
      warn "Ignoring $v=${!v} (data of another GDAL/PROJ installation)"
      unset "$v"
    fi
  done
}

# preflight → everything up to a usable PYEXE
mac_preflight() {
  isolate_from_conda
  isolate_geo_env
  check_macos || return 1
  check_network || return 1
  find_python || install_python_org || return 1
  fix_certificates
}

# install_deps [--build] → installs every missing module in the active venv. If something failed and
# the Command Line Tools are missing (needed to compile), offers them and tries once more.
# Return: 0 = all required modules present (optional ones may be missing), 1 = a required one missing
install_deps() {
  python core/deps.py install "$@"
  if ! have_clt && python core/deps.py check "$@" 2>/dev/null | grep -q "missing"; then
    ensure_clt
    if have_clt; then say "Retrying the missing modules…"; python core/deps.py install "$@"; fi
  fi
  say ""
  python core/deps.py check "$@"      # final summary; exit 1 if a required module is missing
}
