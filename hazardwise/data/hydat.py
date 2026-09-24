"""HYDAT access layer.

Reads the real ECCC HYDAT SQLite database (installed by `python -m
hazardwise.setup_data`). Schema used:

- STATIONS(STATION_NUMBER, STATION_NAME, PROV_TERR_STATE_LOC, LATITUDE,
  LONGITUDE, DRAINAGE_AREA_GROSS, HYD_STATUS)
- ANNUAL_INSTANT_PEAKS(STATION_NUMBER, DATA_TYPE, YEAR, PEAK_CODE, PEAK, SYMBOL)
  -> preferred source: instantaneous annual maxima (DATA_TYPE='Q', PEAK_CODE='H')
- ANNUAL_STATISTICS(STATION_NUMBER, DATA_TYPE, YEAR, MAX, MAX_SYMBOL)
  -> fallback: annual max of DAILY flows (systematically <= instantaneous peak;
     flagged in provenance so the explanation chain can say so)

SYMBOL/MAX_SYMBOL 'E' marks estimated values -> feeds the confidence engine.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..models.ams import AnnualMaximumSeries

DEFAULT_DATA_DIR = Path.home() / ".hazardwise"
HYDAT_FILENAME = "Hydat.sqlite3"


def hydat_path(data_dir: Path | None = None) -> Path:
    return (data_dir or DEFAULT_DATA_DIR) / HYDAT_FILENAME


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Station:
    station_number: str
    station_name: str
    province: str
    latitude: float
    longitude: float
    drainage_area_km2: float | None
    distance_km: float
    n_peak_years: int


class HydatDB:
    def __init__(self, db_path: Path | None = None):
        self.path = Path(db_path) if db_path else hydat_path()
        if not self.path.exists():
            raise FileNotFoundError(
                f"HYDAT database not found at {self.path}. "
                "Run:  python -m hazardwise.setup_data"
            )
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    # ---------------------------------------------------------------- stations

    def stations_near(
        self, lat: float, lon: float, radius_km: float = 50.0,
        min_years: int = 15, limit: int = 5,
    ) -> list[Station]:
        """Nearest flow-gauged stations with a usable peak record.

        v0.2 heuristic: proximity search with a coarse lat/lon prefilter, ranked
        by distance. TRUE watershed membership (NHN spatial join) is a later
        milestone; every report built on this heuristic carries a caveat.
        """
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
        rows = self.conn.execute(
            """
            SELECT s.STATION_NUMBER, s.STATION_NAME, s.PROV_TERR_STATE_LOC,
                   s.LATITUDE, s.LONGITUDE, s.DRAINAGE_AREA_GROSS,
                   (SELECT COUNT(*) FROM ANNUAL_INSTANT_PEAKS p
                     WHERE p.STATION_NUMBER = s.STATION_NUMBER
                       AND p.DATA_TYPE = 'Q' AND p.PEAK_CODE = 'H'
                       AND p.PEAK IS NOT NULL) AS n_inst,
                   (SELECT COUNT(*) FROM ANNUAL_STATISTICS a
                     WHERE a.STATION_NUMBER = s.STATION_NUMBER
                       AND a.DATA_TYPE = 'Q' AND a.MAX IS NOT NULL) AS n_daily
            FROM STATIONS s
            WHERE s.LATITUDE BETWEEN ? AND ? AND s.LONGITUDE BETWEEN ? AND ?
            """,
            (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
        ).fetchall()

        out: list[Station] = []
        for r in rows:
            n_years = max(r["n_inst"], r["n_daily"])
            if n_years < min_years or r["LATITUDE"] is None:
                continue
            d = haversine_km(lat, lon, r["LATITUDE"], r["LONGITUDE"])
            if d > radius_km:
                continue
            out.append(Station(
                r["STATION_NUMBER"], r["STATION_NAME"] or r["STATION_NUMBER"],
                r["PROV_TERR_STATE_LOC"] or "", float(r["LATITUDE"]),
                float(r["LONGITUDE"]),
                float(r["DRAINAGE_AREA_GROSS"]) if r["DRAINAGE_AREA_GROSS"] else None,
                round(d, 1), int(n_years),
            ))
        out.sort(key=lambda s: s.distance_km)
        return out[:limit]

    def station_info(self, station_number: str):
        """(lat, lon) of a station, or None if unknown."""
        r = self.conn.execute(
            "SELECT LATITUDE, LONGITUDE FROM STATIONS WHERE STATION_NUMBER=?",
            (station_number,)).fetchone()
        if r is None or r["LATITUDE"] is None:
            return None
        return float(r["LATITUDE"]), float(r["LONGITUDE"])

    def annual_stage_maxima(self, station_number: str) -> list[tuple[int, float]]:
        """Measured annual maximum WATER LEVELS (m) at the gauge — DATA_TYPE
        'H' in ANNUAL_INSTANT_PEAKS. Levels use an arbitrary local datum, but
        DIFFERENCES between years are physical: max(level) - median(level) is
        the observed rise of the biggest flood above a typical annual peak,
        with no rating curve involved."""
        rows = self.conn.execute(
            """SELECT YEAR, PEAK FROM ANNUAL_INSTANT_PEAKS
               WHERE STATION_NUMBER=? AND DATA_TYPE='H' AND PEAK_CODE='H'
                 AND PEAK IS NOT NULL ORDER BY YEAR""",
            (station_number,)).fetchall()
        return [(int(r["YEAR"]), float(r["PEAK"])) for r in rows]

    # --------------------------------------------------------------------- AMS

    def annual_maxima(self, station_number: str) -> tuple[AnnualMaximumSeries, str]:
        """Extract the AMS for a station. Returns (ams, provenance)."""
        rows = self.conn.execute(
            """SELECT YEAR, PEAK, SYMBOL FROM ANNUAL_INSTANT_PEAKS
               WHERE STATION_NUMBER=? AND DATA_TYPE='Q' AND PEAK_CODE='H'
                 AND PEAK IS NOT NULL AND PEAK > 0 ORDER BY YEAR""",
            (station_number,),
        ).fetchall()
        provenance = "instantaneous annual peak flows"
        if len(rows) < 15:
            rows = self.conn.execute(
                """SELECT YEAR, MAX AS PEAK, MAX_SYMBOL AS SYMBOL
                   FROM ANNUAL_STATISTICS
                   WHERE STATION_NUMBER=? AND DATA_TYPE='Q'
                     AND MAX IS NOT NULL AND MAX > 0 ORDER BY YEAR""",
                (station_number,),
            ).fetchall()
            provenance = ("annual maxima of DAILY mean flows (instantaneous peaks "
                          "unavailable; daily maxima slightly understate true peaks)")
        if not rows:
            raise ValueError(f"No usable annual maxima for station {station_number}")

        name_row = self.conn.execute(
            "SELECT STATION_NAME FROM STATIONS WHERE STATION_NUMBER=?",
            (station_number,),
        ).fetchone()
        name = name_row["STATION_NAME"] if name_row else station_number

        # de-duplicate years (keep max), track estimated flags
        by_year: dict[int, tuple[float, bool]] = {}
        for r in rows:
            y, q = int(r["YEAR"]), float(r["PEAK"])
            est = (r["SYMBOL"] or "").strip().upper() == "E"
            if y not in by_year or q > by_year[y][0]:
                by_year[y] = (q, est)
        years = tuple(sorted(by_year))
        flows = tuple(by_year[y][0] for y in years)
        flags = tuple(by_year[y][1] for y in years)
        return AnnualMaximumSeries(station_number, name, years, flows, flags), provenance

    def close(self):
        self.conn.close()
