"""A seabed raster that is NOT loaded into the project: it is opened and sampled only when an analysis runs
(only the window around the cable is read), so tens of yearly model rasters do not fill the memory."""
from __future__ import annotations

import os


class RasterSource:
    def __init__(self, path, crs=None, band=1):
        self.path = path
        self.crs = crs                    # project CRS the stations are in (reprojected to the raster's CRS)
        self.band = band
        self.name = os.path.basename(path)

    def sample_xy(self, x, y):
        from core.uncertainty import sample_raster
        return sample_raster(self.path, x, y, self.band, self.crs)

    def __repr__(self):
        return f"RasterSource({self.name})"


def is_lazy(obj):
    return hasattr(obj, "sample_xy")
