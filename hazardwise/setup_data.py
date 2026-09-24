"""HazardWise data installer:  python -m hazardwise.setup_data

Downloads the official ECCC HYDAT SQLite database (~1.1 GB zipped) into
~/.hazardwise/ and verifies the tables the pipeline needs. Run once at
installation; re-run quarterly when ECCC publishes a new HYDAT release
(the pipeline records the HYDAT vintage in every report).

Windows-safe: pure requests + zipfile + pathlib, resumable-ish (skips download
if the zip is already present and intact).
"""

from __future__ import annotations

import re
import sqlite3
import sys
import zipfile
from pathlib import Path

import requests

from .data.hydat import DEFAULT_DATA_DIR, HYDAT_FILENAME

ECCC_INDEX = "https://collaboration.cmc.ec.gc.ca/cmc/hydrometrics/www/"
BASINS_INDEX = ECCC_INDEX + "HydrometricNetworkBasinPolygons/"
EGS_ARCHIVE_URL = ("https://data.eodms-sgdot.nrcan-rncan.gc.ca/public/EGS/"
                   "EGS_FGP_Geodatabases/Flood_Inondation/archive_archives/"
                   "EGS_Flood_Product_Archive.gdb.zip")
REQUIRED_TABLES = {"STATIONS", "ANNUAL_INSTANT_PEAKS", "ANNUAL_STATISTICS"}
CHUNK = 1 << 20  # 1 MiB


def find_latest_hydat_url() -> tuple[str, str]:
    r = requests.get(ECCC_INDEX, timeout=60)
    r.raise_for_status()
    names = sorted(set(re.findall(r"Hydat_sqlite3_\d{8}\.zip", r.text)))
    if not names:
        raise RuntimeError(
            f"No Hydat_sqlite3_YYYYMMDD.zip found at {ECCC_INDEX}. "
            "ECCC may have moved the file; check the page in a browser."
        )
    latest = names[-1]
    return ECCC_INDEX + latest, latest


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and zipfile.is_zipfile(dest):
        print(f"  {dest.name} already downloaded and intact — skipping.")
        return
    print(f"  Downloading {url}\n  -> {dest}  (~1.1 GB, this takes a while)")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK):
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = 100 * done / total
                    print(f"\r  {done/1e6:8.0f} / {total/1e6:.0f} MB ({pct:4.1f}%)",
                          end="", flush=True)
    print()


