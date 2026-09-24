"""Street-map backdrop from OSM tiles (v0.17). Fetched live, stitched,
cropped to the AOI, resized to the analysis grid, drawn UNDER the hazard
layer at reduced opacity. Fail-open: any error -> caller falls back to the
hillshade backdrop. Polite: auto-drops zoom to fit a tile budget so a backdrop
ALWAYS renders (never raises for size), identified User-Agent, on-disk
tile cache to cut repeat requests. (c) OpenStreetMap contributors — attribution is stamped."""
import io, math, urllib.request
import numpy as np

TILE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
AERIAL = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
          "World_Imagery/MapServer/tile/{z}/{y}/{x}")
ATTRIB_AERIAL = "imagery (c) Esri, Maxar, Earthstar Geographics"
UA = {"User-Agent": "HazardWise/0.20 (local analyst tool)"}
TILE_BUDGET = 25          # max tiles per fetch; zoom auto-drops to fit
MIN_ZOOM = 8              # never coarser than this
import os, tempfile, hashlib
CACHE_DIR = os.path.join(tempfile.gettempdir(), "hw_tilecache")
ATTRIB = "basemap (c) OpenStreetMap contributors"

def deg2num(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y

def _fit_zoom(lat, lon, half_km, start_zoom, budget=TILE_BUDGET):
    """Largest zoom <= start_zoom whose tile span fits the budget. Coarser
    resolution is ALWAYS acceptable -- a backdrop that renders beats a sharp
    one that raises. Falls to MIN_ZOOM even if that still exceeds budget."""
    dlat = half_km * 1000.0 / 111320.0
    dlon = dlat / max(math.cos(math.radians(lat)), 1e-6)
    for z in range(int(start_zoom), MIN_ZOOM - 1, -1):
        xa, ya = deg2num(lat + dlat, lon - dlon, z)   # count REAL tiles the
        xb, yb = deg2num(lat - dlat, lon + dlon, z)   # exact way fetch does
        n = (int(xb) - int(xa) + 1) * (int(yb) - int(ya) + 1)
        if n <= budget:
            return z
    return MIN_ZOOM

def _get_tile(url):
    """Fetch one tile through a small on-disk cache (keyed by URL)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.md5(url.encode()).hexdigest() + ".png"
    path = os.path.join(CACHE_DIR, key)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=15) as r:
        data = r.read()
    try:
        with open(path, "wb") as f:
            f.write(data)
    except OSError:
        pass
    return data

def fetch_street_basemap(lat, lon, half_km, n_px, zoom=15, style="street"):
    """Stitch a tile mosaic for the AOI. zoom is a CEILING: the function
    auto-drops to whatever zoom fits the tile budget, so it renders in any
    situation (wide rural context maps included) rather than raising."""
    from PIL import Image
    z = _fit_zoom(lat, lon, half_km, zoom)
    dlat = half_km * 1000.0 / 111320.0
    dlon = dlat / max(math.cos(math.radians(lat)), 1e-6)
    xa, ya = deg2num(lat + dlat, lon - dlon, z)   # NW corner
    xb, yb = deg2num(lat - dlat, lon + dlon, z)   # SE corner
    txs = list(range(int(xa), int(xb) + 1))
    tys = list(range(int(ya), int(yb) + 1))
    mosaic = Image.new("RGB", (256 * len(txs), 256 * len(tys)), (238, 238, 236))
    for i, tx in enumerate(txs):
        for j, ty in enumerate(tys):
            url = (AERIAL if style == "aerial" else TILE).format(
                z=z, x=tx, y=ty)
            try:
                mosaic.paste(Image.open(io.BytesIO(_get_tile(url))),
                             (256 * i, 256 * j))
            except Exception:
                pass                      # a missing tile leaves the fill color
    left = int((xa - int(xa)) * 256); top = int((ya - int(ya)) * 256)
    right = int((xb - txs[0]) * 256); bottom = int((yb - tys[0]) * 256)
    crop = mosaic.crop((left, top, right, bottom)).resize((n_px, n_px))
    return np.asarray(crop).astype(float) / 255.0
