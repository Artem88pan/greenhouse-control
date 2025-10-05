
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from uuid import UUID
from datetime import datetime

@dataclass
class Fix:
    greenhouse_id: UUID
    dt: datetime
    metric: str
    reason: str
    old_value: Optional[float]
    new_value: Optional[float]


def _pct_diff(a: float, b: float) -> float:
    if a is None or b is None or b == 0:
        return 0.0
    return abs(a - b) / abs(b)


def _linear_interpolate(series: List[Tuple[datetime, Optional[float]]]) -> List[Tuple[datetime, Optional[float]]]:

    xs = series[:]  # (dt, value)
    n = len(xs)
    i = 0
    while i < n:
        if xs[i][1] is not None:
            i += 1
            continue

        j = i
        while j < n and xs[j][1] is None:
            j += 1
        left_idx = i - 1
        right_idx = j
        if left_idx >= 0 and right_idx < n and xs[left_idx][1] is not None and xs[right_idx][1] is not None:
            left_dt, left_v = xs[left_idx]
            right_dt, right_v = xs[right_idx]
            span = (right_dt - left_dt).total_seconds() or 1
            for k in range(i, j):
                t = (xs[k][0] - left_dt).total_seconds() / span
                xs[k] = (xs[k][0], (1 - t) * left_v + t * right_v)
        i = j
    return xs


def clean_by_region_mean(
    values_by_gh: Dict[UUID, List[Tuple[datetime, Optional[float]]]],
    metric: str,
    threshold_pct: float = 0.10,
) -> Tuple[Dict[UUID, List[Tuple[datetime, Optional[float]]]], List[Fix]]:

    fixes: List[Fix] = []


    if not values_by_gh:
        return values_by_gh, fixes

    gh_ids = list(values_by_gh.keys())
    length = len(values_by_gh[gh_ids[0]])


    for idx in range(length):

        vals = [values_by_gh[gid][idx][1] for gid in gh_ids if values_by_gh[gid][idx][1] is not None]
        if not vals:
            continue
        mean_v = sum(vals) / len(vals)
        for gid in gh_ids:
            dt, v = values_by_gh[gid][idx]
            if v is None:
                continue
            if _pct_diff(v, mean_v) > threshold_pct:
                fixes.append(Fix(gid, dt, metric, "outlier", v, None))
                values_by_gh[gid][idx] = (dt, None)


    for gid in gh_ids:
        before = values_by_gh[gid][:]
        after = _linear_interpolate(before)

        for (dt1, old), (_, new) in zip(before, after):
            if old is None and new is not None:
                fixes.append(Fix(gid, dt1, metric, "interp", None, new))
        values_by_gh[gid] = after

    return values_by_gh, fixes
