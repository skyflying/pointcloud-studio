"""Vertical datum correction — e.g. from the survey datum (TWVD2001, MSL, ellipsoid) to chart datum (LAT, MLLW …).

The separation S is the height of the TARGET datum (chart datum) in the SOURCE vertical datum, in metres, up
positive: a chart datum 1.8 m below MSL has S = −1.8. Elevations then become  z_target = z_source − S
(depths below chart datum = −z_target). S comes from a constant, a raster (GeoTIFF / ASC …, bilinear) or a
table of points (X Y S, nearest or inverse distance).

S-100 vertical datum codes (IHO S-100 Part 4a / S-102 3.0.0) are listed for the products that record them."""
from __future__ import annotations

import os

import numpy as np

# code → (name, abbreviation)
S100_VERTICAL_DATUMS = {
    1: ("meanLowWaterSprings", "MLWS"), 2: ("meanLowerLowWaterSprings", ""), 3: ("meanSeaLevel", "MSL"),
    4: ("lowestLowWater", ""), 5: ("meanLowWater", "MLW"), 6: ("lowestLowWaterSprings", ""),
    7: ("approximateMeanLowWaterSprings", ""), 8: ("indianSpringLowWater", "ISLW"), 9: ("lowWaterSprings", ""),
    10: ("approximateLowestAstronomicalTide", ""), 11: ("nearlyLowestLowWater", ""),
    12: ("meanLowerLowWater", "MLLW"), 13: ("lowWater", "LW"), 14: ("approximateMeanLowWater", ""),
    15: ("approximateMeanLowerLowWater", ""), 16: ("meanHighWater", "MHW"), 17: ("meanHighWaterSprings", "MHWS"),
    18: ("highWater", "HW"), 19: ("approximateMeanSeaLevel", ""), 20: ("highWaterSprings", ""),
    21: ("meanHigherHighWater", "MHHW"), 22: ("equinoctialSpringLowWater", ""),
    23: ("lowestAstronomicalTide", "LAT"), 24: ("localDatum", ""), 25: ("internationalGreatLakesDatum1985", ""),
    26: ("meanWaterLevel", ""), 27: ("lowerLowWaterLargeTide", ""), 28: ("higherHighWaterLargeTide", ""),
    29: ("nearlyHighestHighWater", ""), 30: ("highestAstronomicalTide", "HAT"),
    44: ("balticSeaChartDatum2000", ""),
}


def datum_label(code):
    name, abbr = S100_VERTICAL_DATUMS.get(int(code), (f"code {code}", ""))
    return f"{abbr} — {name} ({code})" if abbr else f"{name} ({code})"


class Separation:
    """S(x, y): height of the target datum in the source datum (m, up +).

    kind "constant": value · "raster": path, band · "table": path, x_col, y_col, value_col, method, max_dist."""

    def __init__(self, kind="constant", value=0.0, path=None, band=1, x_col=0, y_col=1, value_col=2,
                 method="idw", max_dist=0.0, crs=None):
        self.kind = kind
        self.value = float(value)
        self.path = path
        self.band = int(band)
        self.x_col, self.y_col, self.value_col = int(x_col), int(y_col), int(value_col)
        self.method = method
        self.max_dist = float(max_dist)
        self.crs = crs                       # project CRS (rasters / tables are transformed when they differ)
        self._table = None

    def spec(self):
        """JSON-able description (stored with the layer)."""
        d = {"kind": self.kind}
        if self.kind == "constant":
            d["value"] = self.value
        elif self.kind == "raster":
            d.update(path=self.path, band=self.band)
        else:
            d.update(path=self.path, x_col=self.x_col, y_col=self.y_col, value_col=self.value_col,
                     method=self.method, max_dist=self.max_dist)
        return d

    @classmethod
    def from_spec(cls, d, crs=None):
        d = dict(d or {})
        return cls(crs=crs, **{k: v for k, v in d.items() if k in ("kind", "value", "path", "band", "x_col", "y_col",
                                                                    "value_col", "method", "max_dist")})

    def describe(self):
        if self.kind == "constant":
            return f"constant {self.value:+.3f} m"
        name = os.path.basename(self.path or "?")
        return f"{'raster' if self.kind == 'raster' else 'table'} {name}"

    def at(self, x, y):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        if self.kind == "constant":
            return np.full(x.shape, self.value)
        if not self.path or not os.path.isfile(self.path):
            raise FileNotFoundError(f"Separation file not found: {self.path}")
        if self.kind == "raster":
            from core.uncertainty import sample_raster
            return np.asarray(sample_raster(self.path, x, y, self.band, self.crs), float)
        if self._table is None:
            from core.uncertainty import read_table
            xy, vals, _h = read_table(self.path, self.x_col, self.y_col, (self.value_col,))
            self._table = (xy, vals[:, 0])
        from core.uncertainty import match_table
        xy, v = self._table
        return np.asarray(match_table(np.column_stack([x, y]), xy, v, self.max_dist, self.method), float)


def apply_xyz(xyz, sep, chunk=2_000_000):
    """Elevations relative to the target datum, in place: z −= S(x, y). Returns the number of points without a
    separation value (left unchanged and reported, never silently shifted by a guess)."""
    missing = 0
    for s in range(0, len(xyz), chunk):
        p = xyz[s:s + chunk]
        S = sep.at(p[:, 0], p[:, 1])
        ok = np.isfinite(S)
        missing += int((~ok).sum())
        p[ok, 2] -= S[ok]
    return missing


def apply_dem(dem, sep):
    """New Dem relative to the target datum (cells without a separation → NaN). Returns (dem, missing cells)."""
    from core.surface import Dem
    xs, ys = dem.centers()
    X, Y = np.meshgrid(xs, ys)
    if dem.has_true_position:                                       # shoal grids: separation at the sounding
        X = np.where(np.isfinite(dem.tx), dem.tx, X)
        Y = np.where(np.isfinite(dem.ty), dem.ty, Y)
    S = sep.at(X.ravel(), Y.ravel()).reshape(dem.z.shape)
    has = np.isfinite(dem.z)
    missing = int((has & ~np.isfinite(S)).sum())
    z = np.where(np.isfinite(S), dem.z - S, np.nan).astype(np.float32)
    meta = dict(dem.meta)
    return Dem(z, dem.x0, dem.ytop, dem.cell, dem.method, meta=meta,
               tx=None if dem.tx is None else dem.tx.copy(), ty=None if dem.ty is None else dem.ty.copy()), missing
