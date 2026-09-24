"""Single entry point for the existing report generator. Wire this where the
HTML/JSON report is assembled; everything upstream (gauge selection, HAND window,
AEP curve) is what the repo already produces."""
import numpy as np
from .aoi import AOI
from .extent_stack import ExtentStack
from .frequency import effective_n
from .events import annual_event_rate
from .surface import fuse
from .render import render_map
from .integrity import run_all

def build_map_section(lat, lon, hand_m, prior_aep, events, obs_table,
                      record_years, stack_layers, occurrence_pct=None,
                      gauge_case="GAUGE_OK", geocode_precision_m=10.0,
                      grade=None, out_png="flood_map.png",
                      constants_stamp="Model constants: DEFAULT",
                      era_start=None, date_range=""):
    aoi = AOI(lat, lon, geocode_precision_m=geocode_precision_m)
    stack = ExtentStack(aoi.n, hand_m)
    if occurrence_pct is not None:
        stack.set_permanent_water_from_occurrence(occurrence_pct)
    for layer in stack_layers:
        stack.add(layer, era_start=era_start)
    lam = annual_event_rate(events, record_years) if events else 0.0
    n_eff = effective_n(obs_table) if obs_table is not None and len(obs_table) else 0.0
    k = stack.wet_event_count()
    fusion = fuse(prior_aep, k, n_eff, max(lam, 1e-6), gauge_case=gauge_case)
    if gauge_case == "UNGAUGED" and n_eff <= 0:
        fusion.mode = "susceptibility"
    meta = render_map(aoi, fusion, hand_m, stack, out_png,
                      constants_stamp=constants_stamp, date_range=date_range,
                      grade=grade)
    integ = run_all(grade, fusion, stack, meta)
    return {"aoi": aoi, "fusion": fusion, "stack": stack,
            "map_meta": meta, "integrity": integ,
            "address_aep": float(fusion.aep[aoi.address_rc])
                           if fusion.mode == "probability" else None}
