"""AOI definition and local grid. Uses a local equirectangular projection which is
accurate to well under a pixel over a 3 km AOI; swap for pyproj/UTM at integration
if the rest of the repo standardizes on it."""
from dataclasses import dataclass, field
import numpy as np
from . import params_sat as P

M_PER_DEG_LAT = 111320.0

@dataclass
class AOI:
    lat: float
    lon: float
    half_km: float = P.SAT_AOI_HALF_KM
    res_m: float = P.SAT_GRID_RES_M
    geocode_precision_m: float = 10.0
    n: int = field(init=False)

    def __post_init__(self):
        self.n = int(round(2 * self.half_km * 1000.0 / self.res_m))

    @property
    def m_per_deg_lon(self):
        return M_PER_DEG_LAT * np.cos(np.deg2rad(self.lat))

    def lonlat_to_rc(self, lon, lat):
        """(lon, lat) -> fractional (row, col); row 0 = north edge."""
        dx = (np.asarray(lon) - self.lon) * self.m_per_deg_lon
        dy = (np.asarray(lat) - self.lat) * M_PER_DEG_LAT
        col = dx / self.res_m + self.n / 2.0
        row = self.n / 2.0 - dy / self.res_m
        return row, col

    @property
    def address_rc(self):
        return self.n // 2, self.n // 2

    @property
    def geocode_gated(self) -> bool:
        return self.geocode_precision_m > P.SAT_MAP_MIN_GEOCODE_M

    def bounds_lonlat(self):
        dlat = self.half_km * 1000.0 / M_PER_DEG_LAT
        dlon = self.half_km * 1000.0 / self.m_per_deg_lon
        return (self.lon - dlon, self.lat - dlat, self.lon + dlon, self.lat + dlat)
