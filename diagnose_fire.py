"""HazardWise fire-pipeline diagnostic. Run from the project folder:

    python diagnose_fire.py            (add --lat --lon to test elsewhere)

Checks every stage independently and prints a VERDICT naming the broken
link: stale code, missing/unreadable NBAC file, CRS/bbox mismatch, YEAR
field, or pipeline wiring. Paste the full output when reporting a bug."""
import argparse, json, os, sys, traceback
from pathlib import Path

JASPER = (52.8734, -118.0806)
OK, BAD = "  [ok] ", "  [!!] "

def stage(title):
    print(f"\n=== {title} ===")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=JASPER[0])
    ap.add_argument("--lon", type=float, default=JASPER[1])
    a = ap.parse_args()
    verdicts = []

    stage("0. Code version markers")
    try:
        import hazardwise
        root = Path(hazardwise.__file__).parent
        nbac_src = (root / "satellite" / "nbac.py").read_text()
        wf_src = (root / "satellite" / "wildfire.py").read_text()
        print(OK + f"hazardwise at {root}")
        if "EPSG:3978" not in nbac_src:
            print(BAD + "nbac.py has NO CRS cascade -> you are running the "
                  "OLD code. Re-extract the latest hazardwise_v0_18.zip "
                  "OVER this folder (this alone explains Undetermined at "
                  "Jasper).")
            verdicts.append("STALE CODE: update nbac.py from the latest zip")
        else:
            print(OK + "nbac.py: CRS cascade present")
        if "evidence of absence" in wf_src or "n_record_years <= 0:   #" in wf_src:
            print(OK + "wildfire.py: empty-window fix present")
        else:
            print(BAD + "wildfire.py: empty-window fix MISSING (old code)")
            verdicts.append("STALE CODE: update wildfire.py from latest zip")
    except Exception as e:
        print(BAD + f"cannot import hazardwise: {e}"); return 1

    stage("1. NBAC cache discovery")
    from hazardwise.satellite.nbac import nbac_dir, _find_vector
    d = nbac_dir()
    print(f"  dir: {d}  (HW_NBAC_DIR={os.environ.get('HW_NBAC_DIR','unset')})")
    src = _find_vector()
    if src is None:
        print(BAD + "no *.gpkg/*.shp/*.gdb/*.zip found in that folder")
        verdicts.append(f"FILE NOT FOUND: put the NBAC download in {d}")
    else:
        print(OK + f"found {src.name}  ({src.stat().st_size/1e6:.0f} MB)")

    if src is not None:
        stage("2. Raw readability / CRS / fields")
        import geopandas as gpd
        for path in [src, f"zip://{src}"] if str(src).endswith(".zip") else [src]:
            try:
                head = gpd.read_file(path, rows=5)
                print(OK + f"read {Path(str(path)).name}: crs={head.crs}, "
                      f"columns={list(head.columns)[:8]}")
                yf = [c for c in head.columns if "YEAR" in c.upper()]
                print((OK if yf else BAD) + f"year-like fields: {yf}")
                if not yf:
                    verdicts.append("YEAR FIELD: none found — send the "
                                    "column list above; one-line fix in "
                                    "nbac.py")
                break
            except Exception as e:
                print(BAD + f"{Path(str(path)).name}: {type(e).__name__}: {e}")
                verdicts.append("UNREADABLE FILE: if the zip holds a .gdb, "
                                "extract it into the folder")

        stage("3. Window query at each CRS (the historical bug)")
        try:
            import pyproj
            from hazardwise.satellite.aoi import AOI
            aoi = AOI(a.lat, a.lon)
            w, s_, e, n = aoi.bounds_lonlat()
            for crs in ("EPSG:3978", "EPSG:4326"):
                try:
                    if crs != "EPSG:4326":
                        tr = pyproj.Transformer.from_crs("EPSG:4326", crs,
                                                         always_xy=True)
                        xs, ys = tr.transform([w, e], [s_, n])
                        bbox = (min(xs), min(ys), max(xs), max(ys))
                    else:
                        bbox = (w, s_, e, n)
                    g = gpd.read_file(src, bbox=bbox)
                    print(f"  bbox in {crs}: {len(g)} features")
                except Exception as ex:
                    print(BAD + f"{crs}: {type(ex).__name__}: {ex}")
        except Exception:
            traceback.print_exc(limit=1)

        stage("4. load_layers() — the loader as the pipeline calls it")
        try:
            from hazardwise.satellite.nbac import load_layers
            layers, n_rec = load_layers(a.lat, a.lon, 1.5, 300, 10.0)
            print(OK + f"record {n_rec} yr; {len(layers)} perimeter-years")
            for m, y in layers:
                print(f"    {y}: {int(m.sum())} burned pixels")
            if not layers and abs(a.lat - JASPER[0]) < 0.01:
                print(BAD + "ZERO at Jasper: stage-3 counts above show "
                      "whether the file or the bbox is at fault")
                verdicts.append("LOADER selects nothing at Jasper — send "
                                "stage 2+3 output")
        except Exception as ex:
            print(BAD + f"{type(ex).__name__}: {ex}")
            verdicts.append(f"LOADER RAISED: {ex}")

    stage("5. End-to-end report (live DEM; needs internet)")
    try:
        from hazardwise.fire_pipeline import run_fire_report
        path, risk, p = run_fire_report("diagnostic site",
                                        coords=(a.lat, a.lon),
                                        out_root=Path("reports_fire_diag"))
        j = json.loads((path.parent / "report.json").read_text())
        print(f"  risk={risk} prob={p:.2%} grade={j['estimate']['grade']}")
        for line in j["estimate"]["explanation_chain"]:
            if "NBAC" in line:
                print("  chain: " + line)
        for c in j["estimate"]["mandatory_caveats"]:
            if "NBAC" in c:
                print("  caveat: " + c)
                verdicts.append("PIPELINE CAVEAT: " + c)
    except Exception as ex:
        print(BAD + f"{type(ex).__name__}: {ex} (offline? geocode?)")

    stage("VERDICT")
    if not verdicts:
        print(OK + "every stage passed — rerun the panel: "
              "python -m hazardwise.run_fire_validation")
    else:
        for v in dict.fromkeys(verdicts):
            print(BAD + v)
    return 0

if __name__ == "__main__":
    sys.exit(main())
