"""Flood-event catalog from gauge hydrographs; scene-to-event matching;
detection-probability correction. Unit of evidence = EVENT, never scene."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from . import params_sat as P

@dataclass
class FloodEvent:
    event_id: str
    start: pd.Timestamp
    end: pd.Timestamp
    peak: float

    @property
    def duration_days(self) -> float:
        return max((self.end - self.start).days + 1, 1)

def cluster_events(series: pd.Series, threshold: float,
                   window_days: int = P.SAT_EVENT_CLUSTER_DAYS):
    """series: datetime-indexed flow/stage. Exceedances within window_days merge."""
    exc = series[series >= threshold].sort_index()
    events, cur = [], None
    for t, v in exc.items():
        if cur is None:
            cur = [t, t, v]
        elif (t - cur[1]).days <= window_days:
            cur[1] = t; cur[2] = max(cur[2], v)
        else:
            events.append(cur); cur = [t, t, v]
    if cur is not None:
        events.append(cur)
    return [FloodEvent(f"E{i:03d}", s, e, p) for i, (s, e, p) in enumerate(events)]

def scenes_for_event(ev: FloodEvent, scene_times,
                     pad_days: int = P.SAT_SCENE_PAD_DAYS):
    t0 = ev.start - pd.Timedelta(days=pad_days)
    t1 = ev.end + pd.Timedelta(days=pad_days)
    return [t for t in scene_times if t0 <= t <= t1]

def capture_probability(ev: FloodEvent, revisit_days: float) -> float:
    """P(at least one scene overlaps the event) under a regular revisit."""
    return float(min(1.0, ev.duration_days / max(revisit_days, 1e-6)))

def event_observation_table(events, scene_times, revisit_days: float) -> pd.DataFrame:
    rows = []
    for ev in events:
        sc = scenes_for_event(ev, scene_times)
        rows.append({"event_id": ev.event_id, "start": ev.start, "end": ev.end,
                     "n_scenes": len(sc), "observed": len(sc) > 0,
                     "capture_p": capture_probability(ev, revisit_days)})
    return pd.DataFrame(rows)

def annual_event_rate(events, record_years: float) -> float:
    return len(events) / max(record_years, 1e-6)
