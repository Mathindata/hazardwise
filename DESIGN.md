# HazardWise — Satellite Data Pipeline Design (target: v0.13–v0.15)

**Scope of this document:** (1) inventory of satellite/EO data resources with spatial and
temporal characteristics, (2) pipeline architecture and integration with the existing
address → gauge → HAND → GEV/LP3 flow, (3) decision logic for degraded-gauge cases,
(4) the report map product: probability surface, coloring scheme, rendering, and
integrity checks. Wildfire reuse is noted per-section but deferred (post-flood-pipeline).

---

## 1. Goal and product definition

Two distinct satellite products exist; only the first is in scope now.

**P1 — Historical inundation-frequency surface (in scope).** A per-pixel P(flood)
raster over an AOI centred on the address, built from stacked observed flood extents
(EGS, Copernicus EMS, self-generated Sentinel-1 classifications, JRC water occurrence)
fused with the existing model surface (AEP curve + HAND stage-to-extent mapping).
This is what gets rendered as the colored map in the report, and it doubles as the
per-pixel label source for Lever 3 (learned model form).

**P2 — Near-real-time flood state monitoring (out of scope, post-1.0).** Same catalog
machinery, different latency requirements. Design nothing for it now beyond not
blocking it (keep the catalog module event-agnostic).

---

## 2. Data source inventory

### 2.1 Extent / observation layers (the evidence stack)

| Source | Type | Spatial res. | Temporal | Latency | Record | Access | Role |
|---|---|---|---|---|---|---|---|
| **EGS (NRCan)** | Vector flood extents from SAR, analyst-vetted | ~10–30 m effective | Event-activated | Days | ~2017– | NRCan archive GDB (already ingested) | Gold-standard labels; already the calibration source |
| **Copernicus EMS Rapid Mapping** | Vector flood extents, vetted | 10–30 m effective | Event-activated (on request) | 1–3 days | 2012– | Free download per activation | Supplements EGS for events EGS didn't cover |
| **Sentinel-1 A/C SAR (GRD/RTC)** | C-band backscatter | 10 m pixel (~20 m resolution) | ~6-day revisit over Canada (A+C), better at high latitude | ~1–3 h (NRT), ~24 h (standard) | Oct 2014– | Copernicus Data Space, AWS Open Data, MS Planetary Computer (RTC product) | **Self-generated extents** — the main new capability; fills gaps between EGS activations |
| **RCM (RADARSAT Constellation)** | C-band SAR | Public tier ≥16 m; 5/3/1 m with vetted account | ~Daily average Canada revisit | Hours–1 day | 2019– | EODMS, free | Densifies the S1 stack; **apply early for vetted account** (Canadian entity) |
| **JRC Global Surface Water** | Optical-derived water occurrence/seasonality | 30 m | Monthly composites | Static product (updated ~annually) | 1984– | GEE / public COGs | **Long-record anchor**: permanent-water mask + occurrence percentiles reaching back 40 yr |
| **Copernicus GFM** | Systematic S1-based flood extents | 20 m | Every S1 pass | ~8 h | 2022– | Copernicus | Cross-check for our own S1 classifier |
| **Sentinel-2 MSI** | Optical | 10 m | ~5-day (2A/2B/2C), cloud-limited | Hours | 2015– | Same as S1 | NDWI water masks when cloud-free; secondary |
| **MODIS/VIIRS NRT flood (MCDWD)** | Optical composite flood product | 250 m / 375 m | Daily | ~3 h | 2000– / 2012– | NASA Earthdata / LANCE | Too coarse for parcel maps; useful only for event *timing* detection |

### 2.2 Supporting layers (already in system or trivial)

- **MRDEM-30 HAND** windows — already computed; resample to the output grid.
- **WSC/HYDAT hydrographs** — used here for *event timing* (when did floods occur →
  which scenes could have observed them), not just frequency analysis.
- **Basemap tiles** (OSM/CARTO, grayscale) for report rendering with attribution.

### 2.3 Commercial tier — defer until revenue (unchanged from handoff)

ICEYE flood-depth product (hours latency), Planet 3 m daily, Capella/Umbra SAR
tasking, Maxar 30 cm. No design dependency on any of these.

**Verify at implementation time** (specs drift): Planetary Computer S1-RTC coverage
over Canada, current EODMS API auth flow, GFM Canada coverage completeness.

---

## 3. Pipeline architecture

