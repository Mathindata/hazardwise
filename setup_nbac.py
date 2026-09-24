"""One-shot NBAC setup:  python setup_nbac.py

Resolves the cache location (including a stale HW_NBAC_DIR), finds an
already-downloaded NBAC file anywhere obvious and MOVES it into the active
cache, downloads the latest composite from CWFIS if nothing is found, then
verifies the loader at Jasper. Exit 0 = fire pipeline is ready."""
import os, re, shutil, sys, urllib.request
from pathlib import Path

INDEX = "https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/"
UA = {"User-Agent": "HazardWise-setup/0.18"}
PAT = re.compile(r"NBAC_\d{4}to\d{4}_\d{8}_shp\.zip")

def active_cache() -> Path:
    env = os.environ.get("HW_NBAC_DIR")
    default = Path.home() / ".hazardwise" / "nbac"
    if env and Path(env) != default:
        print(f"[note] HW_NBAC_DIR is set to {env} — the loader uses THAT, "
              f"not {default}.")
        print("       To go back to the default permanently:")
        print("       [Environment]::SetEnvironmentVariable("
              "'HW_NBAC_DIR', $null, 'User')   (then open a NEW PowerShell)")
        return Path(env)
    return default

def find_existing(cache: Path):
    candidates = [cache, Path.home() / ".hazardwise" / "nbac",
                  Path.home() / "Downloads"]
    if os.environ.get("HW_NBAC_DIR"):
        candidates.insert(0, Path(os.environ["HW_NBAC_DIR"]))
    for d in dict.fromkeys(candidates):
        if d.exists():
            for pat in ("NBAC*_shp.zip", "NBAC*.gpkg", "NBAC*.zip",
                        "nbac*.zip"):
                hits = sorted(d.glob(pat))
                if hits:
                    return hits[0], d
    return None, None

def download_latest(cache: Path) -> Path:
    print(f"Fetching index {INDEX} ...")
    with urllib.request.urlopen(
            urllib.request.Request(INDEX, headers=UA), timeout=30) as r:
        names = sorted(set(PAT.findall(r.read().decode("utf-8", "ignore"))))
    if not names:
        sys.exit("Could not find an NBAC_*_shp.zip in the index — download "
                 f"manually from {INDEX} into {cache}")
    name = names[-1]
    dest = cache / name
    print(f"Downloading {name} (several hundred MB) ...")
    with urllib.request.urlopen(
            urllib.request.Request(INDEX + name, headers=UA),
            timeout=60) as r, open(dest, "wb") as f:
        done = 0
        while True:
            chunk = r.read(1 << 22)
            if not chunk:
                break
            f.write(chunk); done += len(chunk)
            print(f"\r  {done/1e6:,.0f} MB", end="", flush=True)
    print("\nSaved:", dest)
    return dest

def main() -> int:
    cache = active_cache()
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        default = Path.home() / ".hazardwise" / "nbac"
        print(f"[!!] Cannot create {cache} ({e}) — the HW_NBAC_DIR path "
              f"does not exist on this machine (no such drive/folder).")
        print(f"     Falling back to the default: {default}")
        print("     Clear the stale variable so the PIPELINE agrees:")
        print("     Remove-Item Env:HW_NBAC_DIR")
        print("     [Environment]::SetEnvironmentVariable('HW_NBAC_DIR', "
              "$null, 'User')")
        os.environ.pop("HW_NBAC_DIR", None)   # this process only
        cache = default
        cache.mkdir(parents=True, exist_ok=True)
    print("Active cache:", cache)
    found, where = find_existing(cache)
    if found and where != cache:
        print(f"Found existing file at {found} — moving into the cache.")
        found = Path(shutil.move(str(found), cache / found.name))
    elif found:
        print("Already in cache:", found.name)
    else:
        found = download_latest(cache)
    print("\nVerifying loader at Jasper ...")
    from hazardwise.satellite.nbac import load_layers
    try:
        layers, n_rec = load_layers(52.8734, -118.0806, 1.5, 300, 10.0)
    except Exception as e:
        print("LOADER FAILED:", e)
        print("Run  python diagnose_fire.py  and report stages 2-3.")
        return 1
    print(f"Record: {n_rec} years; perimeter-years at Jasper: {len(layers)}")
    for m, y in layers:
        print(f"  {y}: {int(m.sum())} burned pixels")
    if not layers:
        print("Loader ran but found no burns at Jasper — run "
              "python diagnose_fire.py and report stages 2-3.")
        return 1
    print("\nREADY. Next:  python -m hazardwise.run_fire_validation")
    return 0

if __name__ == "__main__":
    sys.exit(main())
