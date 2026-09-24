"""Elevation + exposure model.

ELEVATION SOURCES (tried in order, both free, no API key):
  1. NRCan CDEM elevation service (geogratis) — authoritative for Canada
  2. open-elevation.com — global fallback

EXPOSURE MODEL (v0.2 screening — every report says so):
The proper approach is HAND from a hydro-conditioned DEM. Until that module
exists, we compute a defensible proxy:

  local_relief h = elev(property) − min(elev of 8 points at ~800 m around it)
     -> how high the property sits above its local drainage low point.

  bankfull depth  D_b = max(1.0, 0.27 · DA^0.30)   [hydraulic geometry,
     Leopold–Maddock-type scaling on gross drainage area in km²]

  threshold flow  Q* = Q2 · ((D_b + h) / D_b)^2.5   [stage ∝ Q^0.4 inverted]
     where Q2 = median annual peak ≈ bankfull discharge.

  Engine 1 then computes AEP(Q ≥ Q*) with full bootstrap CI.

Because this proxy can misjudge local hydraulics, any estimate built on it is
grade-capped at B and carries a mandatory methodology caveat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

NRCAN_URL = "https://geogratis.gc.ca/services/elevation/cdem/altitude"
OPEN_ELEV_URL = "https://api.open-elevation.com/api/v1/lookup"

STAGE_EXPONENT = 0.4          # depth ~ Q^0.4 (at-a-station hydraulic geometry)
RELIEF_RING_M = 800.0
GRADE_CAP_SCREENING = "B"


class ElevationSource(Protocol):
    def elevation(self, lat: float, lon: float) -> float: ...


class WebElevation:
    """NRCan CDEM first, open-elevation fallback."""

    def __init__(self):
        import requests
        self._rq = requests

    def elevation(self, lat: float, lon: float) -> float:
        try:
            r = self._rq.get(NRCAN_URL, params={"lat": lat, "lon": lon}, timeout=20)
            r.raise_for_status()
            alt = r.json().get("altitude")
            if alt is not None:
                return float(alt)
        except Exception:
            pass
        r = self._rq.get(OPEN_ELEV_URL,
                         params={"locations": f"{lat},{lon}"}, timeout=30)
        r.raise_for_status()
        return float(r.json()["results"][0]["elevation"])


class FixedElevation:
    """Offline/test source: center elevation + surrounding ring elevations."""

    def __init__(self, center: float, ring: float):
        self.center, self.ring = center, ring
        self._first = True

    def elevation(self, lat: float, lon: float) -> float:
        if self._first:
            self._first = False
            return self.center
        return self.ring


@dataclass(frozen=True)
class ExposureAssessment:
    property_elev_m: float
    local_low_elev_m: float
    relief_m: float
    bankfull_depth_m: float
    q2_m3s: float
    threshold_flow_m3s: float
    narrative: list[str]


def _ring_points(lat: float, lon: float, radius_m: float) -> list[tuple[float, float]]:
    pts = []
    for k in range(8):
        b = math.radians(k * 45.0)
        dlat = (radius_m * math.cos(b)) / 111_320.0
        dlon = (radius_m * math.sin(b)) / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
        pts.append((lat + dlat, lon + dlon))
    return pts


def assess_exposure(
    lat: float, lon: float,
    q2_m3s: float,
    drainage_area_km2: float | None,
    elev: ElevationSource,
) -> ExposureAssessment:
    prop = elev.elevation(lat, lon)
    ring = [elev.elevation(a, b) for a, b in _ring_points(lat, lon, RELIEF_RING_M)]
    low = min(ring + [prop])
    relief = max(0.0, prop - low)

    da = drainage_area_km2 if drainage_area_km2 and drainage_area_km2 > 0 else 500.0
    d_b = max(1.0, 0.27 * da ** 0.30)
    q_star = q2_m3s * ((d_b + relief) / d_b) ** (1.0 / STAGE_EXPONENT)

    narrative = [
        f"The property sits at {prop:.0f} m elevation; the lowest ground within "
        f"~{RELIEF_RING_M:.0f} m is {low:.0f} m, so the property stands about "
        f"{relief:.1f} m above its local drainage path.",
        f"Using standard river-channel scaling (bankfull depth ≈ {d_b:.1f} m for a "
        f"{da:,.0f} km² drainage area), water would need to reach roughly "
        f"{q_star:,.0f} m³/s — about {q_star/max(q2_m3s,1e-9):.1f}× the typical annual "
        f"peak — to rise to the property's level.",
        "This is a screening-level terrain estimate, not an engineering flood study; "
        "it is the reason this report's confidence grade is capped at B.",
    ]
    return ExposureAssessment(prop, low, relief, d_b, q2_m3s, q_star, narrative)
