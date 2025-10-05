"""
Эмулятор сервиса «Оценка».

Задача по ТЗ:
POST /greenhouse_state принимает список точек [(datetime, t, phi, pH), ...]
и возвращает состояние 0/1/2. Алгоритм занимает 10–15 минут.

Что мы эмулируем:
- Basic-аутентификацию.
- "Долгую" обработку через time.sleep, НО длительность настраивается
  переменной окружения STATE_DELAY_SEC (по умолчанию 5 сек, чтобы удобно демить).
  Для строгой демонстрации можно поставить 600–900 сек (10–15 мин).

- Сам алгоритм простенький, "похожий на реальность":
  * нормой считаем: 18°C ≤ t ≤ 28°C, 45% ≤ φ (phi) ≤ 85%, 5.8 ≤ pH ≤ 7.4
  * считаем долю точек вне допустимого диапазона по каждой метрике
  * на основе долей выбираем state: 0 (норма) / 1 (предупреждение) / 2 (угроза)

"""

import os
import time
from datetime import datetime
from typing import List, Tuple

from fastapi import FastAPI, Depends, HTTPException, status, Body
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, field_validator, RootModel


app = FastAPI(title="State Mock (Оценка)")
security = HTTPBasic()

STATE_USER = os.getenv("STATE_USERNAME", "state_user")
STATE_PASS = os.getenv("STATE_PASSWORD", "state_pass")
STATE_DELAY_SEC = int(os.getenv("STATE_DELAY_SEC", "5"))

def auth(credentials: HTTPBasicCredentials = Depends(security))->None:
    if not (credentials.username == STATE_USER and credentials.password == STATE_PASS):
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = "Unauthorized",
            headers = {"WWW-Authenticate": "Basic"},
        )

class Point(BaseModel):
    dt: datetime
    t: float
    phi: float
    pH: float

    @field_validator("t", "phi", "pH", mode="before")
    @classmethod
    def _to_float(cls, v):
        return float(v)

class StateRequest(RootModel[List[Point]]):
    def points(self) -> List[Point]:
        return self.roo

TEMP_MIN, TEMP_MAX = 18.0, 28.0
HUM_MIN, HUM_MAX = 45.0, 85.0
PH_MIN, PH_MAX = 5.8, 7.4

def _is_out_of_range(p: Point) -> Tuple[bool, bool, bool]:

    t_bad = not (TEMP_MIN <= p.t <= TEMP_MAX)
    phi_bad = not (HUM_MIN <= p.phi <= HUM_MAX)
    ph_bad = not (PH_MIN <= p.pH <= PH_MAX)
    return t_bad, phi_bad, ph_bad


def _score(points: List[Point]) -> int:
    if not points:
        return 1

    bad = 0
    total = len(points)

    for p in points:
        t_bad, phi_bad, ph_bad = _is_out_of_range(p)
        if t_bad or phi_bad or ph_bad:
            bad += 1

    ratio = bad / total

    if ratio < 0.10:
        return 0
    if ratio < 0.30:
        return 1
    return 2



@app.post("/greenhouse_state/", dependencies=[Depends(auth)], response_model=int)
def greenhouse_state(points: List[Point] = Body(..., embed=False)) -> int:
    """
    Принимаем сразу JSON-массив точек:
    [
      {"dt": "...", "t": ..., "phi": ..., "pH": ...},
      ...
    ]
    """
    time.sleep(STATE_DELAY_SEC)
    return _score(points)

@app.get("/health")
def health():
    return {"status": "ok", "delay_sec": STATE_DELAY_SEC}