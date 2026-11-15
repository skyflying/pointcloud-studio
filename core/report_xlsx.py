"""Formatted Excel reports (openpyxl): one sheet per table, units in the headers, bold coloured header row,
frozen header, filters, column widths, number formats and highlighted rows. Without openpyxl the sheets are
written as CSV files next to the requested path.

A sheet = {"name", "columns": [(key, header, fmt)], "rows": [dict], "highlight": optional row → colour key}
fmt: "kp" 0.000 · "m" 0.00 · "m1" 0.0 · "pct" 0.0 · "int" 0 · "money" #,##0 · "text"."""
from __future__ import annotations

import csv
import os

import numpy as np

FORMATS = {"kp": "0.000", "m": "0.00", "m1": "0.0", "pct": "0.0", "int": "0", "money": "#,##0", "text": "@",
           "xy": "0.00", "year": "0"}
FILLS = {"bad": "FDE2E1", "warn": "FFF1D6", "good": "E3F4EA", "info": "E8EEF9"}
HEAD_FILL = "1F3B57"


def _val(v):
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return v


def write(path, sheets, title=None, subtitle=None):
    """Returns the files written (the .xlsx, or one CSV per sheet without openpyxl)."""
    base = os.path.splitext(path)[0]
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        files = []
        for sh in sheets:
            f = f"{base}_{sh['name'].replace(' ', '_').lower()}.csv"
            with open(f, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow([c[1] for c in sh["columns"]])
                for r in sh["rows"]:
                    w.writerow(["" if _val(r.get(k)) is None else _val(r.get(k)) for k, _h, _f in sh["columns"]])
            files.append(f)
        return files
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin", color="D5DAE1")
    for sh in sheets:
        ws = wb.create_sheet(sh["name"][:31])
        cols = sh["columns"]
        r0 = 1
        if title and sh is sheets[0]:
            ws.cell(1, 1, title).font = Font(bold=True, size=13, color=HEAD_FILL)
            if subtitle:
                ws.cell(2, 1, subtitle).font = Font(size=9, color="6B7280")
            r0 = 4
        for j, (_k, head, _f) in enumerate(cols, start=1):
            c = ws.cell(r0, j, head)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor=HEAD_FILL)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = Border(bottom=thin)
        ws.row_dimensions[r0].height = 30
        hl = sh.get("highlight")
        for i, row in enumerate(sh["rows"], start=r0 + 1):
            colour = hl(row) if hl else None
            for j, (k, _h, f) in enumerate(cols, start=1):
                c = ws.cell(i, j, _val(row.get(k)))
                if f in FORMATS and f != "text":
                    c.number_format = FORMATS[f]
                c.border = Border(bottom=thin)
                if colour:
                    c.fill = PatternFill("solid", fgColor=FILLS.get(colour, colour))
        if not sh["rows"]:
            ws.cell(r0 + 1, 1, sh.get("empty", "none")).font = Font(italic=True, color="6B7280")
        ws.freeze_panes = ws.cell(r0 + 1, 1)
        if sh["rows"] and len(cols) > 1:
            ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(cols))}{r0 + len(sh['rows'])}"
        for j, (k, head, f) in enumerate(cols, start=1):
            vals = [len(f"{_val(r.get(k)):.3f}") if isinstance(_val(r.get(k)), float) else len(str(_val(r.get(k)) or ""))
                    for r in sh["rows"][:200]]
            width = max([min(len(head), 18)] + vals) + 2
            ws.column_dimensions[get_column_letter(j)].width = min(max(width, 9), 48)
    wb.save(base + ".xlsx")
    return [base + ".xlsx"]


def settings_sheet(items, notes=()):
    rows = [{"item": k, "value": _val(v) if not isinstance(v, (dict,)) else str(v)} for k, v in items]
    rows += [{"item": "Note", "value": n} for n in notes]
    return {"name": "Settings", "columns": [("item", "Item", "text"), ("value", "Value", "text")], "rows": rows}
