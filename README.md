# PointCloud Studio

A desktop application for viewing, cleaning, and analysing point cloud data, with a focus on hydrographic and survey workflows. PointCloud Studio lets you import point clouds from common survey formats, inspect and edit them interactively in 3D, generate TIN and DEM surfaces, and cut cross-sections along freely drawn lines.

<!-- Replace with a real screenshot: save it as docs/screenshot.png -->
![PointCloud Studio screenshot]([blob/screenshot.png](https://github.com/skyflying/pointcloud-studio/blob/main/blob/screenshot.png?raw=true))

---

## Features

**Import**
- ASCII XYZ / ASC files with a configurable start row (skip headers) and column mapping (choose which columns hold X, Y, Z and attributes)
- LAS and LAZ files

**Viewing and inspection**
- Interactive 3D view with free rotation, pan and zoom
- Point selection with on-screen display of point information (coordinates and attributes)

**Editing**
- Delete selected points into a trash space, so removed points are kept aside rather than lost and can be restored
- Export the cleaned point cloud back to the supported import formats

**Surface generation**
- TIN (triangulated irregular network) generation
- DEM gridding, including a shoalest-depth mode that keeps the true position of the shoalest sounding in each cell, as required for hydrographic products

**Cross-sections**
- Draw a section line freely on the map view and extract the profile of the point cloud or surface along it

**Interface**
- English UI with a dark, modern theme

---

## Supported formats

| Format | Import | Export |
|--------|:------:|:------:|
| XYZ / ASC (ASCII) | ✓ | ✓ |
| LAS | ✓ | ✓ |
| LAZ | ✓ | ✓ |

---

## Requirements

- Windows 10/11 (other platforms may work but are untested)
- Python 3.10 or later
- Python packages listed in `requirements.txt`

---

## Installation

Clone the repository and set up a virtual environment:

```bash
git clone https://github.com/<your-username>/pointcloud-studio.git
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

1. **File → Import** and choose an XYZ/ASC, LAS or LAZ file. For ASCII files, set the start row and map the columns to X, Y and Z.
2. Rotate and zoom the 3D view to inspect the data. Select points to see their information.
3. Delete outliers or unwanted points; they are moved to the trash space and can be restored if needed.
4. Generate a TIN or DEM. For hydrographic gridding, use the shoalest-depth option to retain the true position of the shoalest point in each cell.
5. Draw a cross-section line to inspect profiles.
6. **File → Export** to save the cleaned point cloud.

---

## Building a standalone executable (optional)

You can package the application as a Windows executable with PyInstaller:

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name "PointCloud Studio" main.py
```

The executable will be created in the `dist/` folder. Pre-built executables, when available, are published on the [Releases](../../releases) page rather than committed to the repository.

---

## Project structure

```
pointcloud-studio/
├── main.py              # Application entry point
├── requirements.txt     # Python dependencies
├── README.md
├── .gitignore
└── docs/
    └── screenshot.png
```

---

## Author

**Mingyi Hsu**
Email: smingyi.hsu@gmail.com

---

## License

<!-- Choose a licence and add a LICENSE file, e.g. MIT. Until then: -->
Copyright © 2026 Mingyi Hsu. All rights reserved.
