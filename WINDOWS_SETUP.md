# HazardWise v0.16 — Windows Setup & Usage
*(satellite-first edition; the gauged/basin method is section 7)*

HazardWise turns a Canadian address into a flood and wildfire risk report
with a line-by-line justification chain, credible intervals, hatched risk
maps, and hard-capped evidence grades. Full model detail: **WHITEPAPER.md**.

## 1. Install (once)
Python 3.11+ from python.org ("Add to PATH" checked). In PowerShell, from
the project folder:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## 2. Verify (offline — no downloads)
```powershell
$env:HW_BOOTSTRAP_N="100"; python -m pytest hazardwise/tests -v
```
Expect **76 passed**: gauged engine, satellite engine (fusion, hygiene,
rendering, integrity), flood sat-mode end-to-end, wildfire Jasper/Ottawa
scenarios.

## 3. Flood risk — satellite-only mode (primary)
```powershell
python -m hazardwise.sat_pipeline "309B Macleod Trail SW, High River, AB"
```
No dataset install: Nominatim, MRDEM windows, and 38 years of JRC water
history are streamed live (~0.3 MB/report; stay online). Output under
`reports_sat\<slug>\`: report.html (chain, index table, hatched map,
Ground-level view links), report.json, flood_map.png.
Reading the map: blue bins + ascending hatch = AEP bands tied to the text;
gray backslash = wide credible interval; magenta dashed = observed extents;
uncertainty circle instead of a pin = centroid-level geocode. Grade is
capped at **C** by construction; no usable occurrence -> **D** screening.

## 4. Wildfire risk (archive-only)
```powershell
python -m hazardwise.fire_pipeline "Connaught Drive, Jasper, AB"
```
NBAC burn history -> regional rate x WUI-distance exposure x slope,
Beta-Binomial on burned-year counts; the graded number is REACH probability
(max within 250 m — fire at the fence is the risk), near-burn bump
disclosed and audit-aware. Warm-ramp hatched map with perimeter reach-back
("the 2024 fire burned to within X m"). Grade capped **C**; until the live
NBAC connector lands (M-FIRE-1) runs return **D screening** — by design.

## 5. Validation panels (labeled addresses, known risk)
```powershell
python -m hazardwise.run_sat_validation     # flood, satellite-only, 12 addresses
python -m hazardwise.run_fire_validation    # wildfire, 12 addresses (Jasper,
                                            # Fort McMurray, Lytton, Ottawa
                                            # Valley, urban-core controls)
```
Summaries: `reports_sat\validation\sat_validation_summary.csv` and
`reports_fire\validation\fire_validation_summary.csv`. Scoring: exact /
off-by-one / TWO-STEP (exit nonzero) / UNDETERMINED counted separately —
a refusal is not a miss. The fire panel is the acceptance gate for
M-FIRE-1: it currently reads all-UNDETERMINED and must turn green when the
NBAC connector lands. Compare flood sat results against the gauged summary:
the per-address gap is the measured value of a gauge.

## 6. Web page + invitation link
```powershell
$env:HW_INVITE_TOKEN="choose-a-long-random-secret"
python -m hazardwise.webapp          # http://localhost:8077
# second window:
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8077
```
Share ONLY `https://<tunnel-host>/invite/<token>`; everything else 403s.
Restart with a new token to revoke all links. Localhost-bound; the tunnel
is the only way in. Demo tool — not a client-delivery channel.

## 7. Gauged / basin method (HYDAT engine — highest grades)
The original engine: WSC gauge selected by BASIN MEMBERSHIP (property inside
the gauge's drainage polygon, smallest catchment first; distance fallback
caps at C), GEV+LP3 frequency with bootstrap CI, measured-stage reach-back,
HAND terrain, satellite map fused as a second evidence channel.
```powershell
python -m hazardwise.setup_data --basins    # HYDAT ~1.1 GB + basin polygons
python -m hazardwise.pipeline "ADDRESS"     # gauged report (grades up to B)
python -m hazardwise.run_validation         # gauged 12-address panel
python -m hazardwise.benchmark              # appends benchmark_history.csv;
                                            # release criterion: all integrity
                                            # counters zero (incl. map + fire)
```
Re-run setup_data quarterly with ECCC releases. A gauged report supersedes a
satellite-only one wherever both exist.

