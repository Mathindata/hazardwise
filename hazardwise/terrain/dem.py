"""DEM sources for terrain analysis.

Default: NRCan MRDEM-30 DTM as a Cloud-Optimized GeoTIFF on S3 (EPSG:3979,
CGVD2013 heights). rasterio reads only the window we need over HTTP — no bulk
download, ~0.2 MB per property. MRDEM replaces the discontinued CDEM service
that v0.2's point-elevation client used.

Override order:
  1. HW_DEM_PATH env var / constructor arg -> local GeoTIFF (e.g. an HRDEM
     1 m tile for a study area)
  2. MRDEM-30 DTM COG (national default)
"""

from __future__ import annotations

import os

import numpy as np

MRDEM_DTM_COG = ("https://canelevation-dem.s3.ca-central-1.amazonaws.com/"
                 "mrdem-30/mrdem-30-dtm.tif")


class CogDem:
    """Windowed reads from a (remote or local) GeoTIFF."""

    def __init__(self, source: str | None = None):
        self.source = source or os.environ.get("HW_DEM_PATH") or MRDEM_DTM_COG

    def window(self, lat: float, lon: float, half_km: float = 6.0
               ) -> tuple[np.ndarray, float]:
        """Return (dem_array, cell_size_m) centred on lat/lon."""
        import rasterio
        from pyproj import Transformer
        with rasterio.open(self.source) as ds:
            tf = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
            x, y = tf.transform(lon, lat)
            half = half_km * 1000.0
            win = rasterio.windows.from_bounds(
                x - half, y - half, x + half, y + half, transform=ds.transform)
            arr = ds.read(1, window=win, boundless=True,
                          fill_value=ds.nodata if ds.nodata is not None else -9999
                          ).astype(float)
            nod = ds.nodata if ds.nodata is not None else -9999
            arr[arr == nod] = np.nan
            if np.isnan(arr).all():
                raise ValueError(
                    f"DEM has no data at ({lat:.4f}, {lon:.4f}) — outside "
                    "MRDEM coverage?")
            # fill residual nodata with the window median so routing survives
            arr[np.isnan(arr)] = np.nanmedian(arr)
            cell = float(abs(ds.transform.a))
        return arr, cell


class ArrayDem:
    """Offline/test source: a fixed numpy array."""

    def __init__(self, arr: np.ndarray, cell_size_m: float = 30.0):
        self.arr, self.cell = np.asarray(arr, dtype=float), cell_size_m

    def window(self, lat: float, lon: float, half_km: float = 6.0):
        return self.arr.copy(), self.cell
