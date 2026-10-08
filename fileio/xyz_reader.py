"""XYZ / ASC text point cloud reader: preview, delimiter & start-row detection, column mapping."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from core.pointcloud import PointCloud, XYZ_ROLES

# key: (display label, pandas sep)
DELIMITERS = {
    "whitespace": ("Whitespace", r"\s+"),
    "tab": ("Tab", "\t"),
    "comma": ("Comma  ,", ","),
    "semicolon": ("Semicolon  ;", ";"),
}
ROLE_CUSTOM = "Custom"
ROLE_IGNORE = "Ignore"
ROLES = ["X", "Y", "Z", "Intensity", "R", "G", "B", "Classification", ROLE_CUSTOM, ROLE_IGNORE]
UNIQUE_ROLES = {"X", "Y", "Z", "Intensity", "R", "G", "B", "Classification"}


def detect_encoding(path, nbytes=65536):
    with open(path, "rb") as f:
        raw = f.read(nbytes)
    for enc in ("utf-8-sig", "cp950"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError as e:
            if e.start >= len(raw) - 4:  # truncated in the middle of a multi-byte char
                return enc
    return "latin-1"


def read_preview(path, n_lines=200):
    enc = detect_encoding(path)
    lines = []
    with open(path, "r", encoding=enc, errors="replace") as f:
        for i, line in enumerate(f):
            if i >= n_lines:
                break
            lines.append(line.rstrip("\r\n"))
    return lines, enc


def split_line(line, delim_key):
    line = line.strip()
    if not line:
        return []
    if delim_key == "whitespace":
        return line.split()
    return [t.strip() for t in line.split(DELIMITERS[delim_key][1])]


def _is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_numeric_row(tokens):
    return len(tokens) >= 3 and all(_is_number(t) for t in tokens)


def detect_delimiter(lines):
    sample = [l for l in lines if l.strip()][-30:]
    best, best_score = "whitespace", 0
    for key in ("comma", "tab", "semicolon", "whitespace"):
        score = sum(_is_numeric_row(split_line(l, key)) for l in sample)
        if score > best_score:
            best, best_score = key, score
    return best


def detect_start_row(lines, delim_key):
    """0-based first data row: 3 consecutive numeric rows with equal column count."""
    parsed = [split_line(l, delim_key) for l in lines]
    ok = [_is_numeric_row(r) for r in parsed]
    for i in range(len(lines)):
        if not ok[i]:
            continue
        window = range(i, min(i + 3, len(lines)))
        if all(ok[j] and len(parsed[j]) == len(parsed[i]) for j in window):
            return i
    return 0


def _looks_like_color(rows, col):
    vals = []
    for r in rows:
        if col < len(r) and _is_number(r[col]):
            vals.append(float(r[col]))
    return bool(vals) and all(v.is_integer() and 0 <= v <= 65535 for v in vals)


def guess_roles(rows):
    ncols = max((len(r) for r in rows), default=0)
    if ncols < 3:
        return [ROLE_CUSTOM] * ncols
    extra = ncols - 3
    if extra == 0:
        tail = []
    elif extra == 1:
        tail = ["Intensity"]
    elif extra == 3 and all(_looks_like_color(rows, c) for c in (3, 4, 5)):
        tail = ["R", "G", "B"]
    elif extra == 4 and all(_looks_like_color(rows, c) for c in (4, 5, 6)):
        tail = ["Intensity", "R", "G", "B"]
    else:
        tail = [ROLE_CUSTOM] * extra
    return ["X", "Y", "Z"] + tail


def validate_roles(roles):
    """Return an error message, or "" if valid."""
    for r in XYZ_ROLES:
        c = roles.count(r)
        if c == 0:
            return f"No column assigned to {r}"
        if c > 1:
            return f"{r} is assigned {c} times"
    for r in UNIQUE_ROLES - set(XYZ_ROLES):
        if roles.count(r) > 1:
            return f"{r} is assigned {roles.count(r)} times"
    return ""


def read_xyz(path, skip_rows, delim_key, column_roles, encoding=None):
    err = validate_roles(column_roles)
    if err:
        raise ValueError(err)
    use = {i: r for i, r in enumerate(column_roles) if r != ROLE_IGNORE}
    kwargs = dict(
        sep=DELIMITERS[delim_key][1], header=None, skiprows=skip_rows,
        usecols=sorted(use), encoding=encoding, engine="c",
        on_bad_lines="skip", skip_blank_lines=True,
    )
    try:
        df = pd.read_csv(path, dtype=np.float64, **kwargs)
    except ValueError:  # non-numeric content: coerce per column, invalid values become NaN
        df = pd.read_csv(path, dtype=str, **kwargs)
        df = df.apply(pd.to_numeric, errors="coerce")

    col = {r: i for i, r in use.items() if r != ROLE_CUSTOM}
    xyz_cols = [col["X"], col["Y"], col["Z"]]
    n_raw = len(df)
    df = df.dropna(subset=xyz_cols)
    xyz = df[xyz_cols].to_numpy(dtype=np.float64)

    attrs = {}
    for i, r in use.items():
        if r in XYZ_ROLES:
            continue
        name = f"col_{i + 1}" if r == ROLE_CUSTOM else r
        attrs[name] = df[i].to_numpy()

    source = {
        "type": "xyz", "path": path, "skip_rows": skip_rows,
        "delimiter": delim_key, "roles": list(column_roles),
        "encoding": encoding, "dropped_rows": n_raw - len(df),
    }
    return PointCloud(xyz, attrs, source, name=os.path.basename(path))
