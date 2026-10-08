@echo off
REM ============================================================
REM  PointCloud Studio - Windows build (PyInstaller, one-folder)
REM  Needs Python 3.10 - 3.13, 64-bit.
REM  If Python is in an unusual place, set it first, e.g.:
REM     set PYTHON_EXE=C:\Tools\Python312\python.exe
REM     build_windows.bat
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "PYEXE="

REM 1) explicit override
if defined PYTHON_EXE if exist "%PYTHON_EXE%" set "PYEXE=%PYTHON_EXE%"

REM 2) python.org installs first (default folders, then the py launcher) - NOT Anaconda:
REM    Anaconda's Python adds anaconda3\Library\bin to the DLL search path, where its own Qt lives;
REM    that Qt then clashes with pip's PySide6 ("DLL load failed ... procedure could not be found").
for %%V in (312 313 311 310) do (
  if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" call :probe "%LocalAppData%\Programs\Python\Python%%V\python.exe"
  if not defined PYEXE if exist "%ProgramFiles%\Python%%V\python.exe" call :probe "%ProgramFiles%\Python%%V\python.exe"
  if not defined PYEXE if exist "C:\Python%%V\python.exe" call :probe "C:\Python%%V\python.exe"
)
if not defined PYEXE call :probe py -3.12
if not defined PYEXE call :probe py -3

REM 3) python on PATH (the Microsoft Store stub fails "import sys" and is skipped)
if not defined PYEXE call :probe python
if not defined PYEXE call :probe python3

if not defined PYEXE (
  echo.
  echo [ERROR] No usable Python found.
  echo   - Install Python 3.12 ^(64-bit^) from https://www.python.org/downloads/windows/
  echo     and tick "Add python.exe to PATH" in the installer, then run this file again.
  echo   - Or point to an existing python.exe, e.g.:
  echo       set PYTHON_EXE=C:\path\to\python.exe
  echo       build_windows.bat
  echo   - Anaconda users: run this file from the "Anaconda Prompt".
  pause & exit /b 1
)

REM Refuse Anaconda / Miniconda Python (see above) unless explicitly allowed
"%PYEXE%" -c "import os,sys; sys.exit(1 if os.path.isdir(os.path.join(sys.base_prefix,'conda-meta')) else 0)"
if errorlevel 1 if not defined ALLOW_CONDA (
  echo.
  echo [ERROR] Found Anaconda/Miniconda Python: %PYEXE%
  echo   Anaconda ships its own Qt and changes the DLL search path, which breaks PySide6
  echo   ^("DLL load failed while importing QtCore: The specified procedure could not be found"^).
  echo.
  echo   Please install the standard Python 3.12 64-bit from python.org ^(Anaconda can stay installed^):
  echo     https://www.python.org/downloads/windows/
  echo   In the installer you may leave "Add python.exe to PATH" unticked - this script finds it.
  echo   Then delete the .venv folder here and run build_windows.bat again.
  pause & exit /b 1
)

REM version / bitness check
"%PYEXE%" -c "import sys,struct; v=sys.version_info[:2]; ok=(3,10)<=v<=(3,13) and struct.calcsize('P')==8; print('Using Python %%d.%%d (%%d-bit): %%s' %% (v[0],v[1],struct.calcsize('P')*8,sys.executable)); sys.exit(0 if ok else 1)"
if errorlevel 1 (
  echo [ERROR] Python 3.10 - 3.13, 64-bit is required. Install Python 3.12 64-bit from python.org.
  pause & exit /b 1
)

REM Clean virtual environment = only the packages the app needs end up in the build.
REM A .venv made earlier from another Python (e.g. Anaconda) is discarded.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import os,sys; sys.exit(0 if os.path.normcase(sys.base_prefix)==os.path.normcase(os.path.dirname(r'%PYEXE%')) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo Existing .venv was created by a different Python - recreating it...
    rmdir /s /q .venv
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  "%PYEXE%" -m venv .venv || (echo [ERROR] Could not create .venv & pause & exit /b 1)
)
set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt pyinstaller || (echo [ERROR] pip install failed & pause & exit /b 1)

REM Isolate PATH for the build so PyInstaller cannot pick up Qt DLLs from other software
REM (Anaconda, GIS packages, Qt SDKs...). Mixed Qt DLLs cause:
REM   "DLL load failed while importing QtOpenGL: The specified procedure could not be found."
set "PYBASE="
"%VPY%" -c "import sys; print(sys.base_prefix)" > "%TEMP%\pcs_pybase.txt" 2>nul
set /p PYBASE=<"%TEMP%\pcs_pybase.txt"
del "%TEMP%\pcs_pybase.txt" >nul 2>nul
if exist "%PYBASE%\conda-meta" (
  echo [NOTE] Anaconda/Miniconda Python detected - PATH is left unchanged; the spec file
  echo        drops any Qt DLL that does not come from the pip PySide6 package.
) else (
  set "PATH=%~dp0.venv\Scripts;%PYBASE%;%SystemRoot%\system32;%SystemRoot%;%SystemRoot%\System32\Wbem"
  echo PATH isolated for the build.
)

REM Always start from an empty dist folder - never mix files from different builds
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
"%VPY%" -m PyInstaller PointCloudStudio.spec --noconfirm || (echo [ERROR] PyInstaller failed & pause & exit /b 1)

echo.
echo Running the self-test on the packaged app...
"dist\PointCloudStudio\PointCloudStudio.exe" --selftest
if errorlevel 1 (
  echo [WARNING] Self-test reported failures - see dist\PointCloudStudio\selftest_report.txt
) else (
  echo Self-test passed.
)
REM "build" only holds PyInstaller's intermediate files - its exe does NOT run
REM ("Failed to load Python DLL ... build\...\python3xx.dll"). Remove it to avoid confusion.
if exist build rmdir /s /q build

echo.
echo ============================================================
echo  Build ready:  dist\PointCloudStudio\PointCloudStudio.exe
echo  Run / share the WHOLE dist\PointCloudStudio folder ^(exe + _internal^).
echo  Troubleshooting: PointCloudStudio_debug.exe ^(same app with a console window^)
echo  Start-up log:    %LOCALAPPDATA%\PointCloudStudio\startup.log
echo ============================================================
start "" explorer "%~dp0dist\PointCloudStudio"
pause
exit /b 0

REM ------------------------------------------------------------
REM :probe <command...>  - if the command runs Python, store the real interpreter path in PYEXE
:probe
set "PROBE_OUT=%TEMP%\pcs_python_probe.txt"
if exist "%PROBE_OUT%" del "%PROBE_OUT%" >nul 2>nul
%* -c "import sys; print(sys.executable)" > "%PROBE_OUT%" 2>nul
if errorlevel 1 goto :eof
set /p PYEXE=<"%PROBE_OUT%"
del "%PROBE_OUT%" >nul 2>nul
if defined PYEXE if not exist "%PYEXE%" set "PYEXE="
goto :eof
