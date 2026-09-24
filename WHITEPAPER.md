# HazardWise Technical White Paper
## Satellite-derived flood and wildfire risk for Canadian addresses (v0.16)

### 1. Purpose and design principles
HazardWise turns a Canadian address into a defensible, per-hazard risk report.
Four principles govern every number it prints:
(1) **Explainability** — each report carries a line-by-line evidence chain from
raw data to the grade; (2) **Honest bounds** — every probability ships with a
credible interval whose construction is stated, and evidence grades are capped
by construction, not by tuning; (3) **Disclosed interim forms** — any
functional form not yet learned from labels is named as interim in the report
itself and is *replaced, not tuned* (roadmap "Lever 3"); (4) **Fail-open,
audit-closed** — analysis failures degrade to caveats, while a benchmark
integrity layer must read zeros before any release.

### 2. Data sources
| Layer | Product | Spatial | Temporal | Role |
|---|---|---|---|---|
| Terrain | NRCan MRDEM DTM (COG windows) | 30 m | static | HAND, slope; streamed ~12 km windows |
| Flood history | JRC Global Surface Water v1.4 | 30 m | monthly, 1984-2021 (38 yr) | occurrence backbone |
| Flood events | NRCan EGS + Copernicus EMS extents; Sentinel-1 self-classified | 10-30 m | event-based, ~2014- | Beta-Binomial likelihood |
| Burn history | NRCan/CWFIS NBAC perimeters | vector/30 m | annual, 1972- (~54 yr) | fire backbone |
| Fuels | CWFIS FBP fuel grids / land cover | 30-250 m | ~static | burnable mask, WUI distance |
| Fire events | NASA FIRMS VIIRS/MODIS hotspots | 375 m/1 km | daily, 2000/2012- | timing, reach-back |
| Geocoding | OSM Nominatim | — | live | address -> point + precision class |

### 3. Terrain foundation
On a filled 30 m DEM window, D8 accumulation defines channel cells where
drainage area >= A* (gauged mode: a fraction of gauge drainage area, clamped;
satellite-only: `SAT_ONLY_CHANNEL_KM2` = 5 km2), window-capped at 25% of the
window so a clipped major river cannot masquerade as a creek. **HAND** is
h(x) = z(x) - z(c(x)) with c(x) the Euclidean-nearest channel cell (Renno et
al. 2008; Nobre et al. 2011). Closed-depression fill depth is retained as a
separate consequence signal. All rasters are resampled to a 10 m analysis
grid over a 3 km AOI (`SAT_AOI_HALF_KM`, `SAT_GRID_RES_M`).

### 4. Flood probability (satellite-only)
**Backbone.** Pixels with occurrence >= `SAT_PERMWATER_PCT` (80%) are
permanent water and excluded (spatial label hygiene). Remaining pixels are
binned by HAND (`SAT_CURVE_BAND_M` = 0.5 m up to `SAT_CURVE_MAX_M` = 12 m);
each contributes v = min(occ/100 x `SAT_OCC_TO_ANNUAL`, 0.5). The band value
p_j = mean(v) with effective sample n_eff = n_px / `SAT_PX_DECORR` (25: 30 m
pixels are correlated at ~5x5 blocks); its interval is the union of
Wilson(p_j, n_eff) and the band's 10th-90th percentile spread. Bands with
< `SAT_CURVE_MIN_PX` (10) pixels are interpolated. Monotonicity is enforced
by reversed running max: wet probability cannot increase with height.
**Fusion.** The curve interpolated over HAND is the prior AEP surface.
With event rate lambda = max_j p_j, AEP converts to per-event probability
q = -ln(1-AEP)/lambda; the prior is Beta(qw, (1-q)w) with prior weight w
per gauge case (`SATELLITE_ONLY`: 8 equivalent events; degraded gauged cases
0.1w-1w — one code path). Observed extents update with (k wet events, n
observed events); the posterior mean annualizes back as
AEP = 1 - exp(-lambda q̂), display-capped at `AEP_DISPLAY_CAP` (0.25).
**At-address.** Central = posterior at the address pixel; the credible range
is the envelope of the curve interval at the address's HAND and the Beta
5-95% posterior, clamped under the cap. Classification uses the same
`classify_risk(AEP, HAND, fill_depth)` thresholds as the gauged engine.
**Grades.** Satellite-only caps at C (`SAT_ONLY_GRADE`); no usable
occurrence -> D screening (`SAT_ONLY_GRADE_NO_OCC`), susceptibility map,
zero probability claimed.

