
from __future__ import annotations
from datetime import datetime
from typing import Optional, Literal
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field
from enum import Enum
class MeasurementKind(str, Enum):
    raw = "raw"
    clean = "clean"

class Measurement(SQLModel, table=True):
    __tablename__ = "measurements"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    greenhouse_id: UUID = Field(index=True, foreign_key="greenhouses.id")
    dt: datetime = Field(index=True)

    t: Optional[float] = None
    phi: Optional[float] = None
    pH: Optional[float] = None

    kind: MeasurementKind = Field(default="raw", index=True)  # raw|clean
    original_id: Optional[UUID] = Field(default=None, index=True)  # ссылка на 'raw', если это 'clean'


class DataFix(SQLModel, table=True):
    __tablename__ = "data_fixes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    greenhouse_id: UUID = Field(index=True, foreign_key="greenhouses.id")
    dt: datetime = Field(index=True)

    metric: str = Field(index=True)
    reason: str
    old_value: Optional[float] = None
    new_value: Optional[float] = None
