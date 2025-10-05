from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple, Optional
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Depends, Body, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import select, Session

from src.db import create_tables, get_session
# если в db.py есть engine, используем его для фабрики сессий
try:
    from src.db import engine as _ENGINE
except Exception:
    _ENGINE = None

from src.models import Region, Greenhouse
from src.models_measurements import (
    Measurement,
    DataFix,
    StateHistory,
    MeasurementKind,
)
from src.services import greenhouse_client as gh
from src.services.cleaning import clean_by_region_mean
from src.services.stats import greenhouse_stats_last24h, region_stats_last24h
from src.services import state_client

log = logging.getLogger("control")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Control")

# простое in-memory хранилище статусов задач «Оценки»
TASKS: Dict[str, Dict[str, Optional[int] | str]] = {}


# --------------- вспомогательные утилиты -----------------

def session_factory() -> Session:
    """
    Фабрика обычной Session без Depends — нужна для фоновой задачи.
    """
    if _ENGINE is None:
        # fallback: открыть через Depends-генератор (не идеально, но на тест хватит)
        gen = get_session()
        s = next(gen)  # type: ignore
        return s
    return Session(_ENGINE)


def _triplets_for_eval(session: Session, greenhouse_id: UUID, frm: datetime, to: datetime):
    """
    Собираем CLEAN-точки в формат для state-mock:
    [{"dt": "...Z", "t": ..., "phi": ..., "pH": ...}, ...]
    """
    rows = session.exec(
        select(Measurement)
        .where(Measurement.kind == MeasurementKind.clean)
        .where(Measurement.greenhouse_id == greenhouse_id)
        .where(Measurement.dt >= frm).where(Measurement.dt <= to)
        .order_by(Measurement.dt.asc())
    ).all()

    by_dt: Dict[datetime, Dict[str, Optional[float] | datetime]] = {}
    for m in rows:
        d = by_dt.setdefault(m.dt, {"dt": m.dt})
        if m.t is not None:
            d["t"] = m.t
        if m.phi is not None:
            d["phi"] = m.phi
        if m.pH is not None:
            d["pH"] = m.pH

    out = []
    for d, vals in sorted(by_dt.items()):
        if any(k in vals for k in ("t", "phi", "pH")):
            out.append({
                "dt": vals["dt"].isoformat().replace("+00:00", "Z"),
                "t": vals.get("t"),
                "phi": vals.get("phi"),
                "pH": vals.get("pH"),
            })
    return out


async def _evaluate_and_store(task_id: str, greenhouse_id: UUID, hours: int):
    """
    Фоновая задача: собрать CLEAN-ряд за окно, вызвать state-mock, записать результат в StateHistory.
    """
    TASKS[task_id] = {"status": "pending", "result": None}
    try:
        # соберём payload из CLEAN данных
        with session_factory() as s:
            now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            frm = now - timedelta(hours=hours)
            payload = _triplets_for_eval(s, greenhouse_id, frm, now)

        async with httpx.AsyncClient() as client:
            state = await state_client.evaluate(client, payload)

        # записываем историю
        with session_factory() as s:
            s.add(StateHistory(greenhouse_id=greenhouse_id, dt=datetime.now(timezone.utc), state=state))
            s.commit()

        TASKS[task_id] = {"status": "done", "result": int(state)}
    except Exception as e:
        log.exception("evaluate task failed: %s", e)
        TASKS[task_id] = {"status": "error", "result": None}


# ----------------- события приложения --------------------

@app.on_event("startup")
def on_startup():
    create_tables()
    log.info("Tables are ready")


# ----------------- health / root -------------------------