### 5. Wildfire probability (archive-only)
**Base rate.** r = mean annual fraction of *burnable* window area inside
NBAC perimeters over the record, Wilson interval on record-years.
**Local modulation.** Exposure e(x) = exp(-d_WUI/`FIRE_WUI_DECAY_M`) with
d_WUI the distance to burnable fuel (500 m ember/radiant e-fold); slope
factor s(x) = 1 + min(slope,30)/30 x `FIRE_SLOPE_MAX_BOOST` (0.5).
**Prior.** p0(x) = 1 - exp(-r e s `FIRE_PRIOR_C`), capped 0.25.
**Update.** Burned-year counts k(x) update the Beta prior; pixel trials are
n_rec x `FIRE_PIXEL_EVIDENCE_FRACTION` (0.2) because the same years produced
r — counting them at full weight is double-counting. lambda = fire-years /
record-years.
**Decision variable.** The graded number is REACH probability: the posterior
maximum within `FIRE_REACH_RADIUS_M` (250 m) of the address — a fire at the
fence line is the risk to the structure; both reach and at-pixel values are
printed. A mapped burn within `FIRE_NEAR_BURN_BUMP_M` (250 m) bumps the
class one step; the map-integrity audit is bump-aware, so the bump is
disclosed, never smuggled.
**Classes / grades.** High >= `FIRE_HIGH_AEP` (0.02), Medium >=
`FIRE_MED_AEP` (0.005); grade capped C (`FIRE_GRADE`), D screening without
NBAC. NBAC under-represents small/WUI fires, so central values are lower
bounds of this channel (stated caveat); CWFIS FWI seasonal danger is a
planned addition, not part of the climatological frequency.

### 6. Parameter registry (probability-assigning constants)
| Constant | Value | Role | Status |
|---|---|---|---|
| SAT_AOI_HALF_KM / SAT_GRID_RES_M | 1.5 km / 10 m | analysis window/grid | structural |
| SAT_ONLY_CHANNEL_KM2 | 5.0 | channel scale sans gauge | interim |
| SAT_PERMWATER_PCT | 80% | permanent-water hygiene | structural |
| SAT_OCC_TO_ANNUAL | 1.0 | occurrence -> annual map | **interim, replaced by Lever 3** |
| SAT_PX_DECORR | 25 | spatial-correlation deflation | measured-order estimate |
| SAT_CURVE_BAND_M / MAX_M / MIN_PX | 0.5 / 12 / 10 | curve support | structural |
| SAT_PRIOR_WEIGHT_EVENTS (w) | 10 (case-scaled; SAT-only 8) | prior strength | interim |
| AEP_DISPLAY_CAP | 0.25 | display honesty cap | structural |
| FIRE_WUI_DECAY_M | 500 m | ember/radiant e-fold | interim (literature-order) |
| FIRE_SLOPE_MAX_BOOST | 0.5 | slope multiplier ceiling | interim |
| FIRE_PRIOR_C | 1.0 | rate scaling | **interim, replaced by Lever 3** |
| FIRE_PIXEL_EVIDENCE_FRACTION | 0.2 | anti-double-counting | structural |
| FIRE_REACH_RADIUS_M / NEAR_BURN_BUMP_M | 250 m | structure-risk decision rule | structural |
| FIRE_HIGH_AEP / FIRE_MED_AEP | 0.02 / 0.005 | class thresholds | policy |
Every constant lives in `params.py` (SAT_/FIRE_ prefixes); calibration
overrides are stamped into every report ("Model constants: ...").

### 7. Uncertainty, asymmetry, and refusal
Three interval layers: within-band spread, correlation-deflated Wilson, Beta
posterior. Both hazards carry a stated *asymmetric* undercount (optical
misses short/cloudy floods; NBAC misses small fires): central values are
lower bounds of their channel and the upper bound carries the asymmetry.
Where the backbone is unusable the model *refuses*: grade D, susceptibility
map, no probability legend renderable — a wrong "Low" is worse than an
honest "Undetermined".

### 8. Integrity auditing
Per report: grade-vs-map-bin at the address (bump-aware for fire), mandatory
legend blocks, probability-legend-in-susceptibility impossibility,
extent-vs-grade conflicts. `python -m hazardwise.benchmark` appends all
counters to benchmark_history.csv; release criterion: zeros.

### 9. Known limits and replacement path
Interim forms (S6) are scheduled for replacement by a learned monotone
mapping trained on EGS/NBAC labels (Lever 3) — the calibration finding that
motivated this: the hand-set power-law rating *lacked the functional form*
to separate flooded from near-miss-dry, and constant tuning is a dead end.
Other stated limits: locality-centroid geocodes (uncertainty circle, never a
parcel claim), canopy/urban sensing blindness (Class-1 panel failures),
standing-water contamination pending JRC recurrence/seasonality layers,
riverine-and-wildfire only (pluvial, coastal post-1.0).

### 10. References
Pekel, Cottam, Gorelick, Belward (2016), Nature 540 — Global Surface Water.
Renno et al. (2008), RSE 112 — HAND. Nobre et al. (2011), J. Hydrology 404 —
HAND terrain descriptor. Hall et al. (2020), Int. J. Wildland Fire — NBAC.
Van Wagner (1987) — Canadian FWI System. USGS Bulletin 17B — flood frequency
moments (gauged engine). Datasets: cwfis.cfs.nrcan.gc.ca/datamart,
global-surface-water.appspot.com, open.canada.ca (MRDEM, EGS),
firms.modaps.eosdis.nasa.gov.
