"""Open a vector file and, for multi-layer formats (GeoPackage, KML folders, DXF layers), choose a layer."""
from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QInputDialog

from fileio import vector_io


def pick_vector(parent, caption, extra_filter=""):
    """→ (path, layer or None) or (None, None) when cancelled. extra_filter: further formats first, e.g.
    'CSV / text (*.csv *.txt)'."""
    flt = vector_io.READ_FILTER
    if extra_filter:
        exts = vector_io.READ_FILTER.split(";;")[0].split("(", 1)[1].rstrip(")")
        tx = extra_filter.split("(", 1)[1].rstrip(")")
        flt = f"Supported files ({tx} {exts});;{extra_filter};;" + vector_io.READ_FILTER
    path, _ = QFileDialog.getOpenFileName(parent, caption, "", flt + ";;All files (*)")
    if not path:
        return None, None
    return path, pick_layer(parent, path)


def pick_layer(parent, path):
    """Layer of a multi-layer vector file (None = all / single-layer)."""
    if not vector_io.is_vector(path):
        return None
    names = vector_io.layers(path)
    if len(names) <= 1:
        return names[0] if names else None
    all_ok = not path.lower().endswith(".gpkg")            # KML folders / DXF layers can be read together
    items = (["(all)"] if all_ok else []) + names
    item, ok = QInputDialog.getItem(parent, "Choose a layer", f"{len(names)} layers in the file:", items, 0, False)
    if not ok:
        return None
    return None if item == "(all)" else item
