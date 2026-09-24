"""Rural-aware geocoding cascade (v0.19.1). Principle: any North-American
address RESOLVES — worst case to a locality/county centroid with the
imprecision disclosed (the geocode gate then draws an uncertainty circle
and blocks parcel-scale claims). Refusal is reserved for garbage input.

Stages: (1) the base geocoder as-is; (2) rural-grammar variants — Alberta/
prairie legal addressing (leading lot/LLD numbers stripped, Rr -> Range
Road, Twp -> Township Road, 'Rural X County' -> 'X County'); (3) Photon
(photon.komoot.io, OSM, keyless) on the raw and simplified strings;
(4) locality/county centroid via base+Photon, method marked 'centroid' so
downstream precision gating engages."""
from __future__ import annotations
import json, re, urllib.parse, urllib.request

PHOTON = "https://photon.komoot.io/api/?q={q}&limit=1&bbox=-141,41,-52,84"
UA = {"User-Agent": "HazardWise-geocoder/0.19"}

def _variants(addr: str):
    out, a = [], addr
    subs = [
        (r"^\s*\d+[\s,-]+(?=\d)", ""),          # leading lot number
        (r"^\s*\d{5,7}\s+", ""),                # LLD/blue-sign number
        (r"\bR\.?r\.?\b", "Range Road"),
        (r"\bRge\.?\s*Rd\.?\b", "Range Road"),
        (r"\bTwp\.?\s*(Rd\.?)?\b", "Township Road"),
        (r"\bRural\s+", ""),
        (r"\bCounty\s+of\s+", ""),
    ]
    seen = {addr.strip().lower()}
    frontier = [addr]
    for _ in range(3):                          # compose substitutions
        nxt = []
        for a in frontier:
            for pat, rep in subs:
                b = re.sub(pat, rep, a, flags=re.I).strip(" ,")
                if b and b.lower() not in seen:
                    seen.add(b.lower()); out.append(b); nxt.append(b)
        frontier = nxt
    return out

def _locality(addr: str):
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if len(parts) >= 2:
        loc = ", ".join(parts[-2:])
        return re.sub(r"\bRural\s+", "", loc, flags=re.I)
    return None

def _photon(q: str):
    url = PHOTON.format(q=urllib.parse.quote(q))
    with urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=12) as r:
        feats = json.load(r).get("features") or []
    if not feats:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    name = feats[0]["properties"].get("name", q)
    return float(lat), float(lon), name

class RuralAwareGeocoder:
    def __init__(self, base=None):
        if base is None:
            from .. import pipeline as _pl
            base = _pl.NominatimGeocoder()
        self.base = base

    def _result(self, lat, lon, address, method, confidence="B"):
        from .. import pipeline as _pl
        return _pl.GeocodeResult(lat, lon, address, confidence, 0, method)

    def geocode(self, address: str):
        try:
            return self.base.geocode(address)
        except Exception as first_error:
            for v in _variants(address):                      # stage 2
                try:
                    r = self.base.geocode(v)
                    r.method = (getattr(r, "method", "") or "") + \
                        f" [rural variant: '{v}']"
                    return r
                except Exception:
                    pass
            for q in [address] + _variants(address):          # stage 3
                try:
                    hit = _photon(q)
                except Exception:
                    hit = None
                if hit:
                    lat, lon, name = hit
                    return self._result(lat, lon, address,
                                        f"Photon/OSM match on '{q}' "
                                        f"({name})", confidence="B")
            loc = _locality(address)                          # stage 4
            if loc:
                for fn in (self.base.geocode, None):
                    try:
                        if fn:
                            r = fn(loc)
                            lat, lon = r.latitude, r.longitude
                        else:
                            hit = _photon(loc)
                            if not hit:
                                break
                            lat, lon, _ = hit
                        return self._result(
                            lat, lon, address,
                            f"locality centroid fallback ('{loc}') — "
                            "parcel-scale precision NOT available; "
                            "supply --lat/--lon for a parcel map",
                            confidence="D")
                    except Exception:
                        continue
            raise first_error
