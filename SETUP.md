# HazardWise — Setup & Usage (satellite edition) — v0.22
*Flood + wildfire risk for Canadian addresses from satellite/archive data.
One address in — evidence-chained report, credible intervals, hatched risk
maps out. Model math: WHITEPAPER.md. This file is the only setup doc you
need; it supersedes WINDOWS_SETUP.md.*

## 1. Requirements
- Windows 10/11 (or any OS; commands below are PowerShell)
- Python 3.11+ from python.org — check **Add to PATH** during install
- Internet while generating reports (geocoding, terrain, and water history
  are streamed live; nothing large is installed for flood)
- ~1 GB disk once, for the wildfire burn archive (step 4)

## 2. Install
```powershell
cd <project folder>
python -m venv .venv
.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
```
Avoid running from `Downloads` (antivirus/OneDrive can lock files);
`C:\hazardwise` is a good home.

## 3. Verify the install (offline, no data needed)
```powershell
$env:HW_BOOTSTRAP_N="100"; python -m pytest hazardwise/tests -q
```
All tests must pass. They use synthetic terrain, water history, and burn
perimeters, so they prove the full pipeline without any downloads.

## 4. One-time data step: the wildfire burn archive (NBAC)
Flood needs nothing installed. Wildfire needs the **merged** National
Burned Area Composite, cached once:

1. Download **`NBAC_1972to2025_*_shp.zip`** (the name MUST contain
   `1972to20xx` — per-year files like `NBAC_1972_...` are rejected by the
   loader, because one year of burns cannot ground a frequency estimate)
   from https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/
2. Place the zip, unmodified, in `%USERPROFILE%\.hazardwise\nbac\`
   (create the folder). Do not set `HW_NBAC_DIR` unless you know you need
   a non-default location — a stale value here is the #1 support issue.
3. **Verify before anything else:**
```powershell
python -m hazardwise.satellite.nbac 52.8734 -118.0806
```
   PASS looks like: your file's name, `Record length: 54 years`, and a
   line `2024: NNNN burned pixels` (the Jasper fire). Anything else, run
   `python diagnose_fire.py` and read its VERDICT — it names the broken
   link (wrong folder, stale HW_NBAC_DIR, single-year file, unreadable
   format, missing YEAR field).

Refresh the file annually when NRCan posts a new composite; the record's
end year is stamped in every report, so a stale archive is visible.

## 5. Generate reports
```powershell
# flood only (satellite-only mode):
python -m hazardwise.sat_pipeline  "309B Macleod Trail SW, High River, AB"
# wildfire only:
python -m hazardwise.fire_pipeline "Connaught Drive, Jasper, AB"
# BOTH hazards, one report, two maps, two justification chains:
python -m hazardwise.dual_report   "Connaught Drive, Jasper, AB"
# unresolvable cottage addresses -> coordinates:
python -m hazardwise.sat_pipeline --lat 50.582 --lon -113.874 "cottage near High River"
```
Each report contains: risk badge + hard-capped evidence grade (**C** is
the ceiling without in-situ data; **D** = screening, no probability
claimed), the line-by-line evidence chain, a risk-index table, credible
range, mandatory caveats, ground-level Street View links, and the map.

**Reading the maps.** Flood = blue bins, wildfire = red bins; hatch
density rises with risk (survives grayscale printing and colorblindness);
gray backslash hatching = wide credible interval; dashed outlines =
observed flood extents / historical burn perimeters; an uncertainty
CIRCLE instead of a pin = centroid-level geocode (no parcel claim made).
Backdrop is an OpenStreetMap street layer at reduced opacity (hillshade
offline).

**Visualization flags** (dual_report and combined_report):
`--basemap street|hillshade|none`, `--basemap-opacity 0.5`,
`--opacity 0.6`, `--flood-colors "#5,hexes,light,to,dark"`,
`--fire-colors ...`, `--no-hatch`.

## 5b. Options & flags (v0.22)

All report commands (`sat_pipeline`, `fire_pipeline`, `dual_report`) accept:

- `--radius KM` — regional history/rate window (default 25). The wildfire
  base rate and the 10/20/50-yr history block both use this ring. Widen it in
  sparse-data regions: `--radius 50`.
- `--debug` — write a structured trace to `reports_debug\<addr>_<timestamp>`
  (`.json` + readable `.txt`): resolved coordinates, the rate decomposition
  (`r_local` from the 3 km window vs `r_hist` from the regional ring, and
  which was used), array summary stats, and every fallback taken. This is the
  file to attach when asking for help or diagnosing a surprising number.
- `--debug stdout` — same trace, also streamed to the console so you can pipe
  it into Claude Desktop.
- Map look: `--basemap street|aerial|hillshade|none`, `--basemap-opacity`,
  `--opacity`, `--flood-colors`/`--fire-colors` (5 hexes, light→dark),
  `--no-hatch`.

Example combining several:
```powershell
python -m hazardwise.dual_report "Connaught Drive, Jasper, AB" `
    --radius 40 --basemap aerial --opacity 0.65 --debug stdout
```

