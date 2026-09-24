"""Combined flood + wildfire reporting, one or many addresses (v0.17).

    python -m hazardwise.combined_report "ADDR1" "ADDR2" ...
    python -m hazardwise.combined_report --file hazardwise/validation/panel_addresses.csv

Per address: satellite-only flood report (blue-hatched map) AND wildfire
report (red-hatched map), both optionally over a street-map layer at reduced
opacity, plus a combined index.html and results_summary.csv (with scoring
against expected_* columns when the input file provides them — the editable
ground-truth store).

Visualization options (apply to both maps unless prefixed):
  --basemap {street,hillshade,none}   backdrop layer (default street)
  --basemap-opacity 0.5               street layer alpha
  --opacity 0.6                       hazard layer alpha
  --flood-colors "#hex,#hex,..." (5)  override blue ramp (light->dark)
  --fire-colors  "#hex,#hex,..." (5)  override red ramp  (light->dark)
  --no-hatch                          disable risk-bin cross-hatching
"""
from __future__ import annotations
import argparse, csv, sys
from pathlib import Path
from .sat_pipeline import run_sat_report
from .fire_pipeline import run_fire_report
from .satellite.occurrence import JrcOccurrence
from .reporting.sat_report import slugify

_STEP = {"Low": 0, "Medium": 1, "High": 2}

def load_panel(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("address", "").strip()]

def _score(pred, expected):
    if not expected or pred in ("Undetermined", "ERROR"):
        return pred if pred != "ERROR" else "ERROR"
    d = abs(_STEP.get(pred, 0) - _STEP.get(expected, 0))
    return ["MATCH", "off-by-one", "TWO-STEP"][d]

def _viz(args, colors_arg):
    rk = {"layer_alpha": args.opacity,
          "basemap_alpha": args.basemap_opacity,
          "street_basemap": args.basemap in ("street", "aerial"),
          "basemap_style": args.basemap}
    if args.basemap == "none":
        rk["basemap_img"] = None
    if colors_arg:
        pal = tuple(c.strip() for c in colors_arg.split(","))
        if len(pal) != 5:
            sys.exit("color overrides need exactly 5 comma-separated hexes")
        rk["colors"] = pal[::-1]        # user gives light->dark; params store dark->light
    if args.no_hatch:
        rk["hatches"] = ()
    return rk

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("addresses", nargs="*")
    ap.add_argument("--file", type=Path,
                    help="CSV with address[,expected_flood,expected_fire,note]")
    ap.add_argument("--out", type=Path, default=Path("reports_combined"))
    ap.add_argument("--basemap", choices=["street", "aerial", "hillshade", "none"],
                    default="street")
    ap.add_argument("--basemap-opacity", type=float, default=0.5)
    ap.add_argument("--opacity", type=float, default=0.6)
    ap.add_argument("--flood-colors"); ap.add_argument("--fire-colors")
    ap.add_argument("--no-hatch", action="store_true")
    a = ap.parse_args()
    entries = ([{"address": x, "expected_flood": "", "expected_fire": ""}
                for x in a.addresses]
               + (load_panel(a.file) if a.file else []))
    if not entries:
        ap.error("give addresses or --file")
    occ = JrcOccurrence()
    rows, cards = [], []
    for e in entries:
        addr = e["address"]
        print(f"\n=== {addr} ===")
        fr = fa = "ERROR"; fp = ff = 0.0; fpath = fipath = ""
        try:
            p1, fr, fp = run_sat_report(addr, out_root=a.out / "flood",
                                        occ_source=occ,
                                        render_kwargs=_viz(a, a.flood_colors))
            fpath = str(p1)
        except Exception as ex:
            print("  flood FAILED:", ex)
        try:
            p2, fa, ff = run_fire_report(addr, out_root=a.out / "fire",
                                         render_kwargs=_viz(a, a.fire_colors))
            fipath = str(p2)
        except Exception as ex:
            print("  fire FAILED:", ex)
        rows.append([addr, fr, f"{fp:.4f}",
                     _score(fr, e.get("expected_flood", "")),
                     fa, f"{ff:.4f}",
                     _score(fa, e.get("expected_fire", "")), fpath, fipath])
        cards.append(
            f"<tr><td>{addr}</td><td>{fr} ({fp:.2%})</td>"
            f"<td>{fa} ({ff:.2%})</td>"
            f'<td><a href="flood/{slugify(addr)}/report.html">flood</a> | '
            f'<a href="fire/{slugify(addr)}/report.html">fire</a></td></tr>')
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / "results_summary.csv", "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["address", "flood_risk", "flood_aep", "flood_score",
                    "fire_risk", "fire_burn_prob", "fire_score",
                    "flood_report", "fire_report"])
        w.writerows(rows)
    (a.out / "index.html").write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "body{font-family:Segoe UI,sans-serif;max-width:900px;margin:2rem "
        "auto}td,th{border:1px solid #ccc;padding:.4rem .7rem}table{border-"
        "collapse:collapse}</style></head><body><h1>HazardWise combined "
        "flood + wildfire results</h1><table><tr><th>Address</th><th>Flood"
        "</th><th>Wildfire</th><th>Reports</th></tr>"
        + "".join(cards) + "</table></body></html>", encoding="utf-8")
    print(f"\nSummary: {a.out/'results_summary.csv'}\n"
          f"Index:   {a.out/'index.html'}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
