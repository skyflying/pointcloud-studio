"""Selection model and statistics."""
from __future__ import annotations

import numpy as np


class Selection:
    """Boolean mask over all points. Ops: replace / add / subtract."""

    def __init__(self, n):
        self.mask = np.zeros(n, dtype=bool)

    @property
    def count(self):
        return int(np.count_nonzero(self.mask))

    def indices(self):
        return np.flatnonzero(self.mask)

    def apply(self, idx, op="replace"):
        if op == "replace":
            self.mask[:] = False
            self.mask[idx] = True
        elif op == "add":
            self.mask[idx] = True
        elif op == "subtract":
            self.mask[idx] = False

    def clear(self):
        self.mask[:] = False

    def select_all(self, active_mask):
        self.mask[:] = active_mask

    def invert(self, active_mask):
        self.mask = ~self.mask & active_mask


def _fmt(v):
    if isinstance(v, (float, np.floating)):
        if not np.isfinite(v):
            return "—"
        return f"{v:,.3f}" if abs(v) >= 1 else f"{v:.6g}"
    return f"{v:,}" if isinstance(v, (int, np.integer)) else str(v)


def describe(pc, idx):
    """Rows of (label, value) describing the selected points."""
    n = len(idx)
    if n == 0:
        return []
    if n == 1:
        i = int(idx[0])
        x, y, z = pc.xyz[i]
        rows = [("Index", f"{i:,}"), ("X", f"{x:,.3f}"), ("Y", f"{y:,.3f}"), ("Z", f"{z:,.3f}")]
        las = pc.source.get("las")
        if las is not None:
            for d in pc.source["dimensions"]:
                if d not in ("X", "Y", "Z"):
                    rows.append((d, _fmt(las[d][i])))
        else:
            for name, arr in pc.attrs.items():
                rows.append((name, _fmt(arr[i])))
        return rows

    xyz = pc.xyz[idx]
    mn, mx = xyz.min(axis=0), xyz.max(axis=0)
    ext = mx - mn
    c = xyz.mean(axis=0)
    rows = [
        ("Points", f"{n:,}  ({n / max(pc.n_active, 1) * 100:.2f}% of active)"),
        ("X", f"{mn[0]:,.3f}  →  {mx[0]:,.3f}"),
        ("Y", f"{mn[1]:,.3f}  →  {mx[1]:,.3f}"),
        ("Z", f"{mn[2]:,.3f}  →  {mx[2]:,.3f}"),
        ("Extent", f"{ext[0]:,.2f} × {ext[1]:,.2f} × {ext[2]:,.2f}"),
        ("Centroid", f"{c[0]:,.3f}, {c[1]:,.3f}"),
        ("Z mean ± std", f"{c[2]:,.3f} ± {xyz[:, 2].std():.3f}"),
    ]
    for name, arr in pc.attrs.items():
        if name in ("R", "G", "B"):
            continue
        a = arr[idx]
        if not np.issubdtype(a.dtype, np.number):
            continue
        if name == "Classification":
            vals, counts = np.unique(np.nan_to_num(a).astype(np.int64), return_counts=True)
            order = np.argsort(-counts)[:6]
            txt = "  ·  ".join(f"{vals[k]}: {counts[k]:,}" for k in order)
            if len(vals) > 6:
                txt += f"  · (+{len(vals) - 6} more)"
            rows.append(("Classes", txt))
        else:
            a = a.astype(np.float64)
            rows.append((name, f"{np.nanmin(a):.4g} → {np.nanmax(a):.4g}  ·  mean {np.nanmean(a):.4g}"))
    if all(pc.has(k) for k in ("R", "G", "B")):
        rgb = [int(round(float(np.nanmean(pc.attrs[k][idx])))) for k in ("R", "G", "B")]
        rows.append(("Mean RGB", ", ".join(map(str, rgb))))
    return rows