## 6. Batch runs & validation panels
Ground truth lives in one **editable** file:
`hazardwise\validation\panel_addresses.csv`
(`address, expected_flood, expected_fire, note`) — add rows in any editor.
```powershell
# batch: both hazards per address, scored where labels exist
python -m hazardwise.combined_report --file hazardwise\validation\panel_addresses.csv
# single-hazard panels (12 labeled addresses each):
python -m hazardwise.run_sat_validation
python -m hazardwise.run_fire_validation
```
Outputs: per-address report folders + `*_summary.csv` + `index.html`.
Scoring: exact / off-by-one / TWO-STEP (exit nonzero) / UNDETERMINED
counted separately — a refusal is not a miss. Expected behaviour: fire
panel graded only after step 4; urban cores read Low (zero burns in a
54-year record is evidence, not a refusal).

## 7. Web app & invitation link
```powershell
python -m hazardwise.webapp        # -> http://localhost:8077
```
Type an address, get the full report; previous reports are listed.
To share by invitation link:
```powershell
$env:HW_INVITE_TOKEN = "choose-a-long-random-secret"
python -m hazardwise.webapp
# second window:
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8077
```
Share ONLY `https://<tunnel-host>/invite/<token>`; all other requests get
403. Restarting with a new token revokes every link; closing cloudflared
kills the URL. The app binds to localhost only. This is a demo/analyst
channel — client deliveries go through the legal-review wrapper, not here.

## 8. Troubleshooting quick table
| Symptom | Cause | Fix |
|---|---|---|
| Fire = Undetermined everywhere | no NBAC / wrong folder / stale HW_NBAC_DIR | step 4; `python diagnose_fire.py` |
| Fire = Low everywhere incl. Jasper | single-year NBAC file | replace with merged `1972to20xx` file (loader now rejects this loudly) |
| `venvlauncher.exe` copy error | antivirus/OneDrive in Downloads | move project to `C:\hazardwise`, recreate `.venv` |
| `requirements.txt` not found | nested folder from Extract-All | `cd` one level deeper |
| Flood AEP 0.0000 under forest canopy | optical blindness (known limit) | read the caveat; SAR events are the roadmap fix |
| Map has hillshade, not streets | offline or OSM unreachable | rerun online; fail-open is intentional |
| Fire = 0.00% beside recent regional fires | pre-v0.22, or --radius too small | v0.22 floors the rate from history; widen with --radius |
| Need to see why a number is what it is | — | rerun with --debug and read reports_debug\\*.txt |

## 9. What this system will and won't claim
Grades cap at C without in-situ data — a gauged report supersedes a
satellite one wherever a gauge exists. Central values are lower bounds of
their sensing channel (optical misses short/cloudy floods; NBAC misses
small fires) and the caveats say so. Interim functional forms are named in
the reports and are replaced, not tuned. Riverine flood + wildfire only;
pluvial and coastal are out of scope. Every probability-assigning constant
is listed with its status in WHITEPAPER.md S6.