New package `hazardwise/satellite/`, mirroring the `spatial/` conventions
(COG-window reads, parquet caches, per-module diagnostic CLI, all constants in
`params.py`).

```
address ──► AOI definition (satellite/aoi.py)
                │  3 km × 3 km default (SAT_AOI_HALF_KM), UTM zone of AOI,
                │  10 m output grid (S1 native; HAND resampled bilinear)
                ▼
        catalog.py  ── STAC search per AOI: S1 RTC, S2, RCM (EODMS), scene index
                │      cached to parquet (aoi_id, scene_id, sensor, datetime, footprint)
                ▼
        events.py   ── event catalog from gauge hydrographs + EGS/EMS activation dates:
                │      cluster exceedances into events (14-day window), record per-event
                │      which scenes fell inside the window → per-pixel effective
                │      observation count N_eff and per-event "observed? y/n"
                ▼
        s1_flood.py ── per-scene water classification (self-generated extents):
                │      RTC gamma0 VV(+VH) → change detection vs. dry-season reference
                │      composite → Otsu/threshold split → HAND mask (wet only where
                │      HAND < SAT_HAND_MAX_M, kills slope/shadow false positives) →
                │      morphological cleanup → vector/raster extent with QA flags
                ▼
        extent_stack.py ── rasterize ALL extents (EGS, EMS, S1-self, GFM) to the
                │      common grid; provenance per layer; permanent-water mask from
                │      JRC occurrence ≥ SAT_PERMWATER_PCT (default 80%) — excluded
                │      from labels (the in-channel label-hygiene lesson, now spatial)
                ▼
        frequency.py ── per-pixel empirical exceedance:
                │      k = # distinct EVENTS pixel observed wet (not # scenes)
                │      n = N_eff events with usable observation
                │      annualize by event rate from the gauge record; Wilson interval;
                │      detection-probability correction (flood duration above threshold
                │      from hydrograph vs. revisit interval → capture probability)
                ▼
        surface.py  ── fusion with the model (see §5) → P(flood) raster + CI raster
                ▼
        render.py   ── report map (see §6) + map integrity checks (see §7)
```

Design rules carried over from the riverine lessons:

- **Events, not scenes.** Counting scenes double-counts long floods and undercounts
  short ones. The unit of evidence is a flood event; scenes are observations of events.
- **Noisy negatives.** "Not wet in the one scene we had" is weak evidence of dry —
  exactly the EGS FAR lesson. The Wilson interval and detection-probability correction
  encode this; never treat n as if every event were perfectly observed.
- **Label hygiene.** Permanent water masked out; extents clipped to HAND-plausible
  terrain before use as labels.
- **Provenance stamping.** Every layer in the stack carries (source, scene/activation
  id, date, processing version); the report legend lists them; `benchmark.py` checks
  they're present.
- **Re-collection trigger.** Any terrain change (new MRDEM, HAND rework) invalidates
  the extent stack HAND-masks → same re-collect discipline as calibration points.

---

## 4. Integration with the existing pipeline — degraded-gauge flows

The satellite branch is a **second evidence channel**, weighted by the Bayesian
confidence engine rather than bolted on as an override. Gauge-quality triggers
already exist in the codebase; each maps to a satellite role:

| Case | Trigger (existing) | Primary evidence | Satellite role | Map content | Language / grade behavior |
|---|---|---|---|---|---|
| **GAUGE_OK** | Basin polygon match, ≤150 km mainstem, unregulated, adequate record | GEV/LP3 + HAND stage-to-extent | *Validation*: observed extents must sit inside modelled ≥their-AEP zones; calibrates stage-to-extent | Model probability surface + observed-extent outlines | Normal grading; note "N observed events consistent/inconsistent with model" |
| **GAUGE_FAR** | Mainstem guard fails / nearest gauge beyond thresholds | **Satellite empirical frequency** + HAND relative elevation | *Primary*: JRC occurrence + event stack drive P(flood); event annualization from regional gauges only | Empirical surface, wider CI hatching | Bounds language (already exists); grade capped per existing cap logic; state "no representative gauge; probability from observed inundation history" |
| **GAUGE_REGULATED** | Regulated-structure demotion fires | Blend, structure-era data only | Extents *post-regulation* only (date-filter the stack) — pre-dam floods are not evidence for today | Surface + note on regulation era | Existing demotion language + era filter disclosed |
| **GAUGE_SHORT** | Record < threshold / inhomogeneity | Blend weighted by confidence engine | Extends effective record: JRC 1984– occurrence acts as the long-memory prior | Blended surface | CI widened; both records' lengths disclosed |
| **UNGAUGED / NO_EVENTS** | No gauge and empty extent stack (no observed flooding since 1984/2014) | HAND-only | None available | **Relative susceptibility** ramp (HAND bands), *not* probability | Explicitly labeled "susceptibility, not probability" — never render the probability legend here |
| **CONFLICT** | Satellite shows repeated wet where model says Low (or inverse) | Neither, until resolved | Diagnostic | Both layers shown, conflict flagged | Confidence demoted; contradiction surfaced in report integrity block (Grand Forks-style regressions become visible instead of silent) |

