"""Geocoding cascade (handoff Section: geocoding is stage-confidence-scored).

Rural/cottage addresses are the hard case: civic numbers on township roads
often don't exist in OSM. The cascade degrades EXPLICITLY, never silently:

  Stage 1  full address as given            -> confidence A (rooftop/parcel)
  Stage 2  street + locality + province     -> confidence B (street level)
  Stage 3  locality + province              -> confidence C (town centroid)
  Stage 4  nothing found                    -> hard failure with guidance

Confidence propagates into the report's Data Quality section and CAPS the
overall grade: a C-geocode can never yield an A-grade report, because we may
be assessing the wrong hillside. Nominatim result 'type' upgrades/downgrades
within a stage (e.g. Stage 1 hit that is only a town centroid -> C).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str
    confidence: str          # "A" | "B" | "C"
    stage: int
    method: str              # human-readable, goes in the explanation chain


class Geocoder(Protocol):
    def geocode(self, address: str) -> GeocodeResult: ...


PRECISE_TYPES = {"house", "building", "residential", "address", "parcel", "yes"}
STREET_TYPES = {"road", "street", "highway", "tertiary", "secondary", "primary",
                "unclassified", "service", "track"}


def _confidence_from_osm(raw, stage: int) -> str:
    t = (getattr(raw, "raw", {}) or {}).get("type", "")
    c = (getattr(raw, "raw", {}) or {}).get("class", "")
    if t in PRECISE_TYPES or c == "building":
        return "A"
    if t in STREET_TYPES or c == "highway":
        return "B"
    if stage == 1:
        return "B"   # found the full string but only coarse match
    return "C"


def _strip_unit_postal(address: str) -> str:
    a = re.sub(r"\b[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d\b", "", address)  # postal
    a = re.sub(r"\b(unit|suite|apt|#)\s*\w+,?\s*", "", a, flags=re.I)
    return re.sub(r"\s{2,}", " ", a).strip(" ,")


def _street_town_prov(address: str) -> str | None:
    parts = [p.strip() for p in _strip_unit_postal(address).split(",") if p.strip()]
    if len(parts) >= 3:
        street = re.sub(r"^\d+[A-Za-z]?\s+", "", parts[0])  # drop civic number
        return f"{street}, {parts[-2]}, {parts[-1]}"
    return None


def _town_prov(address: str) -> str | None:
    parts = [p.strip() for p in _strip_unit_postal(address).split(",") if p.strip()]
    if len(parts) >= 2:
        return f"{parts[-2]}, {parts[-1]}"
    return None


class NominatimGeocoder:
    """OSM Nominatim via geopy. Free; 1 req/s policy enforced."""

    def __init__(self, user_agent: str = "HazardWise/0.2 (flood-risk research)"):
        from geopy.geocoders import Nominatim  # import here: offline tests don't need it
        self._geo = Nominatim(user_agent=user_agent, timeout=20)
        self._last = 0.0

    def _query(self, q: str):
        wait = 1.1 - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        return self._geo.geocode(q + ", Canada", addressdetails=True)

    def geocode(self, address: str) -> GeocodeResult:
        stages = [
            (1, address, "full address"),
            (2, _street_town_prov(address), "street + locality (civic number dropped)"),
            (3, _town_prov(address), "locality centroid only"),
        ]
        tried = []
        for stage, q, label in stages:
            if not q:
                continue
            tried.append(q)
            loc = self._query(q)
            if loc is not None:
                conf = _confidence_from_osm(loc, stage)
                if stage == 3:
                    conf = "C"
                return GeocodeResult(
                    loc.latitude, loc.longitude, loc.address, conf, stage,
                    f"geocoded via OpenStreetMap Nominatim at stage {stage} "
                    f"({label}); location precision grade {conf}",
                )
        raise ValueError(
            "Could not geocode this address at any cascade stage. Tried: "
            + " | ".join(tried)
            + ". For remote cottage properties, supply coordinates directly "
              "(pipeline --lat/--lon) or a nearby landmark address."
        )


class FixedGeocoder:
    """Offline/test geocoder with a lookup table."""

    def __init__(self, table: dict[str, tuple[float, float, str]]):
        self.table = table

    def geocode(self, address: str) -> GeocodeResult:
        if address not in self.table:
            raise ValueError(f"FixedGeocoder has no entry for {address!r}")
        lat, lon, conf = self.table[address]
        return GeocodeResult(lat, lon, address, conf, 1,
                             f"fixed test coordinate (confidence {conf})")
