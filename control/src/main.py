from sqlmodel import select, Session
from fastapi import FastAPI, Depends, Body, HTTPException
from uuid import UUID
from typing import List, Dict, Tuple
import httpx
from datetime import datetime
import logging

from src.db import create_tables, get_session
from src.models import Region, Greenhouse
from src.models_measurements import Measurement, DataFix
from src.services import greenhouse_client as gh
from src.services.cleaning import clean_by_region_mean
log = logging.getLogger("control")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Control")


@app.on_event("startup")
def on_startup():
    create_tables()
    log.info("Tables are ready")


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


@app.post("/seed/demo")
def seed_demo(session: Session = Depends(get_session)):
    if session.exec(select(Region)).first():
        return {"note": "already seeded"}
    r1 = Region(name="North")
    r2 = Region(name="South")
    session.add_all([r1, r2]); session.flush()
    ghs = [Greenhouse(name=f"N-GH-{i}", region_id=r1.id) for i in range(1,4)]
    ghs += [Greenhouse(name=f"S-GH-{i}", region_id=r2.id) for i in range(1,4)]
    session.add_all(ghs); session.commit()
    return {"regions": 2, "greenhouses": len(ghs)}


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


    def to_dict(arr, key="data") -> Dict[UUID, List[Tuple[datetime, float|None]]]:
        out: Dict[UUID, List[Tuple[datetime, float|None]]] = {}
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
            session.add(Measurement(greenhouse_id=gid, dt=dt, t=v, kind="raw"))
            raw_count += 1
    for gid, series in h_by.items():
        for dt, v in series:
            session.add(Measurement(greenhouse_id=gid, dt=dt, phi=v, kind="raw"))
            raw_count += 1
    for gid, series in ph_by.items():
        for dt, v in series:
            session.add(Measurement(greenhouse_id=gid, dt=dt, pH=v, kind="raw"))
            raw_count += 1
    session.commit()
    log.info("saved raw rows: %s", raw_count)

    # 4) Очистка ПО РЕГИОНАМ
    # сгруппируем ids по региону
    ids_by_region: Dict[UUID, List[UUID]] = {}
    for gid, rid in region_by_gh.items():
        ids_by_region.setdefault(rid, []).append(gid)

    total_clean = 0
    total_fixes = 0

    for region_id, ids in ids_by_region.items():
        # по каждой метрике вычислим чистые значения
        t_clean, t_fixes = clean_by_region_mean({gid: t_by[gid] for gid in ids}, "t", threshold_pct=0.10)
        h_clean, h_fixes = clean_by_region_mean({gid: h_by[gid] for gid in ids}, "phi", threshold_pct=0.10)
        ph_clean, ph_fixes = clean_by_region_mean({gid: ph_by[gid] for gid in ids}, "pH", threshold_pct=0.10)

        # 5) Сохраняем CLEAN и фиксы
        for gid, series in t_clean.items():
            for (dt, v) in series:
                session.add(Measurement(greenhouse_id=gid, dt=dt, t=v, kind="clean"))
                total_clean += 1
        for gid, series in h_clean.items():
            for (dt, v) in series:
                session.add(Measurement(greenhouse_id=gid, dt=dt, phi=v, kind="clean"))
                total_clean += 1
        for gid, series in ph_clean.items():
            for (dt, v) in series:
                session.add(Measurement(greenhouse_id=gid, dt=dt, pH=v, kind="clean"))
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
