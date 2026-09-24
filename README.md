# HazardWise v0.10 — min-HAND semantics (the misses bundle diagnosis)
HazardWise is a property risk assessment system for flood and wildfire hazards in Canada. It combines hydrometric records (HYDAT), terrain analysis (HAND on MRDEM-30 elevation data), and wildfire burn history (NBAC) into a single pipeline, producing per-property risk reports for both hazards together. Flood risk is estimated using a statistical core built on extreme value methods (GEV and Log-Pearson III), with a confidence grading system (A through D) that reflects how much data is actually available for a given location. The system is built around two principles: every number in a report should trace back to an evidence chain the user can inspect, and uncertainty is reported honestly with credible intervals rather than hidden behind a single confident-looking score.

The misses_bundle finally exposed the true common cause of the persistent
Ontario/BC misses — NOT the rating curve alone, but a HAND semantics error:
Kamloops read "17.3 m above the nearest channel" because Peterson Creek's
incised gorge sat 480 m from downtown and qualified on upstream area, while
the Thompson — the river that floods the city — sat 900 m away at ~5 m. Every
threshold went astronomical, every gauge clamped to 'unresolvable', every AEP
pinned to the floor. Same signature at Minden (15.8 m) and Bracebridge.

Fixes:
1. **min-HAND within radius.** A property is threatened by whichever
   qualifying water body it stands LEAST above: hand_at_scale now takes the
   channel cell with the highest elevation within 1.5 km, not the nearest
   cell. The radius bound prevents upstream channel slope from spuriously
   zeroing real standing. Regression: gorge-vs-river synthetic.
2. **Locality-centroid honesty.** Constance Bay geocoded to the community
   centre on a ridge 16 m above the Ottawa; the flooded homes are on the
   shore. Stage-3 geocodes now carry a mandatory caveat that shoreline and
   ridge properties within the community can have OPPOSITE risk and this
   report cannot distinguish them — a data limitation stated as one, not a
   silent Low. (Property-level resolution there requires parcel/address-point
   data — M4.)

Note for calibration: points collected with v0.9 used nearest-cell HAND;
delete calibration_points.json and re-collect under v0.10 before sweeping.
54 tests.

---

# HazardWise v0.9 — the M5 calibration harness

v0.8 batch confirmed the plateau: 7/12 exact, same 3 misses, all caused by the
uncalibrated rating curve (the cottage report now prints the disagreement
itself: measured 2013 rise 2.1 m vs flow-converted 5.1 m at the same gauge).
The 12-address set is exhausted as a debugging tool. v0.9 ships the machinery
that replaces my textbook constants with fitted ones:

**hazardwise/calibration/** —
- extents.py: loads NRCan EGS satellite flood-extent polygons ("Floods in
  Canada – Archive", open.canada.ca, events since 2011; any vector format)
  and samples labeled points: WET inside the flood, DRY in a near-miss ring
  0.3–3 km outside it.
- collect.py: one expensive pass per point (gauge selection, DEM window with
  a shared terrain cache, central GEV/LP3 fits, measured stage rises) stored
  as ingredients — thousands of constant combinations then re-score instantly.
- sweep.py: grids the five remaining textbook constants (stage exponent,
  bankfull coefficient, HAND bump, AEP bands, reach-back fraction), scores
  POD/FAR/CSI, and — critically — CHOOSES constants on training events and
  REPORTS skill on held-out events it never saw.
- run_calibration.py: one command, resumable, writes skill_table.csv and
  best_params.json.

Usage (on the PC):
  1. Download 3–5 event zips from Floods in Canada – Archive
     (2013 AB, 2017/2019 Ottawa R., 2019 Muskoka, 2021 BC) into egs/
  2. python -m hazardwise.calibration.run_calibration --extents egs
  3. Send calibration/best_params.json + skill_table.csv

Also: run_validation now writes misses_bundle.zip (summary + report.json of
every non-match) — one file to upload per batch instead of hand-picking.

50 tests. The held-out CSI/POD/FAR from step 3 is the first draft of the 1.0
validation claim.

---

# HazardWise v0.8 — measured-stage reach-back (v0.7.1 batch)

v0.7.1 batch (first with WORKING basin selection): 7/12 exact, 2 off-by-one,
3 two-step misses. High River recovered via the mainstem fix. The survivors
(Minden, Constance Bay, Grand Forks) are now provably a RATING-CURVE problem:
Grand Forks 2018 flooded downtown at ~3.5x Q2, but the 0.4-exponent rating
converts that to ~2.3 m of rise — below downtown's HAND — so flow-based
reach-back stayed silent even with the flood in the record.

v0.8 attacks this with data instead of tuning:

1. **Measured-stage reach-back.** HYDAT stores annual maximum WATER LEVELS
   (DATA_TYPE='H'). The observed rise of the biggest flood above the median
   annual peak level is direct physical evidence: no rating curve, no
   hydraulic-geometry constants. When measured rise >= property HAND, the
   class floors at High with 'measured, not modeled' language. Takes
   precedence over the flow-converted check (which remains as fallback for
   gauges without level data).
2. **Float-dust ties.** A regulated structure must now be >2% smaller in
   basin area to win the local pick — the Little Bow Canal (1,954.9 km²) had
   been 'beating' the Highwood (1,955.3 km²) on float noise, twice, in the
   field.

Known observation, deliberately not patched: the mainstem guard admitted the
Bow BELOW BASSANO DAM (~100 km downstream) for High River. The class was
right and the 2013 reach-back legitimate, but the physically ideal citation
is the Highwood at High River; tightening the distance guard is a calibration
question for M5, not another anecdotal constant change. 46 tests.

---

# HazardWise v0.7.1 — hydraulic relevance guard (v0.7 batch hotfix)

v0.7 field failure, from the High River report: 'largest containing basin'
selected the NELSON RIVER at a Manitoba generating station 1,300 km away —
High River genuinely lies inside the Nelson's 1.3M km² Hudson Bay drainage,
so the selection was hydrologically true and hydraulically absurd. It
displaced the Highwood gauge and fed reach-back with the Nelson's flows.

Fixes:
1. The mainstem pick now requires the gauge station to be within 150 km of
   the property (hydraulic relevance guard). Distant megabasins are excluded.
2. Regulated structures lose area TIES for the local pick (the Little Bow
   Canal had been winning its 1,955 km² tie against the Highwood).
3. Restored a selection loop destroyed during the v0.7 edit that silently
   sent EVERY address to the proximity fallback (this also explains the
   v0.7 batch's persistent misses at Minden/Constance Bay/Grand Forks —
   basin selection never actually ran in that batch).

Regression: a 1.3M km² basin with a station 1,300 km away must never be
selected while a nearby same-size river and canal tie is resolved to the
river. 44 tests.

---

# HazardWise v0.7 — selection breadth + grade fairness (v0.6 batch)

v0.6 batch: 7/12 exact, 2 off-by-one, 3 two-step misses — best run yet; all
earlier failure classes held. Two systematic patterns fixed:

1. **Mainstem inclusion.** Basin selection took the two SMALLEST containing
   basins, letting tributary creeks crowd out the very river a mainstem
   property sits on (Constance Bay is ON the Ottawa). Now: smallest containing
   basin (most local watercourse) + LARGEST containing basin (the mainstem).
2. **Grade fairness.** Every v0.6 report graded D because one secondary gauge
   with a clamped (unresolvable) threshold dragged the weakest-link grade.
   Unresolvable gauges are now excluded from BOTH the headline probability and
   the grade — reported qualitatively, like beyond-record gauges.

Remaining known misses to re-test on this build: Minden, Constance Bay,
Grand Forks (expected mechanisms: mainstem exclusion and/or stale records;
send those three report.json files if any persist). 43 tests.

---

# HazardWise v0.6 — Sherbrooke lessons (data recency + honest degeneracy)

Field case: 85 Bowen Nord, Sherbrooke, QC — flooded twice 2015–2020, reported
Low. Diagnosis from the report JSON, in order of contribution:

1. **Stale federal records.** The Saint-François HYDAT gauges end in 1965/1972
   (Quebec hydrometry moved to the provincial DEH/CEHQ network). Recent floods
   simply are not in the data. v0.6: stale records (ending > 25 yrs ago) sort
   last in gauge selection, cap the grade at C, warn in the evidence chain
   ("record ends in 1972 — floods of the last 54 years are NOT represented"),
   and add a mandatory 'may be an underestimate' caveat. Full fix = Quebec
   provincial data connector (roadmap: added to M4).
2. **Grade-A-by-clamping bug (Engine 1).** All bootstrap draws pinned at the
   1e-5 AEP floor -> sigma ~ 3e-6 -> grade A on a wild extrapolation, and the
   blend weighted it 100%. v0.6: draws at the floor/ceiling are counted; if
   >10% are clamped, sigma is forced into the unresolvable regime -> grade D.
   Certainty produced by clamping is ignorance, not knowledge.
3. **Regulated-name false positive.** 'HIGHWOOD RIVER BELOW LITTLE BOW CANAL'
   was flagged as a canal. v0.6 parses the WATER BODY (text before
   AT/NEAR/ABOVE/BELOW) so rivers referencing structures aren't penalized —
   and reach-back is skipped entirely for true regulated structures (a
   'stage rise' on a diversion canal is meaningless).
4. **Rating-curve demand** (6.5 m HAND -> '11,354 m³/s needed' on a river whose
   real ~1,800 m³/s floods reach those streets): unchanged in v0.6 — this is
   the M5 calibration problem and needs observed flood extents, not tuning by
   hand. Documented, not patched.

Also: exposure-narrative dedup; model_version string corrected. 41 tests.

---

# HazardWise v0.5 — window-capped scale matching (v0.4 field failures)

Confirmed from a field report at High River coordinates: the requested channel
scale (5% of gauge DA) is structurally unreachable inside a 12 km terrain
window because flow accumulation restarts at the window edge — the Highwood
(3,950 km²) read "~38 km²", the fallback matched only the channel's EXIT cells
(hence "5,903 m away", HAND 9.1 m), thresholds exploded, AEPs pinned to the
floor, and reach-back silently disarmed. Also: a CANAL station led the gauge
list.

Fixes:
1. Scale targets are capped by what the window can express
   (min(0.05·DA, 25% of window-max accumulation)); the honesty flag and grade
   cap remain when capping occurs.
2. Channel masks cover the whole largest watercourse, not its exit cells —
   HAND is measured where the river passes CLOSEST to the property.
3. Regulated structures (CANAL/DIVERSION/SPILLWAY/FLOODWAY in the station
   name) are deprioritized in both basin and proximity selection, and flagged
   in the report when used at all.

Regression tests reconstruct the High River/Bowness geometry (river entering
the window from an external basin) and the canal-selection failure. 37 tests.

STILL PENDING FROM THE FIELD: basin polygons have never engaged in any run
("no gauged catchment contains this point" at points certainly inside gauged
catchments). Before the next validation batch run:
    python -m hazardwise.setup_data --basins-only
    python -m hazardwise.spatial.basins 50.582 -113.874
The second command must list Highwood-system stations. If it prints NOT
INSTALLED or zero matches, send me its exact output.

---

# HazardWise v0.4 — terrain correctness release (v0.3 field failures)

Second validation run regressed (5/12 exact, 4 two-step misses) and the report
JSONs made the mechanism unambiguous. Three structural fixes:

1. **Flat/lake fragmentation (root cause of "upstream area ~0 km²").** After
   depression filling, lakes are level → D8 sees no descent → the river network
   fragments into 2-cell ditches. Fix: priority-flood **+epsilon** so every
   filled flat drains to its pour point; rivers now connect through lakes.
   (`terrain/hand.py`, with a lake-crossing test.)
2. **Scale-matched HAND.** One terrain solve (`TerrainModel`), then per-gauge
   queries: HAND is height above the NEAREST channel whose upstream area is
   ≥ 5% of that gauge's drainage area — not the nearest storm swale (Mount
   Royal 77%-AEP false-High) and not an along-path confluence kilometres
   downstream (channel-slope inflation).
3. **Observed-flood reach-back.** If the largest flood already in the record
   maps to a stage at or above the property's HAND, the risk class is floored
   at High with a named year — no fitted tail may call an event that happened
   "negligible" (Merritt/Minden false-Lows).

Plus: displayed AEP is clamped to [0.01%, 25%] with honesty caveats (no more
"77% a year"), and basin containment is now an explicit polygon.contains(pt)
check with a diagnostic CLI:

    python -m hazardwise.spatial.basins 50.582 -113.874

**Action needed:** every v0.3 report used the proximity fallback — either
`setup_data --basins` wasn't run, or polygons failed to load. Run the
diagnostic above (High River coordinates shown) before the validation batch;
it prints polygon count and containing stations, or says NOT INSTALLED.

---

# HazardWise v0.3 — validation-driven update (M1 + M2)

First real validation run (12 labeled addresses): 8/12 exact, 2 off-by-one,
2 two-step misses. Both misses were diagnosed from the report JSON and fixed:

| Miss | Root cause | v0.3 fix |
|---|---|---|
| Markham: Low→High | 18-yr gauge, threshold never observed; Weibull 1/(n+1) laundered into headline | beyond-record results are bounds, excluded from headline, reported qualitatively |
| Sumas: High→Low | 800 m relief ring blind to a closed depression (former lake bed behind dikes) | HAND on 30 m DEM + priority-flood fill-depth = depression signature → risk bump + caveat |

New in v0.3:
- `spatial/basins.py` — M1 gauge selection by WSC basin-polygon containment
  (smallest catchment first); proximity only as caveated fallback (grade ≤ C)
- `terrain/hand.py` — pure-numpy HAND: priority-flood filling, D8 routing,
  flow accumulation, height-above-drainage, ponding-depth detection
- `terrain/dem.py` — MRDEM-30 DTM cloud-COG windowed reads (~0.2 MB/report);
  local GeoTIFF override via HW_DEM_PATH for HRDEM study areas
- `setup_data --basins` — installer now fetches basin polygons too
- 30 offline tests including regression scenarios reconstructing both misses

Also credible-number fix: the absurd 72–74% AEPs (Bowness, Constance Bay) came
from ring-relief ≈ 0 beside the river; HAND above the actual channel replaces
them with defensible values. Re-run `python -m hazardwise.run_validation` to
confirm — expected outcome is zero two-step misses and saner High-class AEPs.

---

# HazardWise — Engine 1 Flood Statistical Core (Sprint 1, increment 1)

Status: implemented, 16/16 tests passing, end-to-end demo runs.

## What this is

The flood-frequency statistical core from handoff Section 5.1 — the component
every other part of the product consumes. Pure Python, no database or network
dependencies yet, so it drops into the Claude Code repo as `hazardwise/models/`
and can be developed against synthetic data until HYDAT ingestion exists.

```
hazardwise/models/ams.py         AMS container + data-quality validation
hazardwise/models/frequency.py   GEV + LP3 (MLE), bootstrap CI (n=1000),
                                 Weibull fallback, precision-weighted blending
hazardwise/models/confidence.py  Bayesian confidence engine, A–D grades,
                                 CA-polygon priors, mandatory caveats
hazardwise/models/estimate.py    FloodRiskEstimate assembly, multi-gauge
                                 combination, explanation-chain generation
hazardwise/tests/test_engine1.py 16 tests incl. known-distribution recovery
demo_exemplar.py                 Reproduces the Section 15 exemplar scenario
```

Run tests: `HW_BOOTSTRAP_N=150 pytest hazardwise/tests` (env var shrinks the
bootstrap for CI speed; production default is 1000).

## Ambiguities resolved (founder review requested)

1. **"Bayesian blending weighted by record length"** — both models fit the same
   record, so record length can't distinguish them. Implemented as
   inverse-variance weighting of the bootstrap estimates in log10-AEP space,
   with between-model disagreement added to the blended sigma (two confident
   but conflicting fits produce honest, wide uncertainty). Record length still
   drives overall CI width through the bootstrap.
2. **CA polygon prior direction** — the spec says an official polygon "narrows
   CI." Implemented so it narrows only on *agreement*; a polygon that
   contradicts the gauge estimate *widens* sigma and adds a mandatory caveat.
   Narrowing on contradiction would be the false confidence Section 3.5 forbids.
3. **Multi-gauge headline** — P(at least one watercourse floods) under
   independence; per-gauge results shown separately per Section 3.3. Independence
   underestimates joint risk under basin-wide storms — revisit with copulas in
   Phase 2. Property grade = worst per-gauge grade.
4. **Flow threshold provenance** — AEP is computed against a caller-supplied
   flow threshold until the DEM/HAND module exists; the explanation chain says
   so explicitly rather than pretending the linkage is done.
5. **Adjusted sigma feeds the CI, not just the grade** — penalties and priors
   re-derive the reported interval so the numbers and the badge never disagree.

## Honest finding from the demo (important)

With a 27-year record and a 1-in-60-year threshold, the recovered central AEP
was off by a large factor (0.07% vs true 1.67%) — although the 90% CI contained
the truth and the confidence engine correctly assigned **grade D**. This is the
known instability of MLE-fitted GEV/LP3 tails on short records, and it is
precisely the failure mode the grade system exists to catch. Two implications:

- The confidence machinery is doing its job: bad tail estimates arrive wearing
  a red badge, not a confident number. This mirrors the AR-lag lesson from the
  Alberta flood work — the system must make its own weaknesses visible.
- **Planned refinement (before real HYDAT data):** replace MLE with L-moments
  estimation for records under ~50 years (standard hydrological practice;
  `lmoments3` package), and consider a regional-skew prior for LP3 per USGS
  Bulletin 17C. This should tighten short-record central estimates materially.

## Not yet built (deliberately)

HYDAT ingestion, PostGIS schema, watershed gauge-finding (NHN spatial join),
HAND/DEM elevation module, geocoding cascade, wildfire engine, API layer.
Recommended next increment: **HYDAT ingestion → `gauge_frequency` batch
pre-computation**, since the "never fit at report time" rule makes it the
dependency root of the whole pipeline.


## v0.13 (July 2026) — satellite/map layer
Every report now embeds flood_map.png: binned AEP probability surface (PuBu,
bins tied to report language) over the TerrainModel HAND raster, Beta-Binomial
fusion ready for observed extents (EGS/EMS/S1-self; stack empty until
M-SAT-1), geocode-precision gate, susceptibility mode for the ungauged case,
uncertainty hatching. Benchmark gains maps_n / map_integrity_bad (T5).
Fail-open: map errors caveat the report, never kill it. 66 tests.
See hazardwise/satellite/ and WINDOWS_SETUP.md; design doc: DESIGN.md.

## v0.14 (July 2026) — satellite-only mode
`python -m hazardwise.sat_pipeline ADDRESS`: flood risk with NO gauge/HYDAT.
JRC Global Surface Water occurrence (30 m, 1984-2021, streamed windows) binned
by HAND -> empirical wet-probability curve (monotone-enforced) -> address AEP
with credible range; Beta-Binomial fusion with SAR-mapped extents when
present; risk class via the same classifier; grade capped C (D = terrain
screening when occurrence is unusable). Full evidence chain, asymmetric-
undercount caveat, index table, map with integrity checks. 69 tests.

## v0.15 (July 2026) — hatched risk bands
Map risk bins now carry cross-hatch textures of ascending density ('..' '//'
'xx' '++') alongside the blue ramp: neighbourhood risk levels survive
grayscale printing and colorblindness. Uncertainty hatch moved to gray
backslash to stay distinguishable. Legend block audited (bin_hatches).

## v0.15.1 — local web UI for satellite-only mode
`python -m hazardwise.webapp` -> http://localhost:8077: address in, report +
hatched map out. Stdlib http.server, localhost-only, serves strictly from
reports_sat/ (path-traversal guarded).

## v0.16 (July 2026) — wildfire hazard + street view
hazardwise/satellite/wildfire.py + fire_pipeline.py: NBAC-based annual burn
probability (regional rate x WUI exposure x slope, Beta-Binomial on burned-
year counts with 5x pixel-evidence deflation), REACH-based classification
(max within 250 m + near-burn bump), warm-ramp hatched map with perimeter
reach-back, C/D grade caps, Jasper & Ottawa Valley scenario tests. Reports
gain Ground-level view links. 76 tests.

## v0.16.1 — fire validation panel, satellite-first docs, white paper
run_fire_validation + validation/fire_addresses.yaml (12 labeled WUI/urban
addresses; acceptance gate for M-FIRE-1). WINDOWS_SETUP.md rewritten
satellite-first with the basin method last. WHITEPAPER.md: full model math,
parameter registry with status labels, uncertainty construction, references.

## v0.17 (July 2026) — combined runner, street basemap, viz options
combined_report.py: one or many addresses -> flood + fire reports, blue/red
hatched maps over an optional OSM street layer (fail-open to hillshade),
index.html + results_summary.csv scored against the editable
validation/panel_addresses.csv ground-truth store. Render options plumbed
end-to-end (colors, layer/basemap opacity, hatching toggle). 79 tests.

## v0.18 (July 2026) — functional fire pipeline + dual-hazard report
M-FIRE-1 landed: satellite/nbac.py reads any cached NBAC vector (gpkg/shp/
gdb/zip, HW_NBAC_DIR override), bbox-filters, rasterizes per-YEAR onto the
AOI grid; fire_pipeline auto-loads it (fail-open to screening with the
loader message as caveat); offline end-to-end acceptance test included.
dual_report.py: one command -> one report with both hazards, two hatched
maps, two independent justification chains, benchmark-compatible JSON.
81 tests.

## v0.18.1 — NBAC loader hardened
CRS-attempt cascade (detected -> EPSG:3978 -> 4326) with zip:// fallback and
per-attempt diagnostics (the real NBAC ships in Canada Lambert metres; a
degree bbox selected nothing). Empty-window-with-record now GRADES Low
(evidence of absence) instead of refusing. New diagnostic:
python -m hazardwise.satellite.nbac LAT LON. WINDOWS_SETUP S13: foolproof
install/verify procedure. 84 tests.

## v0.19 (July 2026) — aerial basemap + regional context maps
--basemap aerial (Esri World Imagery) so neighbours/streets/rivers sit
under the risk colors. Flood reports gain a ~12 km auxiliary map:
topography hillshade + permanent (JRC>=80) and seasonal (5-80%) waters.
Fire reports gain a ~12 km auxiliary: terrain + NBAC perimeters by year +
current wind arrow (Open-Meteo, fail-open); vegetation/fuel layer stamped
pending M-FIRE-2 rather than faked. All aux rendering fail-open; dual reports embed both context maps. 87 tests.

## v0.19.1 - rural geocoding cascade + test isolation
geocoding/rural.py: any address resolves - prairie legal-address grammar
(lot/LLD strip, Rr->Range Road, Twp->Township Road), Photon/OSM second
engine, locality/county-centroid last resort whose method string triggers
the precision gate (uncertainty circle, no parcel claim). Screening test
isolated from host NBAC caches. 89 tests.

## v0.20 - trust guards
Scale-compatibility rejection (gauge/window >30x -> refusal, all-rejected ->
clean SCALE-COMPATIBILITY REFUSAL pointing to sat mode); centroid geocodes
carry confidence D and cap gauged grade at D; flood map audit bump-aware
(mirrors fire); LOCATION IMPRECISE banner on all centroid-gated reports;
model_version stamp fixed; IMPROVEMENT_PLAN.md added. 92 tests.

## v0.21 - regional event history + auto-zoom basemap
Reports now list recent fires (and floods where EGS extents exist) over
10/20/50-year horizons within a 25 km window, with counts, years, and
nearest-event distance; honest empties and a note that JRC occurrence is a
frequency not a dated-event list until the EGS connector lands. Basemap
auto-drops zoom to fit any AOI width (no more tile-budget fallback) with an
on-disk tile cache. New history_indices in the fire index table. 94 tests.

## v0.22 - B2 regional rate + history floor + debug/radius options
Wildfire base rate now computed over the --radius ring (default 25 km) not the
3 km window (B2); when regional history shows fires the rate is floored to the
history-implied value and disclosed in the chain, so a populated history can
never coexist with 0.00%. New --radius and --debug[ stdout] options on all
report commands (debug writes reports_debug/ traces with the rate
decomposition). Sangudo regression test added. 96 tests.