Two additional guards:

- **Geocode-quality gate.** Locality-centroid geocodes (Constance Bay case) make a
  parcel-scale map actively misleading. If geocode precision is worse than
  `SAT_MAP_MIN_GEOCODE_M` (default ~100 m), render the map at neighbourhood zoom with
  the address shown as an *uncertainty circle*, not a pin, and say so in the caption.
- **River-identity dependency.** The Kamloops failure mode (wrong channel) also
  poisons extent interpretation — Peterson Creek flooding is not Thompson River
  evidence. The frozen river-identity task (Lever 1) remains a prerequisite for
  *attributing* extents to a channel; until it lands, the map fuses extents
  channel-agnostically and the report says so.

---

## 5. Fusion: from extent stack + model to P(flood)

Per pixel, a Beta-Binomial update keeps this defensible and CI-honest:

1. **Prior** from the existing model: the AEP curve + HAND gives, for each pixel,
   the modelled annual probability that water reaches it (interpolate stage between
   return-period levels; pixel wet when stage-converted depth > HAND). Encode as
   Beta(α₀, β₀) with an effective prior weight `SAT_PRIOR_WEIGHT_EVENTS` (default:
   equivalent to ~10 observed events — tune on the panel).
2. **Likelihood** from the stack: k wet out of n effectively-observed events
   (detection-corrected, era-filtered).
3. **Posterior** mean → P(flood) raster; posterior interval → CI raster driving the
   uncertainty hatching. The gauge-case table in §4 is implemented purely as
   different (α₀ weight, n admissibility) settings — one code path, no forked logic.

This is also exactly the feature/label table Lever 3 needs: (hand, q2, qmax, DA,
measured_rise, fill_depth, jrc_occurrence, k, n) per pixel. The satellite pipeline
retires the "power law lacks functional form" blocker by *replacing the prior* with
the learned monotone model when it exists — surface.py takes the prior as an
injected callable.

---

## 6. The report map

### 6.1 Rendering

- **Static PNG embedded in HTML/JSON reports** — defensibility first: fixed scale
  bar, north arrow, CRS note, acquisition-date range, layer provenance in the legend,
  generation timestamp, model-constants stamp (same string as the report header).
  Matplotlib + contextily grayscale basemap (attribution required).
- Optional **interactive layer (folium/leaflet) in the HTML report only**, clearly
  marked "illustrative"; the PNG remains the document of record.
- Default extent: 1.5 km half-width around the address (matches the min-HAND search
  radius); zoom out automatically under the geocode-quality gate.
- Grid: 10 m; reproject to Web Mercator only at render time.

### 6.2 Coloring scheme

**Decision: blues for flood probability, warm ramp reserved for wildfire.** The two
hazards will eventually share one report; keeping hazard hue families disjoint
(flood = blue, fire = yellow-orange-red) prevents cross-reading. Blue is also the
conventional flood-mapping semantic (FEMA, provincial flood maps).

Binned, not continuous — bins tie 1:1 to the AEP language the report already uses,
so the map can never contradict the text. Sequential PuBu (colorblind-safe):

| AEP band | Return period | Hex | Note |
|---|---|---|---|
| ≥ 5% | ≤ 1:20 | `#045a8d` | darkest |
| 2–5% | 1:20–1:50 | `#2b8cbe` | |
| 1–2% | 1:50–1:100 | `#74a9cf` | |
| 0.5–1% | 1:100–1:200 | `#bdc9e1` | |
| 0.2–0.5% | 1:200–1:500 | `#f1eef6` | lightest; below 0.2% unshaded |

Overlaid elements:

