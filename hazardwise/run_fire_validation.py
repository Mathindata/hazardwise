"""Wildfire validation harness:  python -m hazardwise.run_fire_validation

Runs the labeled wildfire panel (validation/fire_addresses.yaml) through
fire_pipeline. Same scoring discipline as the flood panels: exact /
off-by-one / TWO-STEP (exit nonzero) / UNDETERMINED counted separately.

HONESTY NOTE: until the live NBAC/fuel connectors land (M-FIRE-1), every
address returns UNDETERMINED — the model refuses to grade without burn
history, by design. Do not read an all-UNDETERMINED run as failure: this
panel is the acceptance gate that M-FIRE-1 must turn green."""
from __future__ import annotations
import csv, sys, time, traceback
from pathlib import Path
import yaml
from .fire_pipeline import run_fire_report

VALIDATION_FILE = Path(__file__).parent / "validation" / "fire_addresses.yaml"
OUT_ROOT = Path("reports_fire") / "validation"
_STEP = {"Low": 0, "Medium": 1, "High": 2}

def main() -> int:
    spec = yaml.safe_load(VALIDATION_FILE.read_text(encoding="utf-8"))
    rows, two_step, undet = [], 0, 0
    for e in spec["addresses"]:
        addr, expected = e["address"], e["expected_risk"]
        print(f"\n=== {addr}  (expected: {expected} — {e.get('note','')}) ===")
        try:
            path, predicted, p = run_fire_report(addr, out_root=OUT_ROOT)
            if predicted == "Undetermined":
                undet += 1; status = "UNDETERMINED (grade D screening)"
            else:
                diff = abs(_STEP[predicted] - _STEP[expected])
                two_step += int(diff == 2)
                status = ["MATCH", "off-by-one", "TWO-STEP MISS"][diff]
            print(f"  predicted: {predicted} | burn prob {p:.2%} | {status}")
            rows.append([addr, expected, predicted, f"{p:.4f}", status,
                         str(path)])
        except Exception as ex:
            traceback.print_exc(limit=1)
            rows.append([addr, expected, "ERROR", "", str(ex), ""])
        time.sleep(1.0)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    out = OUT_ROOT / "fire_validation_summary.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["address", "expected", "predicted", "burn_prob",
                    "status", "report_path"])
        w.writerows(rows)
    m = sum(r[4] == "MATCH" for r in rows)
    obo = sum(r[4] == "off-by-one" for r in rows)
    print(f"\n=== FIRE PANEL: {m} exact, {obo} off-by-one, {two_step} "
          f"two-step, {undet} undetermined of {len(rows)} ===\n{out}")
    return 1 if two_step > 0 else 0

if __name__ == "__main__":
    sys.exit(main())
