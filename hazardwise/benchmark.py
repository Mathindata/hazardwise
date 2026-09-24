"""Progress monitor:  python -m hazardwise.benchmark
Appends one row per run to benchmark_history.csv so progress across versions
is a plot, not a memory. Checks four layers:
  T1 panel      — latest reports/validation/validation_summary.csv confusion
  T2 skill      — calibration points re-scored under the LIVE params
  T3 integrity  — every report.json under reports/: no contradictory
                  reach-back (measured + converted for same gauge), calibration
                  provenance line present, AEP within display bounds, no
                  grade-A alongside the display-cap caveat
  T4 params     — live constants + calibration source (edge-pinning is visible)
"""
from __future__ import annotations
import csv, json, sys
from datetime import date
from pathlib import Path
from . import params as P


def panel_metrics(csv_path=Path("reports/validation/validation_summary.csv")):
    if not csv_path.exists():
        return {}
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    st = [r["status"] for r in rows]
    return {"panel_n": len(st), "panel_exact": st.count("MATCH"),
            "panel_obo": st.count("off-by-one"),
            "panel_twostep": sum("TWO-STEP" in x for x in st)}


def skill_metrics(pts_path=Path("calibration/calibration_points.json")):
    if not pts_path.exists():
        return {}
    from .calibration.collect import load_points
    from .calibration.sweep import filter_label_noise, score
    ok = [p for p in load_points(pts_path)
          if not p.get("error") and p.get("gauges")]
    ok, _ = filter_label_noise(ok)
    r = score(ok, b=P.STAGE_EXPONENT, k_bankfull=P.K_BANKFULL,
              hand_bump_m=P.HAND_BUMP_M, high_aep=P.RISK_HIGH_AEP,
              rb_frac=P.REACHBACK_MEDIUM_FRACTION)
    return {"skill_pod": round(r["pod"], 3), "skill_far": round(r["far"], 3),
            "skill_csi": round(r["csi"], 3)}


def integrity_metrics(root=Path("reports")):
    bad_contra = bad_prov = bad_bounds = bad_grade = n = 0
    map_bad = maps_n = 0
    for rj in root.rglob("report.json"):
        try:
            j = json.loads(rj.read_text(encoding="utf-8"))
        except Exception:
            continue
        n += 1
        chain = j["estimate"]["explanation_chain"]
        for g in j["estimate"]["gauges_used"]:
            meas = any("(measured)" in l and g in l for l in chain)
            conv = any("would have risen" in l and g in l for l in chain)
            bad_contra += meas and conv
        bad_prov += not any(l.startswith("Model constants:") for l in chain)
        if (j.get("map") or {}).get("mode") != "susceptibility":
            aep = j["estimate"]["aep_central"]
            bad_bounds += not (0 < aep <= P.AEP_DISPLAY_CAP + 1e-9)
        mi = (j.get("map") or {}).get("integrity") or {}
        maps_n += bool(mi)
        map_bad += sum(int(v) for v in mi.values())
        capped = any("1-in-4-year" in c for c in j["estimate"]["mandatory_caveats"])
        bad_grade += capped and j["estimate"]["grade"] == "A"
    return {"reports_n": n, "maps_n": maps_n,
            "map_integrity_bad": map_bad, "contradictions": bad_contra,
            "missing_provenance": bad_prov, "aep_out_of_bounds": bad_bounds,
            "gradeA_with_cap": bad_grade}


def main() -> int:
    row = {"date": date.today().isoformat(),
           "calibration": P.CALIBRATION_SOURCE[:60],
           "b": P.STAGE_EXPONENT, "k": P.K_BANKFULL,
           "bump": P.HAND_BUMP_M, "high_aep": P.RISK_HIGH_AEP}
    row.update(panel_metrics()); row.update(skill_metrics())
    row.update(integrity_metrics())
    hist = Path("benchmark_history.csv")
    exists = hist.exists()
    with open(hist, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if not exists:
            w.writeheader()
        w.writerow(row)
    for k, v in row.items():
        print(f"  {k}: {v}")
    print(f"-> appended to {hist}")
    bad = (row.get("map_integrity_bad", 0) + row.get("contradictions", 0) + row.get("missing_provenance", 0)
           + row.get("aep_out_of_bounds", 0) + row.get("gradeA_with_cap", 0))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
