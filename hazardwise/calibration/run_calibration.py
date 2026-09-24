"""M5 calibration runner:  python -m hazardwise.calibration.run_calibration
       --extents PATH/to/egs_downloads [--n-wet 40] [--n-dry 40]

1. Loads EGS flood-extent polygons (any vector format) from --extents
2. Samples wet/dry labeled points per event
3. Collects ingredients per point (slow part; resumable via points.json)
4. Sweeps the screening constants; reports train AND held-out test skill
5. Writes calibration_points.json, skill_table.csv, best_params.json
"""
from __future__ import annotations

import argparse, csv, json, sys
from dataclasses import asdict
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extents", required=True)
    ap.add_argument("--n-wet", type=int, default=40)
    ap.add_argument("--n-dry", type=int, default=40)
    ap.add_argument("--out", default="calibration")
    ap.add_argument("--events", default=None,
                    help='comma-separated substrings, e.g. "2013,2017,2019,2021"')
    ap.add_argument("--max-events", type=int, default=8)
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    pts_file = out / "calibration_points.json"

    from .collect import _TerrainCache, collect_point, load_points, save_points
    from .extents import load_extents, sample_points
    from .sweep import sweep

    if pts_file.exists():
        print(f"Resuming from {pts_file}")
        data = load_points(pts_file)
    else:
        from ..data.hydat import HydatDB
        from ..spatial.basins import BasinIndex
        from ..terrain.dem import CogDem
        filt = [x.strip() for x in args.events.split(",")] if args.events else None
        ext = load_extents(Path(args.extents), event_filter=filt)
        top = (ext.assign(a=ext.geometry.area).groupby("event")["a"].sum()
               .nlargest(args.max_events).index)
        ext = ext[ext["event"].isin(top)]
        print(f"Loaded {len(ext)} flood polygons across "
              f"{ext['event'].nunique()} event file(s)")
        samples = sample_points(ext, args.n_wet, args.n_dry)
        print(f"Sampled {len(samples)} labeled points; collecting ingredients "
              "(DEM windows + gauge fits — this is the slow part)...")
        hydat = HydatDB()
        try:
            basins = BasinIndex()
        except Exception as e:
            print(f"WARNING: basins unavailable ({e}); proximity fallback")
            basins = None
        tcache = _TerrainCache(CogDem())
        points = []
        for i, s in enumerate(samples, 1):
            points.append(collect_point(s.lat, s.lon, s.flooded, s.event,
                                        hydat, basins, tcache))
            if i % 10 == 0 or i == len(samples):
                print(f"  {i}/{len(samples)} "
                      f"({sum(1 for p in points if p.error)} errors)")
                save_points(points, pts_file)
        hydat.close()
        data = load_points(pts_file)

    ok = [p for p in data if not p.get("error") and p.get("gauges")]
    print(f"Usable points: {len(ok)}/{len(data)}")
    best, train_row, test_row, rows = sweep(ok)
    with open(out / "skill_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    (out / "best_params.json").write_text(json.dumps(
        {"best": best, "train": train_row, "test_heldout": test_row},
        indent=2), encoding="utf-8")
    print(f"\nBest constants (chosen on train events): {best}")
    print(f"Train skill:     POD {train_row['pod']:.2f}  "
          f"FAR {train_row['far']:.2f}  CSI {train_row['csi']:.2f}")
    print(f"HELD-OUT skill:  POD {test_row['pod']:.2f}  "
          f"FAR {test_row['far']:.2f}  CSI {test_row['csi']:.2f}   "
          "<- this is the number that goes in the validation report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