- **Permanent water** (JRC ≥ 80% occurrence): solid `#08306b` with a wave-hatch —
  visually distinct from the darkest probability bin; excluded from grading.
- **Observed historical extents** (EGS/EMS/S1-self, union): dashed `#c51b8a`
  (magenta) outline — high contrast against blues and distinguishable under the
  common colorblindness types; per-event outlines available in the interactive layer.
- **Uncertainty**: 45° gray hatching wherever the posterior CI spans ≥ 2 bins
  (from the CI raster); legend entry "probability uncertain within shown range".
- **Susceptibility mode** (UNGAUGED case): a *different* ramp (single-hue gray-blue,
  3 bands by HAND: <2 m, 2–5 m, 5–10 m) and a different legend title — the
  probability legend must be impossible to display without a probability surface.
- **Address marker**: black pin at ~60% alpha, or uncertainty circle under the
  geocode gate. Overlay alpha 0.6 over the grayscale basemap.

### 6.3 Legend / caption content (mandatory, checked by benchmark)

Legend blocks: (1) probability bins with return-period equivalents, (2) permanent
water, (3) observed extents with source counts ("7 events: 4 EGS, 1 EMS, 2 S1"),
(4) uncertainty hatching, (5) provenance line: sensors, date range, HAND/DEM
version, constants stamp.

---

## 7. Monitoring & integrity (additions to `benchmark.py`)

- **Grade-vs-map consistency**: the bin at the address pixel must match the report
  grade band; mismatch = integrity failure (same family as grade-vs-cap).
- **Legend completeness**: all five legend blocks present; provenance non-empty.
- **Extent-vs-grade contradiction count**: pixels graded Low inside ≥2 observed
  extents (the CONFLICT detector), reported per address.
- **Classifier skill** (S1-self vs EGS on shared events): IoU per event; target
  median IoU ≥ 0.6 before self-generated extents are admitted into the stack
  (below that they render as outlines only, flagged "unvetted").
- Panel targets unchanged; add: zero probability-legend renders in
  susceptibility-mode addresses.

---

## 8. Milestones

1. **M-SAT-1 — Catalog + stack (no classification).** `aoi/catalog/events/
   extent_stack` with EGS + EMS + JRC only. Run on the 12-address panel; inspect
   stacks manually. *Exit: parquet scene index + stacked rasters for all 12.*
2. **M-SAT-2 — S1 self-classification.** `s1_flood.py`, validated against EGS on
   shared events (IoU gate). *Exit: median IoU ≥ 0.6 on ≥ 5 events.*
3. **M-SAT-3 — Frequency + fusion.** `frequency/surface` with Beta-Binomial;
   detection correction from hydrographs. *Exit: posterior surfaces on panel;
   High River / Bowness surfaces visually and numerically sane vs. known 2013 extents.*
4. **M-SAT-4 — Report map.** `render.py`, coloring scheme, legend, geocode gate,
   susceptibility mode; benchmark integrity additions. *Exit: maps in all 12 panel
   reports, integrity all zeros.*
5. **M-SAT-5 — Degraded-gauge wiring.** §4 table implemented as prior-weight/
   admissibility settings; panel re-run; Grand Forks report pulled and diagnosed
   with the new conflict detector. *Exit: benchmark_history row with satellite
   columns populated.*
6. **M-SAT-6 (deferred) — RCM densification** once EODMS vetted account lands;
   pure catalog addition, no architecture change.

Sequencing vs. existing levers: M-SAT-1 can start immediately and does not depend
on river identity; M-SAT-5's *attribution* quality does (disclosed until then).
The repo-into-git-under-Claude-Code step from the handoff should precede M-SAT-1.

---

## 9. Wildfire reuse map (for later, one paragraph per module)

`catalog.py` gains FIRMS/VIIRS hotspot and NBAC/CWFIS endpoints (same STAC-ish
index schema). `extent_stack.py` stacks NBAC annual burn perimeters instead of
flood extents (label hygiene analogue: inside-burn ≠ every-point-burnable →
fuel-grid mask replaces the JRC permanent-water mask). `frequency.py` is reused
verbatim (events = fire seasons touching the AOI). `surface.py`'s prior becomes
FWI climatology + fuel continuity + slope/aspect from the existing MRDEM windows.
`render.py` reuses everything with the warm ramp (YlOrRd) reserved in §6.2 and a
"distance-to-wildland / WUI" annotation. The report gains a second hazard section;
grades stay per-hazard.