@app.get("/")
def root():
    return {"service": "control", "ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db(session: Session = Depends(get_session)):
    session.exec(select(Region).limit(1))
    return {"db": "ok"}


# ----------------- демо-наполнение -----------------------

@app.post("/seed/demo")
def seed_demo(session: Session = Depends(get_session)):
    if session.exec(select(Region)).first():
        return {"note": "already seeded"}
    r1 = Region(name="North")
    r2 = Region(name="South")
    session.add_all([r1, r2]); session.flush()
    ghs = [Greenhouse(name=f"N-GH-{i}", region_id=r1.id) for i in range(1, 4)]
    ghs += [Greenhouse(name=f"S-GH-{i}", region_id=r2.id) for i in range(1, 4)]
    session.add_all(ghs); session.commit()
    return {"regions": 2, "greenhouses": len(ghs)}


# ----------------- обновление из «Теплицы» ---------------

@app.post("/refresh")
async def refresh(
    greenhouse_ids: List[UUID] = Body(default=None, description="Если не задано — берём все из БД"),
    session: Session = Depends(get_session),
):
    if not greenhouse_ids:
        greenhouse_ids = [g.id for g in session.exec(select(Greenhouse)).all()]
        if not greenhouse_ids:
            raise HTTPException(400, "Нет теплиц в БД. Сначала POST /seed/demo")

    # карта теплица -> регион
    gh_rows = session.exec(select(Greenhouse)).all()
    region_by_gh: Dict[UUID, UUID] = {g.id: g.region_id for g in gh_rows if g.id in greenhouse_ids}

    async with httpx.AsyncClient() as client:
        try:
            temps = await gh.get_temperature(client, greenhouse_ids)
            hums = await gh.get_humidity(client, greenhouse_ids)
            phs = await gh.get_ph(client, greenhouse_ids)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"greenhouse-mock error: {e!s}")

    def to_dict(arr, key="data") -> Dict[UUID, List[Tuple[datetime, float | None]]]:
        out: Dict[UUID, List[Tuple[datetime, float | None]]] = {}
        for item in arr:
            gid = UUID(item["id"])
            pairs = [(datetime.fromisoformat(p["datetime"]), p["value"]) for p in item[key]]
            out[gid] = pairs
        return out

    t_by = to_dict(temps)
    h_by = to_dict(hums)
    ph_by = to_dict(phs)

    # 3) Запись RAW
    raw_count = 0
    for gid, series in t_by.items():
        for dt, v in series:
            session.add(Measurement(greenhouse_id=gid, dt=dt, t=v, kind=MeasurementKind.raw))
            raw_count += 1
    for gid, series in h_by.items():
        for dt, v in series:
            session.add(Measurement(greenhouse_id=gid, dt=dt, phi=v, kind=MeasurementKind.raw))
            raw_count += 1
    for gid, series in ph_by.items():
        for dt, v in series:
            session.add(Measurement(greenhouse_id=gid, dt=dt, pH=v, kind=MeasurementKind.raw))
            raw_count += 1
    session.commit()
    log.info("saved raw rows: %s", raw_count)

    # 4) Очистка ПО РЕГИОНАМ
    ids_by_region: Dict[UUID, List[UUID]] = {}
    for gid, rid in region_by_gh.items():
        ids_by_region.setdefault(rid, []).append(gid)

    total_clean = 0
    total_fixes = 0

    for region_id, ids in ids_by_region.items():
        t_clean, t_fixes = clean_by_region_mean({gid: t_by[gid] for gid in ids}, "t", threshold_pct=0.10)
        h_clean, h_fixes = clean_by_region_mean({gid: h_by[gid] for gid in ids}, "phi", threshold_pct=0.10)
        ph_clean, ph_fixes = clean_by_region_mean({gid: ph_by[gid] for gid in ids}, "pH", threshold_pct=0.10)

        for gid, series in t_clean.items():
            for (dt, v) in series:
                session.add(Measurement(greenhouse_id=gid, dt=dt, t=v, kind=MeasurementKind.clean))
                total_clean += 1
        for gid, series in h_clean.items():
            for (dt, v) in series:
                session.add(Measurement(greenhouse_id=gid, dt=dt, phi=v, kind=MeasurementKind.clean))
                total_clean += 1
        for gid, series in ph_clean.items():
            for (dt, v) in series:
                session.add(Measurement(greenhouse_id=gid, dt=dt, pH=v, kind=MeasurementKind.clean))
                total_clean += 1

        for fx in [*t_fixes, *h_fixes, *ph_fixes]:
            session.add(DataFix(
                greenhouse_id=fx.greenhouse_id, dt=fx.dt,
                metric=fx.metric, reason=fx.reason,
                old_value=fx.old_value, new_value=fx.new_value
            ))
            total_fixes += 1

    session.commit()
    log.info("saved clean rows: %s; fixes: %s", total_clean, total_fixes)

    return {"raw_saved": raw_count, "clean_saved": total_clean, "fixes_logged": total_fixes}


# ----------------- статистика ----------------------------

def _sort_items(items, key: str, order: str):
    rev = (order == "desc")
    try:
        return sorted(items, key=lambda x: getattr(x, key), reverse=rev)
    except Exception:
        return items