def extract(zip_path: Path, data_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as z:
        member = next((m for m in z.namelist()
                       if m.lower().endswith(("hydat.sqlite3", ".sqlite3"))), None)
        if member is None:
            raise RuntimeError(f"No .sqlite3 file inside {zip_path.name}")
        print(f"  Extracting {member} ...")
        z.extract(member, data_dir)
    src = data_dir / member
    dest = data_dir / HYDAT_FILENAME
    if src != dest:
        src.replace(dest)
    return dest


def verify(db: Path) -> None:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = {r[0].upper() for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise RuntimeError(f"HYDAT missing required tables: {missing}")
        n = conn.execute("SELECT COUNT(*) FROM STATIONS").fetchone()[0]
        print(f"  Verified: {n:,} stations, all required tables present.")
    finally:
        conn.close()


SHAPE_EXTS = (".zip", ".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx")


def _list_dir(url: str) -> tuple[list[str], list[str]]:
    """Parse an Apache index: returns (file_urls, subdir_urls)."""
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    hrefs = re.findall(r'href="([^"]+)"', r.text, flags=re.I)
    files, dirs = [], []
    for h in hrefs:
        if h.startswith(("?", "#", "/")) or h.startswith(".."):
            continue  # sort links, anchors, absolute/parent links
        full = h if h.startswith("http") else url + h
        if h.endswith("/"):
            dirs.append(full)
        elif h.lower().endswith(SHAPE_EXTS):
            files.append(full)
    return files, dirs


def install_basins(data_dir: Path, index_url: str = BASINS_INDEX,
                   max_depth: int = 2) -> None:
    """Download WSC basin polygons (M1). The ECCC directory nests the actual
    data (e.g. an shp/ subdirectory per the 2024-08-29 release), so we walk
    the index up to two levels, collecting .zip archives and/or bare
    shapefile component sets."""
    base = data_dir / "basins"
    base.mkdir(parents=True, exist_ok=True)
    print(f"Walking basin polygon index: {index_url}")
    found: list[str] = []
    frontier, seen = [(index_url, 0)], set()
    while frontier:
        url, depth = frontier.pop()
        if url in seen:
            continue
        seen.add(url)
        try:
            files, dirs = _list_dir(url)
        except Exception as e:
            print(f"  WARNING: could not list {url}: {e}")
            continue
        print(f"  {url}: {len(files)} data file(s), {len(dirs)} subdir(s)")
        found.extend(files)
        if depth < max_depth:
            # prefer shp/-style dirs first, but walk all
            dirs.sort(key=lambda d: ("shp" not in d.lower(), d))
            frontier.extend((d, depth + 1) for d in dirs)
    if not found:
        raise RuntimeError(
            f"No shapefile data found under {index_url} (walked {len(seen)} "
            "pages). Open the page in a browser, locate the folder holding "
            ".zip or .shp files, and re-run with:\n"
            "  python -m hazardwise.setup_data --basins-only --basins-url <URL>")
    zips = [f for f in found if f.lower().endswith(".zip")]
    comps = [f for f in found if not f.lower().endswith(".zip")]
    print(f"Found {len(zips)} zip(s) and {len(comps)} shapefile component file(s).")
    for url in zips:
        dest = base / Path(url).name
        download(url, dest)
        with zipfile.ZipFile(dest) as zf:
            zf.extractall(base / dest.stem)
    if comps:
        comp_dir = base / "components"
        comp_dir.mkdir(exist_ok=True)
        for url in comps:
            download(url, comp_dir / Path(url).name)
    shp = list(base.rglob("*.shp"))
    gpkg = list(base.rglob("*.gpkg"))
    gjson = list(base.rglob("*.geojson")) + list(base.rglob("*.json"))
    gdb = [d for d in base.rglob("*.gdb") if d.is_dir()]
    n = len(shp) + len(gpkg) + len(gjson) + len(gdb)
    print(f"  Vector data found: {len(shp)} shp, {len(gpkg)} gpkg, "
          f"{len(gjson)} geojson, {len(gdb)} FileGDB under {base}.")
    if n == 0:
        print("  DIAGNOSIS — contents of downloaded archives:")
        for z in sorted(base.glob("*.zip"))[:3]:
            try:
                with zipfile.ZipFile(z) as zf:
                    names = zf.namelist()
                print(f"    {z.name}: {len(names)} entries, first 10:")
                for nm in names[:10]:
                    print(f"      {nm}")
            except Exception as e:
                print(f"    {z.name}: unreadable ({e})")
        raise RuntimeError("No supported vector format found — send the "
                           "console output above for diagnosis.")
    print("  First pipeline run (or the diagnostic CLI) builds the parquet cache.")


def install_egs(data_dir: Path, url: str = EGS_ARCHIVE_URL) -> None:
    """Download the NRCan EGS flood-extent archive (calibration ground truth)."""
    base = data_dir / "egs"
    base.mkdir(parents=True, exist_ok=True)
    dest = base / "EGS_Flood_Product_Archive.gdb.zip"
    download(url, dest)
    print("  Extracting FileGDB ...")
    with zipfile.ZipFile(dest) as zf:
        zf.extractall(base)
    gdbs = [d for d in base.rglob("*.gdb") if d.is_dir()]
    if not gdbs:
        raise RuntimeError("Zip extracted but no .gdb folder found — send "
                           "the console output for diagnosis.")
    print(f"  EGS archive ready: {gdbs[0]}")
    print("  Calibrate with:  python -m hazardwise.calibration.run_calibration"
          f" --extents {base}")


def main(data_dir: Path = DEFAULT_DATA_DIR) -> int:
    print("HazardWise data installer")
    print(f"Data directory: {data_dir}")
    try:
        url, name = find_latest_hydat_url()
        print(f"Latest HYDAT release: {name}")
        zip_path = data_dir / name
        download(url, zip_path)
        db = extract(zip_path, data_dir)
        verify(db)
        (data_dir / "HYDAT_VERSION.txt").write_text(name, encoding="utf-8")
        print("\nDone. You can now run reports, e.g.:")
        print('  python -m hazardwise.pipeline "309B Macleod Trail SW, High River, AB"')
        return 0
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--basins", action="store_true",
                    help="also download WSC basin polygons (M1 gauge linkage)")
    ap.add_argument("--basins-only", action="store_true")
    ap.add_argument("--egs", action="store_true",
                    help="also download the EGS flood-extent archive (M5)")
    ap.add_argument("--egs-only", action="store_true")
    ap.add_argument("--egs-url", default=None)
    ap.add_argument("--basins-url", default=None,
                    help="override the basin polygons index URL (e.g. .../shp/)")
    a = ap.parse_args()
    rc = 0
    if not a.basins_only:
        rc = main()
    if (a.egs or a.egs_only) and rc == 0:
        try:
            install_egs(DEFAULT_DATA_DIR, url=a.egs_url or EGS_ARCHIVE_URL)
        except Exception as e:
            print(f"ERROR downloading EGS archive: {e}", file=sys.stderr); rc = 1
    if a.egs_only and not a.basins_only:
        sys.exit(rc)
    if (a.basins or a.basins_only) and rc == 0:
        try:
            install_basins(DEFAULT_DATA_DIR,
                           index_url=a.basins_url or BASINS_INDEX)
        except Exception as e:
            print(f"ERROR downloading basins: {e}", file=sys.stderr); rc = 1
    sys.exit(rc)
