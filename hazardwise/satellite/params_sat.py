"""Satellite-pipeline constants. Merge into hazardwise/params.py; keep the
SAT_ prefix so calibration/best_params.json can override with provenance."""

SAT_AOI_HALF_KM = 1.5          # map half-width around address (matches min-HAND radius)
SAT_GRID_RES_M = 10.0          # output grid (Sentinel-1 native pixel)
SAT_HAND_MAX_M = 15.0          # wet pixels only where HAND < this (slope/shadow guard)
SAT_PERMWATER_PCT = 80.0       # JRC occurrence >= this  => permanent water (excluded)
SAT_PRIOR_WEIGHT_EVENTS = 10.0 # model prior worth ~this many observed events
SAT_EVENT_CLUSTER_DAYS = 14    # exceedance clustering window -> one flood event
SAT_SCENE_PAD_DAYS = 2         # scene counts as observing an event within +/- pad
SAT_MAP_MIN_GEOCODE_M = 100.0  # worse precision -> uncertainty circle, zoomed out
SAT_IOU_ADMIT = 0.6            # S1-self extents admitted to stack at/above this IoU vs EGS
SAT_CI_HATCH_BINS = 2          # hatch uncertainty where CI spans >= this many bins
SAT_S1_DROP_DB = -3.0          # change-detection backscatter drop vs dry reference
SAT_MORPH_OPEN_PX = 2          # binary-opening structure radius (pixels)

# AEP bins (annual exceedance probability), descending, tied 1:1 to report language.
SAT_AEP_BINS = (0.05, 0.02, 0.01, 0.005, 0.002)
SAT_AEP_COLORS = ("#045a8d", "#2b8cbe", "#74a9cf", "#bdc9e1", "#f1eef6")  # dark->light
SAT_PERMWATER_COLOR = "#08306b"
SAT_EXTENT_OUTLINE_COLOR = "#c51b8a"
SAT_SUSCEPT_BANDS_M = (2.0, 5.0, 10.0)               # HAND bands, susceptibility mode
SAT_SUSCEPT_COLORS = ("#5c6b7a", "#8fa3b3", "#c9d4dc")

SAT_PRIOR_HAND_EFOLD_M = 3.0   # prior AEP e-folds per this many m of HAND above
                               # the address (anchor = pipeline's aep_central);
                               # replaced by the learned model form at Lever 3

# --- satellite-only mode (v0.14) ---
SAT_OCC_TO_ANNUAL = 1.0        # occurrence-fraction -> annual wet probability
                               # (interim disclosed form; JRC 'recurrence' or a
                               # learned mapping replaces it at Lever 3)
SAT_PX_DECORR = 25.0           # 30 m pixels are spatially correlated: effective
                               # sample = n_px / this (5x5 blocks)
SAT_ONLY_CHANNEL_KM2 = 5.0     # channel scale when no gauge DA is known
SAT_ONLY_GRADE = "C"           # no in-situ gauge -> grade capped here
SAT_ONLY_GRADE_NO_OCC = "D"    # no occurrence data either -> screening only
SAT_CURVE_BAND_M = 0.5         # HAND band width for the occurrence-AEP curve
SAT_CURVE_MAX_M = 12.0         # curve support; beyond -> lowest band value
SAT_CURVE_MIN_PX = 10          # bands with fewer valid pixels are interpolated

# --- v0.15: risk-level cross-hatching (print- and colorblind-safe: risk is
# encoded twice, hue AND texture, so the map survives grayscale printing) ---
SAT_BIN_HATCHES_ASC = ("", "..", "//", "xx", "++")  # ascending AEP, light->dark
SAT_BIN_HATCH_COLOR = "#08306b"
SAT_UNC_HATCH = "\\\\"                              # uncertainty: backslash, gray

# --- v0.16: wildfire (satellite/archive-only; mirrors the flood constants) ---
FIRE_AEP_BINS = (0.05, 0.02, 0.01, 0.005, 0.002)          # annual burn prob
FIRE_AEP_COLORS = ("#b30000", "#e34a33", "#fc8d59", "#fdcc8a", "#fef0d9")
FIRE_NONFUEL_COLOR = "#9aa5ad"       # water/urban core/rock: not burnable
FIRE_PERIM_COLOR = "#67000d"         # historical burn perimeter outline
FIRE_WUI_DECAY_M = 500.0             # ember/radiant exposure e-fold distance
FIRE_SLOPE_MAX_BOOST = 0.5           # multiplier grows to 1.5x at >=30 deg
FIRE_PRIOR_C = 1.0                   # interim disclosed constant (Lever 3)
FIRE_HIGH_AEP = 0.02                 # >= 1:50  -> High
FIRE_MED_AEP = 0.005                 # >= 1:200 -> Medium
FIRE_NEAR_BURN_BUMP_M = 250.0        # mapped burn within ember distance bumps class
FIRE_GRADE = "C"                     # archive-only cap; D = no NBAC history
FIRE_GRADE_NO_DATA = "D"
FIRE_PIXEL_EVIDENCE_FRACTION = 0.2  # pixel non-burn trials are correlated with
                                    # the regional rate estimated from the SAME
                                    # years: deflate, don't double-count
FIRE_REACH_RADIUS_M = 250.0         # classify on max probability within ember
                                    # distance: fire at the fence IS the risk
FIRE_NBAC_START_YEAR = 1972      # NBAC archive start -> record length
FIRE_NBAC_END_YEAR_MIN = 2024    # floor for record end if local file is older


# v0.22 B2: the regional burn RATE is computed over this radius, not the 3 km
# risk-map window (a single peninsula/town rarely contains a burn; the region
# carries the signal). Overridable per-run with --radius.
FIRE_REGIONAL_RATE_KM = 25.0
# v0.22 fix-1: when the regional history shows N burn-years over the record,
# the base rate cannot honestly be zero -- floor it at this fraction of the
# observed regional burn frequency so a populated history never sits beside a
# 0.00%% number. Disclosed in the chain when it binds.
FIRE_RATE_FLOOR_FROM_HISTORY = True
