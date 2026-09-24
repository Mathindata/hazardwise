# HazardWise — Quick Install & Usage (satellite mode, Windows) — v0.22

Flood + wildfire risk for any Canadian address from satellite/archive data.
Full model math: WHITEPAPER.md · full reference: SETUP.md · roadmap:
IMPROVEMENT_PLAN.md.

## 1. Install
```powershell
cd C:\hazardwise            # extract the zip here, NOT in Downloads
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Python 3.11+ ("Add to PATH" checked). If activation is blocked:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## 2. Verify (offline, ~5 min)
```powershell
$env:HW_BOOTSTRAP_N="100"; python -m pytest hazardwise/tests -q
```

## 3. Wildfire data (one file, once)
Download the MERGED **`NBAC_1972to2025_*_shp.zip`** (name must contain
`1972to20xx`) from https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/ into
`%USERPROFILE%\.hazardwise\nbac\`. Do NOT set HW_NBAC_DIR unless you need a
non-default path. Verify:
```powershell
python -m hazardwise.satellite.nbac 52.8734 -118.0806
```
PASS = "Record length: 54 years" + a "2024: N burned pixels" line.
Flood needs no data install (streamed live — stay online).

## 4. Run reports
```powershell
# flood only
python -m hazardwise.sat_pipeline  "309B Macleod Trail SW, High River, AB"
# wildfire only
python -m hazardwise.fire_pipeline "Connaught Drive, Jasper, AB"
# BOTH in one report (two maps, two justification chains, regional history)
python -m hazardwise.dual_report   "Connaught Drive, Jasper, AB"
# coordinates for unresolvable rural addresses
python -m hazardwise.sat_pipeline --lat 53.65 --lon -114.47 "cottage"
```
Rural tip: include the community/village
("...Lakeshore Drive, Alberta Beach, AB") — a county-only string resolves to a
centroid (disclosed with a red LOCATION IMPRECISE banner).

## 5. Options (all commands)
```powershell
# regional history/rate radius (default 25 km) — wider ring, more context
python -m hazardwise.dual_report "Connaught Drive, Jasper, AB" --radius 50

# debug trace: writes reports_debug\<addr>_<ts>.json + .txt (inputs, resolved
# coords, rate decomposition r_local/r_hist, array stats, fallbacks)
python -m hazardwise.dual_report "Connaught Drive, Jasper, AB" --debug

# stream the trace to the console too (pipe into Claude Desktop for help):
python -m hazardwise.dual_report "Connaught Drive, Jasper, AB" --debug stdout

# map look:
python -m hazardwise.dual_report "..." --basemap aerial            # satellite imagery under the colors
python -m hazardwise.dual_report "..." --basemap street --basemap-opacity 0.4
python -m hazardwise.dual_report "..." --opacity 0.7               # hazard layer strength
python -m hazardwise.dual_report "..." --flood-colors "#f7fbff,#c6dbef,#6baed6,#2171b5,#08306b"
python -m hazardwise.dual_report "..." --fire-colors  "#fff5eb,#fdd0a2,#fd8d3c,#e6550d,#a63603"
python -m hazardwise.dual_report "..." --no-hatch

# combine freely:
python -m hazardwise.dual_report "Connaught Drive, Jasper, AB" `
    --radius 40 --basemap aerial --opacity 0.65 --debug stdout
```

## 6. Batch & validation
```powershell
python -m hazardwise.combined_report --file hazardwise\validation\panel_addresses.csv
python -m hazardwise.run_fire_validation
python -m hazardwise.run_sat_validation
```
`panel_addresses.csv` is the editable ground-truth store.

## 7. Web app + invitation link
```powershell
python -m hazardwise.webapp                       # http://localhost:8077
$env:HW_INVITE_TOKEN="a-long-secret"; python -m hazardwise.webapp
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8077    # share <host>/invite/<token>
```

## What v0.22 changed
- **B2 regional rate**: the wildfire base rate is computed over the `--radius`
  ring (default 25 km), not just the 3 km map window. A town/peninsula that
  hasn't itself burned no longer reads a false 0.00% when the region burns.
- **History rate floor**: when the regional history shows fires, the base rate
  can't be zero — it's floored to the history-implied value and the chain says
  so ("Regional-rate floor applied…"). A populated history can never sit beside
  a 0.00% number again.
- **--radius** and **--debug[ stdout]** options on all report commands.
- Troubleshooting and full flag reference: SETUP.md.
