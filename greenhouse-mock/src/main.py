import os
import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

app = FastAPI(title="Greenhouse Mock")
security = HTTPBasic()

# --- Basic Auth (берём из .env) ---
GH_USER = os.getenv("GH_USERNAME", "greenhouse_user")
GH_PASS = os.getenv("GH_PASSWORD", "greenhouse_pass")

def auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not (credentials.username == GH_USER and credentials.password == GH_PASS):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )

# --- Модели запрос/ответ ---
class SeriesRequest(BaseModel):
    greenhouses: List[UUID]
    dt_from: Optional[datetime] = None
    dt_to: Optional[datetime] = None

class SeriesPoint(BaseModel):
    dt: datetime = Field(..., serialization_alias="datetime")
    value: Optional[float]

class SeriesResponse(BaseModel):
    id: UUID
    data: List[SeriesPoint]

# --- Вспомогательные функции ---
def _region_for_greenhouse(gid: UUID) -> UUID:
    regions = [
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
        UUID("33333333-3333-3333-3333-333333333333"),
    ]
    return regions[hash(gid) % len(regions)]

def _hourly_range(dt_from: Optional[datetime], dt_to: Optional[datetime]) -> List[datetime]:
    if dt_to is None:
        dt_to = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if dt_from is None:
        dt_from = dt_to - timedelta(hours=23)
    dt_from = dt_from.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    dt_to = dt_to.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if dt_from > dt_to:
        dt_from, dt_to = dt_to, dt_from
    out, cur = [], dt_from
    while cur <= dt_to:
        out.append(cur)
        cur += timedelta(hours=1)
    return out

def _gen_value(metric: str, seed: int) -> float:
    random.seed(seed + 12345)
    if metric == "temperature":   # около 23±5
        return 23 + 5 * random.uniform(-0.6, 0.6)
    if metric == "humidity":      # около 65±20
        return 65 + 20 * random.uniform(-0.5, 0.5)
    if metric.lower() == "ph":    # 6.6±0.8
        return 6.6 + 0.8 * random.uniform(-0.5, 0.5)
    return 0.0

def _points(metric: str, hours: List[datetime]) -> List[SeriesPoint]:
    data: List[SeriesPoint] = []
    for i, dt in enumerate(hours):
        v = _gen_value(metric, i)
        # 10% пропусков
        if random.random() < 0.10:
            data.append(SeriesPoint(dt=dt, value=None))
            continue
        # 5% выбросов (±30%)
        if random.random() < 0.05:
            v *= random.choice([1.3, 0.7])
        # округление по типу метрики
        if metric == "humidity":
            v = round(v, 1)
        else:
            v = round(v, 2)
        data.append(SeriesPoint(dt=dt, value=v))
    return data

# --- Эндпоинты мока ---

@app.get("/greenhouse_info/{id}", dependencies=[Depends(auth)])
def greenhouse_info(id: UUID):
    return {
        "id": id,
        "name": f"Greenhouse-{str(id)[:8]}",
        "region": _region_for_greenhouse(id),
    }

@app.post("/temperature/", response_model=list[SeriesResponse], dependencies=[Depends(auth)])
def temperature(req: SeriesRequest):
    hours = _hourly_range(req.dt_from, req.dt_to)
    return [{"id": gid, "data": _points("temperature", hours)} for gid in req.greenhouses]

@app.post("/humidity/", response_model=list[SeriesResponse], dependencies=[Depends(auth)])
def humidity(req: SeriesRequest):
    hours = _hourly_range(req.dt_from, req.dt_to)
    return [{"id": gid, "data": _points("humidity", hours)} for gid in req.greenhouses]

@app.post("/pH/", response_model=list[SeriesResponse], dependencies=[Depends(auth)])
def ph_endpoint(req: SeriesRequest):
    hours = _hourly_range(req.dt_from, req.dt_to)
    return [{"id": gid, "data": _points("pH", hours)} for gid in req.greenhouses]

# вспомогательные
@app.get("/")
def root():
    return {"service": "greenhouse-mock", "ok": True}

@app.get("/health")
def health():
    return {"status": "ok"}
