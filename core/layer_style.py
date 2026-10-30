"""Display style of line / point layers (routes, vector layers, structures): colour and the attribute shown
as text label. Stored per dataset (Dataset.style) and in the project file."""
from __future__ import annotations

DEFAULT_COLOR = {"route": "#4cc9f0", "route_z": "#ff9e42", "vector": "#c77dff", "structure": "#ff9e42"}
STYLED_KINDS = ("route", "vector", "structure")


def color_of(ds_kind, style, obj=None):
    c = (style or {}).get("color")
    if c:
        return c
    if ds_kind == "route" and obj is not None and getattr(obj, "z", None) is not None:
        return DEFAULT_COLOR["route_z"]
    return DEFAULT_COLOR.get(ds_kind, "#ffffff")


def label_fields(ds_kind, obj):
    """[(key, label)] the user can choose to show as text."""
    out = [(None, "(no labels)")]
    if ds_kind == "route":
        out.append(("__name__", "Name"))
        for k in sorted(((getattr(obj, "meta", {}) or {}).get("attributes") or {}).keys()):
            out.append((k, k))
    elif ds_kind == "structure":
        out += [("__name__", "Name"), ("__diameter__", "Diameter (m)")]
        attrs = (getattr(obj, "meta", {}) or {}).get("attributes")
        if isinstance(attrs, list) and attrs and isinstance(attrs[0], dict):
            for k in sorted({k for a in attrs for k in a}):
                out.append((k, k))
    elif ds_kind == "vector":
        keys = []
        for f in getattr(obj, "features", []):
            for k in (f.get("attrs") or {}):
                if k not in keys:
                    keys.append(k)
        out += [(k, k) for k in keys]
    return out


def default_label(ds_kind):
    return {"route": "__name__", "structure": "__name__", "vector": None}.get(ds_kind)


def label_key(ds_kind, style):
    st = style or {}
    return st["label"] if "label" in st else default_label(ds_kind)


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return str(v)