## Known limits (each stamped into reports)
Satellite modes: C-cap; optical/canopy blindness; standing-water
contamination pending JRC recurrence layers; NBAC small-fire undercount;
interim functional forms replaced (not tuned) at Lever 3. Gauged: rating
grade-cap B pending extent calibration; prerelease basin polygons; Quebec
DEH gap. Riverine + wildfire only; pluvial and coastal post-1.0.

## 11. Combined flood + wildfire runs (v0.17)
```powershell
python -m hazardwise.combined_report "309B Macleod Trail SW, High River, AB" "Connaught Drive, Jasper, AB"
python -m hazardwise.combined_report --file hazardwise\validation\panel_addresses.csv
```
Per address: BOTH reports + two maps — blue-hatched flood, red-hatched
wildfire — over a street-map layer at reduced opacity ((c) OpenStreetMap;
falls back to hillshade offline). Output: reports_combined\index.html +
results_summary.csv, scored against expected_flood / expected_fire when
present. `panel_addresses.csv` is the EDITABLE ground-truth store: add rows
or labels there for batch runs.
Visualization flags: --basemap street|hillshade|none, --basemap-opacity,
--opacity, --flood-colors "#5 hexes light->dark", --fire-colors, --no-hatch.

## 12. Dual-hazard single report (v0.18) — one command, both risks
```powershell
python -m hazardwise.dual_report "Connaught Drive, Jasper, AB"
```
One report.html per address: flood section (blue-hatched map + chain +
indices + caveats) AND wildfire section (red-hatched map + its own chain),
shared street-view links and sources, one benchmark-compatible JSON.
Same visualization flags as combined_report. Fire grades require the NBAC
file cached once: download the National Burned Area Composite (any vector
format) from https://cwfis.cfs.nrcan.gc.ca/datamart into
%USERPROFILE%\.hazardwise\nbac\ — the loader auto-detects it, bbox-filters,
and stamps "NBAC loaded: N perimeter-years" into the chain. Without it the
fire section is grade-D screening, disclosed. Rerun the fire panel after
caching NBAC: `python -m hazardwise.run_fire_validation` — that run is the
M-FIRE-1 acceptance gate.

## 13. Installing NBAC — foolproof procedure (supersedes the note in S12)

1. Create the cache folder and put the download in it:
```powershell
mkdir $env:USERPROFILE\.hazardwise\nbac
move $env:USERPROFILE\Downloads\NBAC_1972to2025_*_shp.zip $env:USERPROFILE\.hazardwise\nbac\
```
The zip is fine as-is (do NOT unzip a shapefile zip). Only exception: a zip
containing a .gdb must be extracted so the .gdb folder sits in the cache.

2. VERIFY THE LOADER BEFORE ANY REPORT — this is the step that catches
every known failure:
```powershell
python -m hazardwise.satellite.nbac 52.8734 -118.0806
```
Expected output (Jasper): the file path, "Record length: 54 years", and at
least one line like "2024: NNNN burned pixels". Interpreting anything else:
- "No NBAC file found" -> wrong folder; check the two paths printed.
- "LOADER FAILED / could not read" -> the format needs extracting (gdb) or
  the file is corrupt; the message lists every read attempt it made.
- "Perimeter-years: 0" AT JASPER -> report it as a bug (a burn provably
  exists there); at an urban address 0 is CORRECT and grades Low.

3. Then run one report and look for the chain line
"NBAC loaded: N perimeter-years, 54-yr record":
```powershell
python -m hazardwise.fire_pipeline "Connaught Drive, Jasper, AB"
```

4. Only then run the panel — the M-FIRE-1 acceptance gate:
```powershell
python -m hazardwise.run_fire_validation
```
Urban controls (Toronto/Vancouver/Winnipeg) should now read LOW (zero burns
in a 54-year record is evidence, not a refusal); Jasper, Fort McMurray and
Lytton should read High. UNDETERMINED after this section means the loader
diagnostic in step 2 was skipped — do it first.

Refresh yearly when NRCan posts the new composite; the record end-year is
stamped in every report, so a stale file is visible.
