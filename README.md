# PointCloud Studio

A desktop application for viewing, cleaning, and analysing point cloud data from hydrographic (MBES) and land (LiDAR, survey) sources alike. PointCloud Studio lets you import point clouds and grids from common survey formats, inspect and edit them interactively in 3D, generate TIN and DEM surfaces, cut cross-sections along freely drawn lines or cable routes, compute volumes and earthworks designs, and export contours to GIS / CAD.

<!-- Replace with a real screenshot: save it as docs/screenshot.png -->
![PointCloud Studio screenshot](https://raw.githubusercontent.com/skyflying/pointcloud-studio/refs/heads/main/blob/screenshot.png)

**Current version: 0.1.12** · [Releases](../../releases) · [使用說明（繁體中文）](docs/USER_GUIDE.zh-TW.md)

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

- Windows 10/11 (other platforms may work but are untested)
- Python 3.10–3.13 from [python.org](https://www.python.org/downloads/windows/) — **not Anaconda** (Anaconda's own Qt conflicts with PySide6)
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

Pre-built executables are published on the [Releases](../../releases) page rather than committed to the repository: pushing a version tag (e.g. `v0.1.7`) makes GitHub Actions build the Windows app, run the self-test and attach `PointCloudStudio-windows.zip` to a release.

---

## Project structure

```
pointcloud-studio/
├── main.py                  # Application entry point (start-up log, --selftest, --diagnose)
├── requirements.txt         # Python dependencies
├── PointCloudStudio.spec    # PyInstaller build specification
├── build_windows.bat        # One-click Windows build
├── check_env.bat            # Environment / DLL diagnostics
├── core/                    # Data model and algorithms (point cloud, project, CRS, surfaces,
│                            #   filters, sections, routes, volumes, earthworks, contours, self-test)
├── fileio/                  # Readers / writers (XYZ, LAS/LAZ, grids, routes, surfaces, contours)
├── render/                  # 3D view (VisPy), colour maps, colour bar, picking, interaction
├── ui/                      # Main window and dialogs (PySide6)
├── tools/                   # Environment check, sample data generator
├── docs/
│   └── USER_GUIDE.zh-TW.md  # User guide (Traditional Chinese)
├── blob/
│   └── screenshot.png
├── .github/workflows/       # Windows build on GitHub Actions
├── README.md
└── .gitignore
```

---

## Version history

- **0.1.12** — Point classification (SMRF / PMF / CSF / grid minimum ground filters, vegetation and buildings); KP / Chainage label
- **0.1.11** — Selectable reject class (7 / 18 / custom); TIN / DEM from selected classes; bulking and shrinkage factors in earthworks design
- **0.1.10** — Data stored as depths (import option, flip, export as depth); Topo-bathy palette and 0 m line; neutral filter wording
- **0.1.9** — Earthworks design and volumes for land data: every level as elevation or depth; neutral cut / fill wording
- **0.1.8** — Drawing from any viewing angle (terrain-aware picking, draped lines); Drawings in the Data panel; contour labels in 3D; closable Navigation panel
- **0.1.7** — DXF contour depth labels; GitHub Actions Windows build
- **0.1.6** — Contours (Shapefile PolyLineZ, DXF 3D polylines)
- **0.1.5** — Projects and coordinate systems, Subset editor and Class 7, automatic filters, cable routes and KP, volumes and multi-epoch comparison, earthworks design (dredge / fill, cable trench), slope maps

---

## Author

**Mingyi Hsu**
Email: smingyi.hsu@gmail.com

---

## License

<!-- Choose a licence and add a LICENSE file, e.g. MIT. Until then: -->
Copyright © 2026 Mingyi Hsu. All rights reserved.
