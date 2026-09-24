"""HazardWise satellite evidence pipeline (v0.13).

Modules mirror the design doc: aoi -> catalog -> events -> s1_flood ->
extent_stack -> frequency -> surface -> render, plus integrity checks.
All constants live in params_sat (merge into hazardwise/params.py on integration).
"""
from . import params_sat  # noqa: F401
