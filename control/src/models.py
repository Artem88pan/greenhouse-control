
from typing import Optional
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field, Relationship

class Region(SQLModel, table=True):
    __tablename__ = "regions"
    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str = Field(index=True, min_length=1, max_length=120)
    greenhouses: list["Greenhouse"] = Relationship(back_populates="region")

class Greenhouse(SQLModel, table=True):
    __tablename__ = "greenhouses"
    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str = Field(index=True, min_length=1, max_length=120)
    region_id: UUID = Field(foreign_key="regions.id", index=True)
    region: Optional[Region] = Relationship(back_populates="greenhouses")
