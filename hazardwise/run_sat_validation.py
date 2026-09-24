"""Satellite-only validation harness:  python -m hazardwise.run_sat_validation

Runs the SAME 12-address labeled panel as run_validation, but through
sat_pipeline (no gauge, no HYDAT): Nominatim + MRDEM windows + streamed JRC
occurrence, all live web services — the machine must be online. Writes one
report folder per address under reports_sat/validation/ plus
sat_validation_summary.csv.

Read the results against the right yardstick: this mode is a C-capped
SCREENING product. The success criterion is the same one that matters for
the gauged pipeline — zero two-step misses — but off-by-one is expected to
be worse than the gauged panel (that gap IS the measured value of a gauge).
'Undetermined' (grade D, no usable occurrence in the window) is reported as
its own status, not scored as a miss: no claim was made, so none can be wrong.
Nominatim is rate-limited to 1 req/s; the run takes a few minutes.
"""
from __future__ import annotations

import csv
import sys
import time
import traceback
from pathlib import Path

import yaml

from .sat_pipeline import run_sat_report
from .satellite.occurrence import JrcOccurrence

VALIDATION_FILE = Path(__file__).parent / "validation" / "addresses.yaml"
OUT_ROOT = Path("reports_sat") / "validation"
MAX_TWO_STEP_ERRORS = 0
_STEP = {"Low": 0, "Medium": 1, "High": 2}


def main() -> int:
    spec = yaml.safe_load(VALIDATION_FILE.read_text(encoding="utf-8"))
    rows, two_step, undet = [], 0, 0
    occ_source = JrcOccurrence()
    for entry in spec["addresses"]:
        addr, expected = entry["address"], entry["expected_risk"]
        print(f"\n=== {addr}  (expected: {expected}) ===")
        try:
            path, predicted, aep = run_sat_report(
                addr, out_root=OUT_ROOT, occ_source=occ_source)
            if predicted == "Undetermined":
                undet += 1
                status = "UNDETERMINED (grade D screening)"
                print(f"  {status}")
            else:
                diff = abs(_STEP[predicted] - _STEP[expected])
                two_step += int(diff == 2)
                status = ["MATCH", "off-by-one", "TWO-STEP MISS"][diff]
                print(f"  predicted: {predicted} | AEP {aep:.2%} | {status}")
            print(f"  report: {path}")
            rows.append([addr, expected, predicted, f"{aep:.4f}",
                         status, str(path)])
        except Exception as e:
            print(f"  FAILED: {e}")
            traceback.print_exc(limit=1)
            rows.append([addr, expected, "ERROR", "", str(e), ""])
        time.sleep(1.0)          # Nominatim rate limit

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    summary = OUT_ROOT / "sat_validation_summary.csv"
    with open(summary, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["address", "expected", "predicted", "aep",
                    "status", "report_path"])
        w.writerows(rows)

    n = len(rows)
    matches = sum("MATCH" == r[4] for r in rows)
    obo = sum("off-by-one" == r[4] for r in rows)
    errors = sum("ERROR" == r[2] for r in rows)
    print(f"\n=== SATELLITE-ONLY PANEL: {matches} exact, {obo} off-by-one, "
          f"{two_step} two-step, {undet} undetermined, {errors} errors "
          f"of {n} ===")
    print(f"Summary: {summary}")
    print("Compare against reports/validation/validation_summary.csv — the "
          "per-address gap between the two harnesses is the measured value "
          "of gauge data, and the satellite-only misses show exactly where "
          "the occurrence-vs-HAND transfer breaks (read those evidence "
          "chains first).")
    return 1 if two_step > MAX_TWO_STEP_ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())
