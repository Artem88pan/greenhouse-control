

from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Dict, Iterable, List, Optional, Tuple
from uuid import UUID

from sqlmodel import Session, select

from src.models import Region, Greenhouse
from src.models_measurements import Measurement, MeasurementKind


TEMP_MIN, TEMP_MAX = 18.0, 28.0
HUM_MIN, HUM_MAX = 45.0, 85.0
PH_MIN,  PH_MAX  = 5.8,  7.4


@dataclass
class Triplet:
    dt: datetime
    t: Optional[float]
    phi: Optional[float]
    pH: Optional[float]


@dataclass
class MetricAgg:
    avg: Optional[float]
    min: Optional[float]
    max: Optional[float]


@dataclass
class GHStats:
    greenhouse_id: UUID
    greenhouse_name: str
    region_id: UUID
    region_name: str
    points: int                  # кол-во временных точек (строк-триплетов)
    violations: int              # сколько из них нарушили диапазоны
    state: int                   # 0/1/2
    t: MetricAgg
    phi: MetricAgg
    pH: MetricAgg


@dataclass
class RegionStats:
    region_id: UUID
    region_name: str
    points: int
    violations: int
    state: int
    t: MetricAgg
    phi: MetricAgg
    pH: MetricAgg


def _state_by_ratio(ratio: float) -> int:
    if ratio < 0.10:
        return 0
    if ratio < 0.30:
        return 1
    return 2


def _triplets_from_rows(rows: Iterable[Measurement]) -> Dict[UUID, List[Triplet]]:

    tmp: Dict[Tuple[UUID, datetime], Dict[str, Optional[float]]] = {}
    for m in rows:
        key = (m.greenhouse_id, m.dt)
        if key not in tmp:
            tmp[key] = {"t": None, "phi": None, "pH": None, "dt": m.dt}
        if m.t is not None:
            tmp[key]["t"] = m.t
        if m.phi is not None:
            tmp[key]["phi"] = m.phi
        if m.pH is not None:
            tmp[key]["pH"] = m.pH

    by_gh: Dict[UUID, List[Triplet]] = defaultdict(list)
    for (gid, _dt), v in tmp.items():
        by_gh[gid].append(Triplet(
            dt=v["dt"],
            t=v["t"],
            phi=v["phi"],
            pH=v["pH"],
        ))
    # сортировка по времени для аккуратности
    for gid in by_gh:
        by_gh[gid].sort(key=lambda x: x.dt)
    return by_gh


def _metric_agg(values: List[Optional[float]]) -> MetricAgg:
    nums = [x for x in values if x is not None]
    if not nums:
        return MetricAgg(None, None, None)
    return MetricAgg(
        avg=round(mean(nums), 3),
        min=min(nums),
        max=max(nums),
    )


def _violation(tr: Triplet) -> bool:
    """
    Нарушение, если ХОТЯ БЫ ОДНА присутствующая метрика вне диапазона.
    Отсутствующие значения не учитываем.
    Если все три отсутствуют — не считаем ни нарушением, ни валидной точкой.
    """
    present = 0
    bad = False
    if tr.t is not None:
        present += 1
        bad = bad or not (TEMP_MIN <= tr.t <= TEMP_MAX)
    if tr.phi is not None:
        present += 1
        bad = bad or not (HUM_MIN <= tr.phi <= HUM_MAX)
    if tr.pH is not None:
        present += 1
        bad = bad or not (PH_MIN <= tr.pH <= PH_MAX)
    if present == 0:
        return False
    return bad


def _compute_stats_for_gh(
    gid: UUID,
    gname: str,
    rid: UUID,
    rname: str,
    triplets: List[Triplet],
) -> GHStats:

    valid = [tr for tr in triplets if tr.t is not None or tr.phi is not None or tr.pH is not None]
    points = len(valid)
    violations = sum(1 for tr in valid if _violation(tr))
    ratio = (violations / points) if points else 0.0
    state = _state_by_ratio(ratio)

    t_agg   = _metric_agg([tr.t for tr in valid])
    phi_agg = _metric_agg([tr.phi for tr in valid])
    ph_agg  = _metric_agg([tr.pH for tr in valid])

    return GHStats(
        greenhouse_id=gid,
        greenhouse_name=gname,
        region_id=rid,
        region_name=rname,
        points=points,
        violations=violations,
        state=state,
        t=t_agg, phi=phi_agg, pH=ph_agg,
    )


def greenhouse_stats_last24h(session: Session, region_filter: Optional[UUID] = None) -> List[GHStats]:

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    frm = now - timedelta(hours=24)

    # справочники
    regions = {r.id: r for r in session.exec(select(Region)).all()}
    ghs = session.exec(select(Greenhouse)).all()
    if region_filter:
        ghs = [g for g in ghs if g.region_id == region_filter]
    gh_by_id = {g.id: g for g in ghs}
    gh_ids = list(gh_by_id.keys())
    if not gh_ids:
        return []


    rows = session.exec(
        select(Measurement)
        .where(Measurement.kind == MeasurementKind.clean)
        .where(Measurement.greenhouse_id.in_(gh_ids))
        .where(Measurement.dt >= frm)
        .where(Measurement.dt <= now)
    ).all()

    by_gh = _triplets_from_rows(rows)

    stats: List[GHStats] = []
    for gid, trips in by_gh.items():
        g = gh_by_id.get(gid)
        if not g:
            continue
        r = regions.get(g.region_id)
        stats.append(_compute_stats_for_gh(gid, g.name, g.region_id, r.name if r else "", trips))
    for gid in gh_ids:
        if gid not in by_gh:
            g = gh_by_id[gid]
            r = regions.get(g.region_id)
            stats.append(_compute_stats_for_gh(g.id, g.name, g.region_id, r.name if r else "", []))

    return stats


def region_stats_last24h(session: Session) -> List[RegionStats]:

    gh_stats = greenhouse_stats_last24h(session, region_filter=None)


    by_region: Dict[UUID, List[GHStats]] = defaultdict(list)
    names: Dict[UUID, str] = {}
    for s in gh_stats:
        by_region[s.region_id].append(s)
        names[s.region_id] = s.region_name

    out: List[RegionStats] = []
    for rid, items in by_region.items():

        def agg_metric(getter):
            avgs = [getter(i).avg for i in items if getter(i).avg is not None]
            mins = [getter(i).min for i in items if getter(i).min is not None]
            maxs = [getter(i).max for i in items if getter(i).max is not None]
            return MetricAgg(
                avg=round(mean(avgs), 3) if avgs else None,
                min=min(mins) if mins else None,
                max=max(maxs) if maxs else None,
            )

        points = sum(i.points for i in items)
        violations = sum(i.violations for i in items)
        ratio = (violations / points) if points else 0.0
        state = _state_by_ratio(ratio)

        out.append(RegionStats(
            region_id=rid,
            region_name=names.get(rid, ""),
            points=points,
            violations=violations,
            state=state,
            t=agg_metric(lambda x: x.t),
            phi=agg_metric(lambda x: x.phi),
            pH=agg_metric(lambda x: x.pH),
        ))

    return out
