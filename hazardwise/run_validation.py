"""Validation harness:  python -m hazardwise.run_validation

Runs the labeled AB/ON/BC address set end to end, writes one report folder per
address under reports/validation/, and a summary CSV comparing predicted vs
expected risk. Exits nonzero if more than the allowed number of two-step
misclassifications occur (High predicted Low or vice versa), which is the
failure mode that matters for the product.
"""

from __future__ import annotations

import csv
import sys
import traceback
from pathlib import Path

import yaml

from .pipeline import run_report

VALIDATION_FILE = Path(__file__).parent / "validation" / "addresses.yaml"
OUT_ROOT = Path("reports") / "validation"
MAX_TWO_STEP_ERRORS = 0
_STEP = {"Low": 0, "Medium": 1, "High": 2}


def main() -> int:
    spec = yaml.safe_load(VALIDATION_FILE.read_text(encoding="utf-8"))
    rows, two_step = [], 0
    for entry in spec["addresses"]:
        addr, expected = entry["address"], entry["expected_risk"]
        print(f"\n=== {addr}  (expected: {expected}) ===")
        try:
            path, predicted, est = run_report(addr, out_root=OUT_ROOT)
            diff = abs(_STEP[predicted] - _STEP[expected])
            two_step += int(diff == 2)
            status = ["MATCH", "off-by-one", "TWO-STEP MISS"][diff]
            print(f"  predicted: {predicted} | grade {est.grade} | "
                  f"AEP {est.aep_central:.2%} | {status}")
            print(f"  report: {path}")
            rows.append([addr, expected, predicted, est.grade,
                         f"{est.aep_central:.4f}", status, str(path)])
        except Exception as e:
            print(f"  FAILED: {e}")
            traceback.print_exc(limit=1)
            rows.append([addr, expected, "ERROR", "", "", str(e), ""])

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    summary = OUT_ROOT / "validation_summary.csv"
    with open(summary, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["address", "expected", "predicted", "grade", "aep",
                    "status", "report_path"])
        w.writerows(rows)

    import zipfile
    bundle = OUT_ROOT / "misses_bundle.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(summary, "validation_summary.csv")
        for r in rows:
            if r[5] != "MATCH" and r[6]:
                rj = Path(r[6]).parent / "report.json"
                if rj.exists():
                    zf.write(rj, f"{Path(r[6]).parent.name}_report.json")
    print(f"Diagnostics bundle (upload this one file): {bundle}")

    n = len(rows)
    matches = sum(1 for r in rows if r[5] == "MATCH")
    errors = sum(1 for r in rows if r[2] == "ERROR")
    print(f"\n{'='*60}\nSummary: {matches}/{n} exact, {two_step} two-step misses, "
          f"{errors} pipeline errors\nCSV: {summary}")
    return 1 if (two_step > MAX_TWO_STEP_ERRORS or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
