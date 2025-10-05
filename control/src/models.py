# control/src/models.py
from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from sqlmodel import SQLModel, Field


class Region(SQLModel, table=True):
    __tablename__ = "region"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str


class Greenhouse(SQLModel, table=True):
    __tablename__ = "greenhouse"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str
    # ВАЖНО: foreign_key указывает на 'region.id' — имя совпадает с __tablename__ у Region
    region_id: UUID = Field(foreign_key="region.id", index=True)
