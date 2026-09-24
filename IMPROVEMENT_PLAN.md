# HazardWise — Systematic Model-Improvement Plan (post v0.20)
Principle: every improvement enters as (guard|evidence|calibration) with an
acceptance test BEFORE the code, and every field failure becomes a
regression test. Nothing merges without `git` history (init the repo FIRST).

## Stage 0 — Foundations (this week)
- `git init`; every patch a commit; benchmark_history.csv per version.
- One `default_geocoder()` factory (three call sites was one bug already).

## Stage A — Trust guards (make wrong answers impossible to emit quietly)
A1 DONE scale-compatibility rejection (>30x gauge/window ratio -> refuse).
   Test: Lac Ste. Anne centroid must raise SCALE-COMPATIBILITY REFUSAL.
A2 DONE centroid geocode caps gauged grade at D (weakest-link).
   Test: any centroid-resolved gauged report asserts grade == "D".
A3 Cross-mode consistency counter (item 3): dual runner compares gauged vs
   satellite class; >=2-class gap -> `cross_mode_conflict` integrity column,
   both demoted to bounds language. Test: synthetic conflicting fixtures.
A4 HAND==0 sanity (item 4): zero margin + unreached scale -> refusal, not a
   51% probability. Test: flat-DEM fixture must refuse, not grade.
A5 Panel labels upgraded: `label_source` column; flood labels from
   provincial floodway/fringe mapping, fire from provincial threat ratings
   (e.g. BC PSTA). Acceptance: every High/Low label carries a citation.

## Stage B — Evidence levers (fix misses with data, not thresholds)
B1 M-FIRE-2 fuel grids + FWI (fixes WUI Medium->Low panel rows; feeds the
   vegetation context panel). Test: Canmore/Prince George reach Medium.
B2 25 km regional-rate ring for fire (design-doc spec vs 3 km shortcut).
   Test: rate at Canmore > rate at Toronto by >10x.
B3 M-SAT-2 Sentinel-1 events (fixes Minden/Constance Bay two-steps).
   Acceptance: IoU>=0.6 vs EGS; both two-steps resolve or become refusals.
B4 JRC recurrence/seasonality + lake mask (Kelowna/Bracebridge limnic
   contamination). Test: lakeshore-with-no-river fixture stays <= Medium.
B5 Blindness index (canopy/channel-width) -> refuse instead of confident
   Low. Test: Minden becomes Undetermined until B3 grades it.
B6 ATS legal-land geocoder (computable AB grid) ahead of centroid fallback.

## Stage C — Calibration (Lever 3, last)
Replace interim forms (occ_to_annual, fire c/exposure) with a learned
monotone model on EGS+NBAC labels. Gate: held-out POD>=0.80 FAR<=0.40
CSI>=0.50, no constant at a grid edge; panels zero two-step with A5 labels.

## Standing test protocol per change
unit -> scenario fixture -> labeled panels (both) -> benchmark integrity
all-zeros -> cross-mode conflict count -> commit with panel CSVs attached.