@app.get("/stats/greenhouses")
def stats_greenhouses(
    region_id: Optional[UUID] = Query(default=None),
    state: Optional[int] = Query(default=None, ge=0, le=2),
    sort: str = Query(default="greenhouse_name", description="state|greenhouse_name|region_name|points|violations|t|phi|pH"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    session: Session = Depends(get_session),
):
    stats = greenhouse_stats_last24h(session, region_filter=region_id)

    if state is not None:
        stats = [s for s in stats if s.state == state]

    # сортировка
    if sort in {"t", "phi", "pH"}:
        stats.sort(key=lambda s: getattr(s, sort).avg or -1e18, reverse=(order == "desc"))
    elif sort in {"state", "greenhouse_name", "region_name", "points", "violations"}:
        stats.sort(key=lambda s: getattr(s, sort), reverse=(order == "desc"))
    else:
        stats.sort(key=lambda s: s.greenhouse_name)

    return [
        {
            "greenhouse_id": str(s.greenhouse_id),
            "greenhouse_name": s.greenhouse_name,
            "region_id": str(s.region_id),
            "region_name": s.region_name,
            "points": s.points,
            "violations": s.violations,
            "state": s.state,
            "t": s.t.__dict__,
            "phi": s.phi.__dict__,
            "pH": s.pH.__dict__,
        }
        for s in stats
    ]


@app.get("/stats/regions")
def stats_regions(
    state: Optional[int] = Query(default=None, ge=0, le=2),
    sort: str = Query(default="region_name", description="state|region_name|points|violations|t|phi|pH"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    session: Session = Depends(get_session),
):
    stats = region_stats_last24h(session)

    if state is not None:
        stats = [s for s in stats if s.state == state]

    if sort in {"t", "phi", "pH"}:
        stats.sort(key=lambda s: getattr(s, sort).avg or -1e18, reverse=(order == "desc"))
    elif sort in {"state", "region_name", "points", "violations"}:
        stats.sort(key=lambda s: getattr(s, sort), reverse=(order == "desc"))
    else:
        stats.sort(key=lambda s: s.region_name)

    return [
        {
            "region_id": str(s.region_id),
            "region_name": s.region_name,
            "points": s.points,
            "violations": s.violations,
            "state": s.state,
            "t": s.t.__dict__,
            "phi": s.phi.__dict__,
            "pH": s.pH.__dict__,
        }
        for s in stats
    ]


# ----------------- оценка и история ----------------------

@app.post("/evaluate/{greenhouse_id}")
async def evaluate_greenhouse(
    greenhouse_id: UUID,
    hours: int = Query(default=24 * 30, ge=1, le=24 * 60, description="Период для оценки, часов (по умолчанию 30 дней)"),
):
    # проверим, что теплица существует
    with session_factory() as s:
        gh = s.get(Greenhouse, greenhouse_id)
        if not gh:
            raise HTTPException(404, "Greenhouse not found")

    task_id = uuid4().hex
    asyncio.create_task(_evaluate_and_store(task_id, greenhouse_id, hours))
    return {"task_id": task_id}


@app.get("/tasks/{task_id}")
def task_status(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.get("/greenhouse/{greenhouse_id}/state-history")
def greenhouse_state_history(
    greenhouse_id: UUID,
    dt_from: Optional[datetime] = Query(default=None),
    dt_to: Optional[datetime] = Query(default=None),
    session: Session = Depends(get_session),
):
    q = select(StateHistory).where(StateHistory.greenhouse_id == greenhouse_id)
    if dt_from:
        q = q.where(StateHistory.dt >= dt_from)
    if dt_to:
        q = q.where(StateHistory.dt <= dt_to)
    rows = session.exec(q.order_by(StateHistory.dt.desc())).all()
    return [
        {"id": r.id, "greenhouse_id": str(r.greenhouse_id), "dt": r.dt.isoformat(), "state": r.state}
        for r in rows
    ]


# ----------------- ручная корректировка ------------------

class MeasurementPatch(BaseModel):
    t: Optional[float] = None
    phi: Optional[float] = None
    pH: Optional[float] = None


@app.patch("/measurement/{measurement_id}")
def patch_measurement(
    measurement_id: int,
    body: MeasurementPatch,
    session: Session = Depends(get_session),
):
    row = session.get(Measurement, measurement_id)
    if not row:
        raise HTTPException(404, "Measurement not found")

    fields_changed: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

    if body.t is not None and body.t != row.t:
        fields_changed["t"] = (row.t, body.t)
        row.t = body.t
    if body.phi is not None and body.phi != row.phi:
        fields_changed["phi"] = (row.phi, body.phi)
        row.phi = body.phi
    if body.pH is not None and body.pH != row.pH:
        fields_changed["pH"] = (row.pH, body.pH)
        row.pH = body.pH

    if not fields_changed:
        return {"updated": False, "note": "no changes"}

    for metric, (old, new) in fields_changed.items():
        session.add(DataFix(
            greenhouse_id=row.greenhouse_id,
            dt=row.dt,
            metric=metric,
            reason="manual",
            old_value=old,
            new_value=new,
        ))

    session.commit()
    return {"updated": True, "measurement_id": measurement_id, "changed": list(fields_changed.keys())}
