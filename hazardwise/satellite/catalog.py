"""Scene index: STAC search with parquet cache. Network-dependent parts are lazy;
everything downstream consumes the plain DataFrame schema so tests and offline
runs work from synthetic/loaded indexes."""
import os
import pandas as pd

SCHEMA = ["scene_id", "sensor", "datetime", "footprint_wkt", "href"]

STAC_ENDPOINTS = {
    # verify at deploy time (design doc S2.3)
    "planetary_computer": "https://planetarycomputer.microsoft.com/api/stac/v1",
    "copernicus": "https://catalogue.dataspace.copernicus.eu/stac",
}
COLLECTIONS = {"s1_rtc": "sentinel-1-rtc", "s2": "sentinel-2-l2a"}

def empty_index() -> pd.DataFrame:
    return pd.DataFrame(columns=SCHEMA)

def load_index(path: str) -> pd.DataFrame:
    return pd.read_parquet(path) if os.path.exists(path) else empty_index()

def save_index(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_parquet(path, index=False)

def stac_search(aoi, dt_start, dt_end, endpoint="planetary_computer",
                collection="s1_rtc") -> pd.DataFrame:
    """Live STAC search (requires network + pystac-client)."""
    from pystac_client import Client  # lazy: not needed offline
    cli = Client.open(STAC_ENDPOINTS[endpoint])
    w, s, e, n = aoi.bounds_lonlat()
    items = cli.search(collections=[COLLECTIONS[collection]], bbox=[w, s, e, n],
                       datetime=f"{dt_start}/{dt_end}").item_collection()
    rows = [{"scene_id": it.id, "sensor": collection,
             "datetime": pd.Timestamp(it.datetime).tz_localize(None),
             "footprint_wkt": str(it.geometry), "href": it.get_self_href() or ""}
            for it in items]
    return pd.DataFrame(rows, columns=SCHEMA)
