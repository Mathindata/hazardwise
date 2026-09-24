"""Debug-mode logging (v0.22). When enabled, every report run writes a
structured .json + .txt trace to reports_debug/ capturing inputs, resolved
coordinates, intermediate rasters' summary stats, the rate decomposition,
and any fallbacks — the information needed to diagnose 'why is this number
what it is' without rerunning. Optionally emit to stdout for piping to
Claude Desktop. No effect at all when disabled (zero overhead)."""
from __future__ import annotations
import json, sys, datetime
from pathlib import Path

class DebugLog:
    def __init__(self, enabled=False, out_dir="reports_debug", tag="",
                 to_stdout=False):
        self.enabled = enabled
        self.to_stdout = to_stdout
        self.records = []
        self.tag = tag or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.out_dir = Path(out_dir)

    def log(self, stage, **fields):
        if not self.enabled:
            return
        rec = {"stage": stage, **fields}
        self.records.append(rec)
        if self.to_stdout:
            print(f"[debug] {stage}: "
                  + ", ".join(f"{k}={v}" for k, v in fields.items()),
                  file=sys.stderr)

    def array_stats(self, name, arr):
        if not self.enabled:
            return
        import numpy as np
        a = np.asarray(arr, float)
        self.log(f"array:{name}", shape=list(a.shape),
                 min=float(np.nanmin(a)), max=float(np.nanmax(a)),
                 mean=float(np.nanmean(a)),
                 nonzero=int(np.count_nonzero(a)))

    def flush(self, address):
        if not self.enabled or not self.records:
            return None
        self.out_dir.mkdir(parents=True, exist_ok=True)
        slug = "".join(c if c.isalnum() else "-"
                       for c in address.lower())[:60].strip("-")
        base = self.out_dir / f"{slug}_{self.tag}"
        base.with_suffix(".json").write_text(
            json.dumps({"address": address, "trace": self.records},
                       indent=2, default=str))
        lines = [f"HazardWise debug trace — {address}",
                 f"generated {datetime.datetime.now():%Y-%m-%d %H:%M}", ""]
        for r in self.records:
            lines.append(f"[{r['stage']}] " + ", ".join(
                f"{k}={v}" for k, v in r.items() if k != "stage"))
        base.with_suffix(".txt").write_text("\n".join(lines))
        if self.to_stdout:
            print(f"[debug] trace written: {base}.json / .txt",
                  file=sys.stderr)
        return base
