"""Color modes, palettes and scalar color scales. All colors are N×4 float32 RGBA."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MODE_LABELS = {
    "elevation": "Elevation",
    "intensity": "Intensity",
    "rgb": "RGB",
    "classification": "Classification",
    "single": "Solid",
    "deviation": "Δ to surface",
}
SCALAR_MODES = ("elevation", "intensity", "deviation")
DEVIATION_ATTR = "ΔZ to surface"
GRAY_RGBA = (0.33, 0.35, 0.38, 1.0)

# palette stops: [t, r, g, b], low value → high value
PALETTES = {
    "Rainbow": [[0.00, 0.05, 0.05, 0.55], [0.25, 0.00, 0.50, 1.00], [0.50, 0.10, 0.85, 0.40],
                [0.75, 1.00, 0.85, 0.00], [1.00, 0.85, 0.10, 0.10]],
    "Turbo": [[0.000, 0.190, 0.072, 0.232], [0.125, 0.276, 0.420, 0.891], [0.250, 0.158, 0.736, 0.923],
              [0.375, 0.196, 0.949, 0.597], [0.500, 0.643, 0.990, 0.234], [0.625, 0.945, 0.828, 0.208],
              [0.750, 0.981, 0.540, 0.136], [0.875, 0.854, 0.243, 0.039], [1.000, 0.480, 0.016, 0.011]],
    "Viridis": [[0.00, 0.267, 0.005, 0.329], [0.25, 0.229, 0.322, 0.546], [0.50, 0.128, 0.567, 0.551],
                [0.75, 0.369, 0.789, 0.383], [1.00, 0.993, 0.906, 0.144]],
    "Bathymetry": [[0.00, 0.03, 0.04, 0.22], [0.30, 0.05, 0.20, 0.55], [0.60, 0.12, 0.52, 0.80],
                   [0.85, 0.45, 0.82, 0.92], [1.00, 0.85, 0.97, 1.00]],
    "Grayscale": [[0.0, 0.05, 0.05, 0.05], [1.0, 1.0, 1.0, 1.0]],
    # land + sea: blues below 0 m, greens / browns above; the break is kept at 0 m (see map_scalar)
    "Topo-bathy": [[0.0, 0.03, 0.09, 0.33], [0.25, 0.08, 0.32, 0.66], [0.495, 0.55, 0.83, 0.95],
                   [0.505, 0.22, 0.55, 0.27], [0.65, 0.56, 0.70, 0.32], [0.82, 0.62, 0.46, 0.26],
                   [1.0, 0.96, 0.96, 0.96]],
    # change maps: negative (deeper / cut / erosion) blue · 0 white · positive (shoaler / fill / accretion) red
    "Diverging": [[0.0, 0.13, 0.27, 0.70], [0.25, 0.42, 0.62, 0.92], [0.5, 0.96, 0.96, 0.96],
                  [0.75, 0.94, 0.55, 0.42], [1.0, 0.70, 0.09, 0.17]],
}

# ASPRS standard class colors
_CLASS_COLORS = {
    0: (0.60, 0.60, 0.60), 1: (0.75, 0.75, 0.75), 2: (0.65, 0.45, 0.25),
    3: (0.60, 0.90, 0.50), 4: (0.30, 0.75, 0.30), 5: (0.10, 0.50, 0.15),
    6: (0.90, 0.30, 0.25), 7: (1.00, 0.00, 1.00), 8: (1.00, 1.00, 0.40),
    9: (0.20, 0.45, 1.00), 10: (0.55, 0.30, 0.55), 11: (0.35, 0.35, 0.35),
    17: (0.95, 0.65, 0.20), 18: (1.00, 0.20, 0.60),
}


def _lut(stops, n=256):
    stops = np.asarray(stops, dtype=np.float64)
    t = np.linspace(0.0, 1.0, n)
    rgb = [np.interp(t, stops[:, 0], stops[:, k]) for k in (1, 2, 3)]
    return np.column_stack(rgb + [np.ones(n)]).astype(np.float32)


_LUTS = {name: _lut(stops) for name, stops in PALETTES.items()}


def get_lut(name, reverse=False):
    lut = _LUTS.get(name, _LUTS["Rainbow"])
    return lut[::-1].copy() if reverse else lut


@dataclass
class ColorScale:
    palette: str = "Rainbow"
    reverse: bool = False
    vmin: float | None = None    # DISPLAY range: colours are stretched between these (colour-bar handles)
    vmax: float | None = None
    amin: float | None = None    # SCALE limits: fixed ends of the colour bar (Shallow / Deep in the card)
    amax: float | None = None
    auto: bool = True            # scale limits follow the data (2–98 %) until the user sets them
    out_of_range: str = "clamp"  # "clamp" | "gray"

    @property
    def lut(self):
        return get_lut(self.palette, self.reverse)


DEFAULT_PALETTE = {"elevation": "Rainbow", "intensity": "Grayscale", "deviation": "Diverging"}
AUTO_PERCENTILES = {"elevation": (2.0, 98.0), "intensity": (1.0, 99.0), "deviation": (2.0, 98.0)}


# ---------------------------------------------------------------- scalar values
def scalar_values(pc, idx, mode):
    if mode == "elevation":
        return pc.xyz[idx, 2].astype(np.float32)
    if mode == "intensity" and pc.has("Intensity"):
        return pc.attrs["Intensity"][idx].astype(np.float32)
    if mode == "deviation" and pc.has(DEVIATION_ATTR):
        return pc.attrs[DEVIATION_ATTR][idx].astype(np.float32)
    return None


def symmetric_range(values, lo=2.0, hi=98.0):
    """±max(|p_lo|, |p_hi|) so that 0 sits in the middle of a diverging palette."""
    a, b = auto_range(values, lo, hi)
    m = max(abs(a), abs(b), 1e-3)
    return -m, m


def _sample(v, n=1_000_000):
    v = v if len(v) <= n else v[:: len(v) // n]
    return v[np.isfinite(v)]


def auto_range(values, lo=2.0, hi=98.0):
    s = _sample(values)
    if len(s) == 0:
        return 0.0, 1.0
    a, b = (float(x) for x in np.percentile(s, [lo, hi]))
    return (a, b) if b > a else (a, a + 1.0)


def full_range(values):
    v = values[np.isfinite(values)]
    if len(v) == 0:
        return 0.0, 1.0
    a, b = float(v.min()), float(v.max())
    return (a, b) if b > a else (a, a + 1.0)


def topo_t(values, vmin, vmax):
    """Topo-bathy position 0…1 with 0 m always at 0.5: [vmin, 0] → [0, 0.5], [0, vmax] → [0.5, 1]."""
    v = np.asarray(values, dtype=np.float64)
    lo, hi = min(vmin, 0.0), max(vmax, 0.0)
    t = np.where(v < 0, 0.5 * (v - lo) / (0.0 - lo if lo < 0 else 1.0),
                 0.5 + 0.5 * v / (hi if hi > 0 else 1.0))
    if vmax <= 0:              # all below 0 m: use the sea half only, stretched over the display range
        t = 0.5 * (v - vmin) / ((vmax - vmin) or 1.0)
    elif vmin >= 0:            # all above 0 m: the land half only
        t = 0.5 + 0.5 * (v - vmin) / ((vmax - vmin) or 1.0)
    return t


_TB = PALETTES["Topo-bathy"]
_SEA_LUT = _lut([[s[0] / 0.495, *s[1:]] for s in _TB if s[0] <= 0.495])       # separate tables: the colour
_LAND_LUT = _lut([[(s[0] - 0.505) / 0.495, *s[1:]] for s in _TB if s[0] >= 0.505])  # changes exactly at 0 m


def map_scalar(values, scale):
    lut = scale.lut
    vmin, vmax = scale.vmin, scale.vmax
    if scale.palette == "Topo-bathy":
        v = np.asarray(values, dtype=np.float64)
        t = topo_t(v, vmin, vmax)
        np.nan_to_num(t, copy=False, nan=0.0)
        sea = t < 0.5
        out = np.empty((len(t), 4), np.float32)
        out[sea] = _SEA_LUT[np.clip(t[sea] / 0.5 * 255, 0, 255).astype(np.int32)]
        out[~sea] = _LAND_LUT[np.clip((t[~sea] - 0.5) / 0.5 * 255, 0, 255).astype(np.int32)]
        if scale.out_of_range == "gray":
            out[(values < vmin) | (values > vmax) | ~np.isfinite(values)] = GRAY_RGBA
        return out
    span = (vmax - vmin) or 1.0
    t = (values - vmin) * (255.0 / span)
    np.nan_to_num(t, copy=False, nan=0.0)
    out = lut[np.clip(t, 0, 255).astype(np.int32)]
    if scale.out_of_range == "gray":
        out[(values < vmin) | (values > vmax) | ~np.isfinite(values)] = GRAY_RGBA
    return out


# ---------------------------------------------------------------- modes
def _has_color(pc):
    """LAS formats 2/3/5/7/8 always carry RGB fields — often all zero. Only offer RGB if real colours exist."""
    n = len(pc)
    step = max(1, n // 200_000)
    try:
        return any(float(np.nanmax(pc.attrs[c][::step])) > 0 for c in ("R", "G", "B"))
    except ValueError:
        return False


def available_modes(pc):
    modes = ["elevation"]
    if pc.has("Intensity"):
        modes.append("intensity")
    if all(pc.has(c) for c in ("R", "G", "B")) and _has_color(pc):
        modes.append("rgb")
    if pc.has("Classification"):
        modes.append("classification")
    if pc.has(DEVIATION_ATTR):
        modes.append("deviation")
    modes.append("single")
    return modes


def compute_colors(pc, idx, mode, scale=None):
    n = len(idx)
    if mode in SCALAR_MODES and scale is not None:
        vals = scalar_values(pc, idx, mode)
        if vals is not None:
            return map_scalar(vals, scale)
    if mode == "rgb" and all(pc.has(c) for c in ("R", "G", "B")):
        rgb = np.column_stack([pc.attrs[c][idx] for c in ("R", "G", "B")]).astype(np.float32)
        rgb = np.nan_to_num(rgb)
        scale_ = 65535.0 if rgb.max(initial=0) > 255 else 255.0
        out = np.ones((n, 4), np.float32)
        out[:, :3] = np.clip(rgb / scale_, 0, 1)
        return out
    if mode == "classification" and pc.has("Classification"):
        cls = np.nan_to_num(pc.attrs["Classification"][idx]).astype(np.int64)
        out = np.ones((n, 4), np.float32)
        for c in np.unique(cls):
            rng = np.random.default_rng(int(c))
            out[cls == c, :3] = _CLASS_COLORS.get(int(c), tuple(rng.uniform(0.3, 1.0, 3)))
        return out
    out = np.empty((n, 4), np.float32)
    out[:] = (0.85, 0.85, 0.85, 1.0)
    return out


# ---------------------------------------------------------------- ticks
def nice_ticks(lo, hi, target=6):
    span = hi - lo
    if span <= 0 or not math.isfinite(span):
        return [], 0
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    start = math.ceil(lo / step - 1e-9) * step
    ticks = []
    v = start
    while v <= hi + step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    decimals = max(0, -math.floor(math.log10(step) + 1e-9)) + (1 if m == 2.5 and step < 10 else 0)
    return ticks, decimals
