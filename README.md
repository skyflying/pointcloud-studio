# PointCloud Studio

A desktop application for viewing, cleaning, and analysing point cloud data from hydrographic (MBES) and land (LiDAR, survey) sources alike. PointCloud Studio lets you import point clouds and grids from common survey formats, inspect and edit them interactively in 3D, generate TIN and DEM surfaces, cut cross-sections along freely drawn lines or cable routes, compute volumes and earthworks designs, and export contours to GIS / CAD.

<!-- Replace with a real screenshot: save it as docs/screenshot.png -->
![PointCloud Studio screenshot](https://raw.githubusercontent.com/skyflying/pointcloud-studio/refs/heads/main/blob/screenshot.png)

**Current version: 0.1.15** · [Releases](../../releases) · [使用說明（繁體中文）](docs/USER_GUIDE.zh-TW.md)

---

## Features

**Import**
- ASCII XYZ / ASC files with a configurable start row (skip headers) and column mapping (choose which columns hold X, Y, Z and attributes); automatic encoding detection (UTF-8 / Big5)
- LAS and LAZ files (all dimensions and VLRs preserved)
- DEM grids: GeoTIFF, ESRI ASCII Grid, regular XYZ grids
- Cable / pipeline routes: RPL-style CSV / TXT (Easting/Northing or Lat/Lon, KP, several cables per file) and Shapefiles
- Land and sea in one convention: Z is elevation (up +) internally; files that store depths (down +) are converted on import (or flipped later), and can be written back as depths

**Projects and coordinate systems**
- Several point clouds, DEMs, TINs and routes in one project, saved as a `.pcsproj` file (original data files are never modified)
- EPSG coordinate system per dataset (searchable picker, TWD97 / TWD67 / UTM presets); data in another system is reprojected automatically
- Editing is locked automatically when several point clouds are loaded; choose the cloud to edit explicitly

**Viewing and inspection**
- Interactive 3D view with free rotation, pan and zoom; colour by elevation / depth, intensity, RGB, classification
- Interactive colour bar with draggable display range; depth mode for bathymetry; **Topo-bathy** palette for land–sea data with the break fixed at 0 m
- Point selection (pick, box, lasso) with on-screen display of point information (coordinates and attributes)

**Editing**
- **Subset editor** (BASE-Editor style): drag an oriented box, edit the points in side / end views, page along the box axis or along a cable route, track reviewed areas
- **Reject**: LAS / LAZ points are flagged with a reject class — **7** (low noise), **18** (high noise) or a custom class — instead of deleted (or moved to the trash space); accept restores the original class
- Delete selected points into a trash space, so removed points are kept aside rather than lost and can be restored; full undo / redo
- **Automatic filters** (results become a selection for review, never deleted automatically): grid median deviation, reference-surface deviation, statistical outlier (SOR), radius outlier, depth / elevation range
- **Point classification**: ground filters **SMRF**, **PMF**, **CSF** (cloth simulation) and **grid minimum**, then vegetation by height above ground (3 / 4 / 5) and buildings by roof flatness (6); one undo step
- Export the cleaned point cloud back to the supported import formats (LAS ↔ XYZ conversion, EPSG embedding, optional exclusion of Class 7)

**Surface generation**
- TIN (triangulated irregular network) generation, optionally from selected classes only (e.g. ground, class 2)
- DEM gridding, including a shoalest-depth mode that keeps the true position of the shoalest sounding (SDTP) in each cell, as required for hydrographic products
- Contours from DEM / TIN with major (index) contours, smoothing and simplification; depth / elevation labels in 3D; highlighted 0 m line

**Cross-sections and routes**
- Draw a section line from any viewing angle (clicks land on the visible terrain) and extract the profile of the point cloud or surface along it
- Every drawn line or area (sections, polygons, subset boxes) is listed in the Data panel and can be shown or hidden
- Sections along imported cable routes, with the horizontal axis in **KP** (or **Chainage**) taken from the route file
- Multi-epoch profiles: every visible surface and point cloud drawn in its own colour

**Analysis**
- **Volume / difference** between two surfaces or against a base level, within the whole overlap, a route corridor, a subset box or a polygon (drawn or from a Shapefile); coverage check, Δ layer, CSV report, Δ GeoTIFF
- **Earthworks design** (marine and land): cut / fill to a design level — given as elevation or depth — with over-cut allowance and side slopes 1 : n; **trench** along a route (bottom width, depth below the surface, side slopes) with excavation volume per KP interval; bulking and shrinkage factors with cut / fill balance
- Point cloud to surface deviation; seabed **slope map** with threshold statistics

**Cable & Foundations**
- **Cable profiles**: routes imported with a top-of-cable column (elevation or depth, e.g. SBI / TSS / ROV data) and optionally the survey's reported DoB; Shapefile PolyLineZ values can be used as cable top. Drawn at their own level in 3D (orange) and as a cable-top line in the profile panel
- **Depth of burial**: DoB = seabed − cable top every *step* metres against a target burial; seabed from a DEM, TIN or point-cloud corridor; non-compliant segments (KP from / to, length, minimum DoB), KP-bin statistics and compliance percentage, comparison with the reported DoB
- **Free spans**: (A) cable profile + seabed, or (B) point cloud only — the cable crest is found in the corridor and the seabed under it interpolated from both sides; gap = cable bottom − seabed; spans listed with length and maximum gap, flagged against a reporting length
- **Rock berm as-built**: design from a template (crest width, height, side and end slopes on the pre-lay seabed) or an imported design surface; as-built − design thickness, under- / over-filled area and volume, coverage, design / placed volume per KP bin, crest error along the centre line, rock cover over the cable against a minimum; design and Δ layers; check cross-sections at a fixed interval
- **Structures** (wind turbines, substations, piles): name, position and foundation diameter from CSV / TXT (column mapping) or Shapefile points; drawn as circles with names
- **Survey dates** on point clouds and surfaces (Data panel → ⋯ → Survey date, or in the analysis tables): epochs are ordered by date and rates are given per year
- **Scour monitoring**: per structure and survey — reference level from a far-field ring (×D), a baseline surface or a fixed level; maximum scour depth with distance and bearing, mean depth, scour extent (area connected to the pile deeper than a threshold), scour area and volume, change and rate between surveys; radial profiles, depth map, depth layers and GeoTIFFs
- **Sand wave migration**: bedforms separated from the regional seabed; displacement per window by 2D surface matching (speed and direction, crest-normal for long-crested bedforms) or along the route profile; crest tracking; burial forecast for a cable profile (bedform pattern moved at the observed along-route rate) with years to exposure and exposure-risk segments — a linear extrapolation for planning
- Results: summary, KP chart, segment and KP-bin tables (double-click → go to the location), coloured segments in 3D, export to CSV (summary / segments / KP bins / stations), Shapefile (PolyLineZ segments) and PNG

**Interface**
- English UI with a dark, modern theme
- Start-up log, diagnostics (`--diagnose`) and a built-in self-test (`--selftest`)

---

## Supported formats

| Format | Import | Export |
|--------|:------:|:------:|
| XYZ / ASC (ASCII points) | ✓ | ✓ |
| LAS | ✓ | ✓ |
| LAZ | ✓ | ✓ |
| GeoTIFF / ESRI ASCII Grid / XYZ grid (DEM) | ✓ | ✓ |
| TIN: LandXML, DXF 3DFACE, OBJ, PLY | | ✓ |
| Routes: RPL CSV / TXT, Shapefile | ✓ | |
| Contours: Shapefile PolyLineZ, DXF 3D polylines with depth / elevation labels | | ✓ |
| Sections: CSV, PNG · Reports: CSV | | ✓ |

---

## Requirements

- Windows 10/11, or macOS on Apple Silicon or Intel: macOS 11 (Big Sur) or later to run from source; a built app needs the macOS version of the newest library inside it — `build_mac.sh` prints it and macOS shows it if the Mac is too old (builds made on a recent Mac typically need macOS 13–14; build on the older Mac to support it)
- Python 3.10–3.13 from python.org ([Windows](https://www.python.org/downloads/windows/) · [macOS](https://www.python.org/downloads/macos/)) — **not Anaconda** (Anaconda's own Qt conflicts with PySide6)
- Python packages listed in `requirements.txt`

---

## Installation

Clone the repository and set up a virtual environment:

```bash
git clone https://github.com/skyflying/pointcloud-studio.git
cd pointcloud-studio

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

**macOS — one step:** run `bash setup_mac.sh` in Terminal instead. It checks the Mac, finds a suitable Python 3.10–3.13 (or offers to install Python 3.12 from python.org — the `python3` that comes with macOS is 3.9 and too old), creates `.venv`, and installs every missing module one by one: pre-built packages first, compiling only when there is no pre-built one (offering Apple's Command Line Tools if that is needed). A failing optional module does not stop the rest. It then creates **`PointCloud Studio.command`**, which starts the program with a double-click. Run it again at any time — only what is missing is installed.

Check or repair the modules on any platform with `python core/deps.py check` / `python core/deps.py install`. When the program is started from source and modules are missing, it lists them and offers to install them (then restarts); a feature that needs a missing optional module (e.g. GeoTIFF export without `rasterio`) offers the same.

---

## Usage

Start the application:

```bash
python main.py
```

A typical workflow:

1. **File → Open** (new project) or **File → Add Data** and choose an XYZ/ASC, LAS or LAZ file or a DEM grid. For ASCII files, set the start row and map the columns to X, Y and Z. Confirm the coordinate system (EPSG) when asked.
2. Rotate and zoom the 3D view to inspect the data. Select points to see their information.
3. Clean the data: use the **Subset editor** (U) to review side views page by page, and **Edit → Auto Filter** (Ctrl+F) to find spikes. Reject outliers (a reject class for LAS, or the trash space); everything can be undone or restored. For LiDAR, **Edit → Classify Points** (Ctrl+K) separates ground, vegetation and buildings.
4. Generate a TIN or DEM (optionally from ground points only). For hydrographic gridding, use the shoalest-depth option to retain the true position of the shoalest point in each cell.
5. Draw a cross-section line, or **File → Import Route** and use *Section along route* to inspect profiles in KP.
6. Use the **Analysis** menu for volumes, earthworks design, slope maps and contours.
7. **File → Save Project** (Ctrl+S) to keep the session, and **File → Export** to save the cleaned point cloud.

Press **F1** in the application for all keyboard shortcuts.

---

## Building a standalone executable (optional)

On Windows, run **`build_windows.bat`** in the project folder. It finds a python.org Python, creates a clean virtual environment, installs the dependencies, builds with PyInstaller (`PointCloudStudio.spec`) and runs the self-test. The result is the folder `dist\PointCloudStudio\` — distribute the whole folder (`PointCloudStudio.exe` + `_internal`). `PointCloudStudio_debug.exe` is the same program with a console window for troubleshooting.

If the program does not start, see `%LOCALAPPDATA%\PointCloudStudio\startup.log`, or run **`check_env.bat`** for a report of the Python / Qt / DLL environment.

On macOS, run **`bash build_mac.sh`** in Terminal in the project folder. It runs the same checks as `setup_mac.sh` (Python, network, certificates, missing modules with retry; an active Anaconda / Miniconda environment is ignored automatically), creates a clean virtual environment, builds `dist/PointCloudStudio.app`, runs the self-test and packs `dist/PointCloudStudio-<version>-macos-<arch>.dmg`. The app is built for the architecture of the Python used (arm64 on Apple Silicon, x86_64 on Intel). Start-up log and reports: `~/Library/Application Support/PointCloudStudio/`. To see start-up messages, run `PointCloudStudio.app/Contents/MacOS/PointCloudStudio` from Terminal.

**macOS: first start.** The app is not signed with an Apple Developer ID, so macOS blocks it at first ("cannot be opened" / "is damaged"). Either right-click the app → **Open** → **Open**, or remove the download quarantine once:

```bash
xattr -cr /Applications/PointCloudStudio.app
```

On a Mac, shortcuts use ⌘ (Command) where Windows uses Ctrl, and the *delete* key rejects the selection. Trackpad: two-finger scroll or pinch to zoom, Shift + drag to pan.

Pre-built executables are published on the [Releases](../../releases) page rather than committed to the repository: pushing a version tag (e.g. `v0.1.13`) makes GitHub Actions build the Windows and macOS apps, run the self-test and attach `PointCloudStudio-windows.zip` and the macOS disk images (`…-macos-arm64.dmg` for Apple Silicon, `…-macos-x86_64.dmg` for Intel) to a release.

---

## Project structure

```
pointcloud-studio/
├── main.py                  # Application entry point (start-up log, --selftest, --diagnose)
├── requirements.txt         # Python dependencies
├── PointCloudStudio.spec    # PyInstaller build specification
├── build_windows.bat        # One-click Windows build
├── build_mac.sh             # One-click macOS build (.app + .dmg)
├── setup_mac.sh             # macOS: set up and run from source (installs missing modules)
├── check_env.bat            # Environment / DLL diagnostics
├── core/                    # Data model and algorithms (point cloud, project, CRS, surfaces,
│                            #   filters, sections, routes, volumes, earthworks, contours, cable analyses,
│                            #   self-test)
├── fileio/                  # Readers / writers (XYZ, LAS/LAZ, grids, routes, surfaces, contours)
├── render/                  # 3D view (VisPy), colour maps, colour bar, picking, interaction
├── ui/                      # Main window and dialogs (PySide6)
├── tools/                   # Environment check, sample data generator, macOS build helpers
├── docs/
│   └── USER_GUIDE.zh-TW.md  # User guide (Traditional Chinese)
├── blob/
│   └── screenshot.png
├── .github/workflows/       # Windows and macOS builds on GitHub Actions
├── README.md
└── .gitignore
```

---

## Version history

### Releases

#### 0.1.15
- **Added** — Structures (CSV / Shapefile import with name and diameter), survey dates on datasets, **scour monitoring** (far-field / baseline / level reference, maximum depth, extent, area, volume, rates, radial profiles, depth maps and GeoTIFFs) and **sand wave migration** (2D matching or profile, crest tracking, burial forecast with years to exposure). CSV / Shapefile / PNG export, 3D overlays.
- **Fixed** — A project whose first data were surfaces only (e.g. opening a GeoTIFF without a point cloud) failed to colour the surface.
- **Added** — Self-test: scour hole and oblique sand waves with known depths, rates and direction.
- **Fixed** (review of 0.1.14–0.1.15) — KP ranges outside the route are refused with a message instead of analysing a zero-length piece; the DoB / free-span KP range follows the chosen route; the rock-berm pre-lay choice "(none)" is no longer reset; scour results keep only the analysis radius (≈ 15× less memory for large wind farms); scour extent is found when the survey has no data right at the pile wall (gap up to 0.25 D); TIN surveys are rasterised per window (scour, 2D sand-wave matching) instead of as a whole; the sand-wave forecast explains why it was skipped (no cable Z, cable > 10 m from the route, no dates, no reliable window); a second analysis while one is running, or one using a removed dataset, gives a message; invalid survey dates count as "no date"; structures in latitude / longitude ask for a projected project system; projects with structures are marked format version 2 so older versions report it clearly; zoom to a route or structure layer works without Z.

#### 0.1.14
- **Added** — *Cable & Foundations* menu: depth of burial, free spans (cable profile + seabed, or point cloud only) and rock berm as-built verification (template or design surface), with KP charts, segment / KP-bin tables, coloured 3D segments, and CSV / Shapefile / PNG export. Rock berm: design and as-built − design layers, check cross-sections.
- **Added** — Cable profiles: route import maps *Cable top (elevation)*, *Cable top (depth, + down)* and *DoB (reported)* columns; Shapefile PolyLineZ values can be used as cable top. Saved in the project; drawn at their own level in 3D and in the profile panel.
- **Added** — Self-test: synthetic cable with known DoB, span lengths / gaps and berm volumes.

#### 0.1.13
- **Added** — macOS version (Apple Silicon and Intel; the minimum macOS of a build is taken from its libraries): `build_mac.sh` builds `PointCloudStudio.app` and a `.dmg`; GitHub Actions builds both architectures on every version tag.
- **Added** — macOS integration: Retina rendering, files opened from Finder (*Open With*, double-click a `.pcsproj`, drop on the Dock icon), shortcut labels shown with ⌘ / ⌥, system font, trackpad pinch zoom.
- **Added** — Missing-module handling: `setup_mac.sh` and `build_mac.sh` find or install Python, check network and SSL certificates, and install every missing module one by one (pre-built first, compile only as fallback, Command Line Tools offered when needed) instead of stopping at the first error; `core/deps.py check | install` on every platform; the program (from source) lists missing modules at start-up and offers to install them, and features needing a missing optional module offer the same. Builds succeed without the optional modules (those features are then unavailable).
- **Added** — Missing optional modules no longer look like crashes: load / project / feature errors caused by a missing module (including LAZ without `lazrs`) open the install dialog; the self-test marks those steps SKIP instead of FAIL.
- **Fixed** — macOS build: pyproj and rasterio both bundle `libproj.25.x.dylib` under the same name, and the app loaded rasterio's copy for pyproj ("Symbol not found: _proj_context_create"). Same-named libraries of different packages are now renamed before PyInstaller runs (`tools/mac_fix_dylibs.py`, automatic). A failed self-test is reported prominently at the end of `build_mac.sh`.
- **Fixed** — GeoTIFF CRS lost (EPSG read back as none) when the shell had `PROJ_DATA` / `PROJ_LIB` / `GDAL_DATA` from another installation (an active Anaconda environment, QGIS, Homebrew GDAL): rasterio then used that installation's `proj.db`. The program and the build scripts now ignore these variables and use the data bundled with pyproj and rasterio (`PCS_KEEP_GEO_ENV=1` keeps them).
- **Changed** — On macOS, the start-up log and self-test / diagnostic reports are written to `~/Library/Application Support/PointCloudStudio/` (never inside the app bundle). The software-OpenGL fallback applies to Windows only.

#### 0.1.12
- **Added** — Point classification (*Edit → Classify Points*, Ctrl+K): four ground filters — SMRF (Pingel 2013), PMF (Zhang 2003), CSF cloth simulation (Zhang 2016), grid minimum — followed by vegetation by height above ground (classes 3 / 4 / 5) and buildings by roof flatness, including walls (class 6). Scope (all / selection / subset), "only unclassified points", one undo step for the whole run; works for clouds without a classification field.
- **Added** — *View → Distance Label*: KP (cables, offshore pipelines) or Chainage (roads, land pipelines) for sections, cursor, route labels, subset status and trench tables.
- **Added** — Self-test check for land-data classification.
- **Changed** — Grid-minimum ground filter allows for the local slope (steep terrain is no longer removed).

#### 0.1.11
- **Added** — Selectable reject class: 7 (low noise), 18 (high noise) or a custom class; all of them are treated as rejected (hidden, shown on request, *Accept* restores the original class). Classes above 31 are refused for LAS point formats 0–5.
- **Added** — *Create TIN / DEM* from selected classes only (e.g. ground, class 2); available classes and counts are listed.
- **Added** — Bulking and shrinkage factors in earthworks design: loose cut volume, material needed for the fill, cut / fill balance (also in the CSV report).

#### 0.1.10
- **Added** — Files that store depths (down +): XYZ import option "Z values are: Depth", *Data → Z values are depths (flip sign)* for any cloud or grid, text export "Write Z as depth"; saved in the project.
- **Added** — Topo-bathy palette for land–sea data (blues below 0 m, greens / browns above, sharp break at 0 m), chosen automatically when the data spans 0 m; highlighted 0 m contour (DXF layer `CONTOUR_ZERO`, SHP type `zero`).
- **Changed** — Filter wording for land and sea ("upper / lower side", MBES and LiDAR).

#### 0.1.9
- **Changed** — Earthworks design and volume base levels work for land data: every level field can be given as elevation (Z, up) or depth (down +), independent of the display mode, with automatic conversion and the existing surface's range as a hint.
- **Changed** — Neutral wording: *Cut / fill* (dredge, excavate, level, fill / embank), *Over-cut*, *Trench* (seabed or ground).

#### 0.1.8
- **Added** — *Drawings* in the Data panel: every section, the volume / dredge polygon, the subset box and reviewed areas can be shown or hidden; the polygon is saved in the project.
- **Added** — Depth / elevation labels on contours in the 3D view.
- **Added** — The *Navigation* help panel can be closed (✕ or *View → Navigation Help*).
- **Changed** — Section mode no longer switches to top view. Clicks are traced onto the visible terrain, so drawing is accurate from any viewing angle, and drawn lines follow the terrain.

#### 0.1.7
- **Added** — Depth / elevation labels in DXF contour exports (layer `CONTOUR_LABEL`, placed at the contour elevation, rotated along the line and kept upright).
- **Added** — GitHub Actions workflow: Windows build, self-test and release zip on every version tag.

#### 0.1.6
- **Added** — Contours from DEM / TIN (interval, base, major contours, smoothing, simplification, minimum length); export to Shapefile PolyLineZ (ELEV / DEPTH / TYPE) and DXF R12 3D polylines.

#### 0.1.5 — first numbered release
- **Added** — Earthworks design: dredge / fill to a design level with over-dredge allowance and side slopes 1 : n; cable trench along a route (bottom width, depth of lowering, side slopes) with excavation per KP interval; design and cut / fill layers, CSV report, GeoTIFF.
- **Added** — Slope map with statistics (mean, 95 %, maximum, area above thresholds); local slope at the profile cursor.
- **Changed** — Version numbering restarted at 0.1.5 (earlier internal builds were numbered 1.0.0–1.5.0, see below).

### Development builds before 0.1.5
These internal builds used the numbers 1.0.0–1.5.0 before the public version line was started at 0.1.5.

- **1.5.0** — Volume / difference between surfaces or against a base level (whole overlap, route corridor, subset box, polygon); coverage check and warning for data gaps; Δ layer, CSV report, Δ GeoTIFF; point cloud to surface deviation; multi-epoch section profiles. *Fixed:* projects with a Δ layer could not be saved; overlapping text in the volume results.
- **1.4.0** — Cable routes from RPL CSV / TXT and Shapefiles (EPSG required at import, automatic column detection, KP in km or m); sections with the KP from the route file; subsets paged along a route; subset length control. *Fixed:* the section profile and subset editor no longer force a window wider than the screen (they share the bottom area as tabs; both fit 1366 × 768).
- **1.3.0** — Automatic noise filters (grid median, reference surface, SOR, radius, range) with preview; results become a selection for review.
- **1.2.0** — Subset editor in the style of BASE Editor (oriented box, side / end views, paging, reviewed-area tracking); reject as LAS class 7 with *Accept*; export option to exclude rejected points; compact toolbar.
- **1.1.0** — Projects: several point clouds, DEMs and TINs; EPSG coordinate systems with automatic reprojection; DEM import (GeoTIFF, ESRI ASCII, XYZ grid); Data panel; automatic edit lock with several clouds; project files (`.pcsproj`). *Fixed:* LAS files with empty RGB fields were drawn black; long file names widened the side panel.
- **1.0.9** — Build script also finds Python installed by the Python Install Manager.
- **1.0.8** — Build refuses Anaconda / Miniconda Python (the cause of "DLL load failed while importing QtCore"), prefers python.org Python, and no longer bundles outdated Windows runtime files.
- **1.0.7** — `check_env.bat`: environment report comparing Qt from source and from the packaged program, with all runtime DLL versions.
- **1.0.6** — Clear error message when Qt cannot be loaded; runtime DLL versions in the start-up log; Microsoft C++ runtime taken from PySide6.
- **1.0.5** — Splash screen, start-up log with crash capture, automatic software-OpenGL fallback after a failed start, `PointCloudStudio_debug.exe` with a console window.
- **1.0.4** — The build script removes the intermediate `build` folder and opens `dist` (running the program from `build` failed with "Failed to load Python DLL").
- **1.0.3** — Packaging guards against mismatched Qt libraries; start-up diagnostics (`--diagnose`) and a readable error report instead of a crash.
- **1.0.2** — Colour bar redesign: fixed scale with draggable display-range handles; the build script isolates PATH.
- **1.0.1** — Simplified About dialog; more robust detection of Python in the build script.
- **1.0.0** — First integrated version: XYZ / ASC / LAS / LAZ import with column mapping and encoding detection; 3D view with colour modes; pick / box / lasso selection with point information; delete to trash with restore and undo / redo; export to LAS / LAZ / text with EPSG; interactive colour bar; TIN and DEM including the shoalest-depth (SDTP) method; cross-section profiles with measurement; packaging and self-test.

---

## Author

**Mingyi Hsu**
Email: smingyi.hsu@gmail.com

---

## License

<!-- Choose a licence and add a LICENSE file, e.g. MIT. Until then: -->
Copyright © 2026 Mingyi Hsu. All rights reserved.
