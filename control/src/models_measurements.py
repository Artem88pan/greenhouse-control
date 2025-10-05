from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import SQLModel, Field


class MeasurementKind(str, Enum):
    raw = "raw"
    clean = "clean"


class Measurement(SQLModel, table=True):
    __tablename__ = "measurement"

    id: Optional[int] = Field(default=None, primary_key=True)
    greenhouse_id: UUID = Field(foreign_key="greenhouse.id", index=True)

    # ВАЖНО: индекс переносим в sa.Column(..., index=True)
    dt: datetime = Field(sa_column=sa.Column(sa.DateTime(timezone=True), index=True))

    # в одной строке — одна метрика; остальные None
    t: Optional[float] = None
    phi: Optional[float] = None
    pH: Optional[float] = None

    kind: MeasurementKind = Field(index=True)


class DataFix(SQLModel, table=True):
    __tablename__ = "data_fix"

    id: Optional[int] = Field(default=None, primary_key=True)
    greenhouse_id: UUID = Field(foreign_key="greenhouse.id", index=True)
    dt: datetime = Field(sa_column=sa.Column(sa.DateTime(timezone=True), index=True))

    metric: str = Field(index=True)   # "t" | "phi" | "pH"
    reason: str = Field(index=True)   # "outlier" | "missing" | "manual"

    old_value: Optional[float] = None
    new_value: Optional[float] = None


class StateHistory(SQLModel, table=True):
    __tablename__ = "state_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    greenhouse_id: UUID = Field(foreign_key="greenhouse.id", index=True)
    dt: datetime = Field(sa_column=sa.Column(sa.DateTime(timezone=True), index=True))
    state: int = Field(ge=0, le=2, index=True)  # 0/1/2
