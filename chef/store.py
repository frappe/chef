"""The bake + image index (SQLite via SQLModel). Recipes are files; only *runs* and
*artifacts* are rows.

Both the API process and the arq worker import this and open short-lived sessions against
the same SQLite file (WAL, ``check_same_thread=False``). Live log lines do NOT live here —
they stream through Redis; this store holds durable state (bake status, the structured step
list, image provenance).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column
from sqlalchemy import JSON as SAJSON
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select

from chef.config import get_settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BakeRecord(SQLModel, table=True):
    __tablename__ = "bakes"

    id: str = Field(primary_key=True)
    recipe: str
    version: str = ""
    mode: str = "cold"
    builder: str = "docker"
    inputs: dict = Field(default_factory=dict, sa_column=Column(SAJSON))
    status: str = "queued"
    exit_code: int | None = None
    error: str | None = None
    idempotency_key: str | None = Field(default=None, index=True)
    vm_ref: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class StepRecord(SQLModel, table=True):
    __tablename__ = "steps"

    id: int | None = Field(default=None, primary_key=True)
    bake_id: str = Field(index=True)
    idx: int = 0
    name: str = ""
    phase: str = ""
    state: str = "running"
    retries: int = 0
    created_at: datetime = Field(default_factory=_now)


class ImageRecord(SQLModel, table=True):
    __tablename__ = "images"

    id: str = Field(primary_key=True)
    bake_id: str = Field(index=True)
    recipe: str = ""
    version: str = ""
    kind: str = "cold"
    base_image: str = ""
    provenance: dict = Field(default_factory=dict, sa_column=Column(SAJSON))
    location_type: str = ""
    location_uri: str = ""
    manifest: dict = Field(default_factory=dict, sa_column=Column(SAJSON))
    size_bytes: int = 0
    host_signature: dict | None = Field(default=None, sa_column=Column(SAJSON))
    created_at: datetime = Field(default_factory=_now)


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args)
    return _engine


def init_db() -> None:
    SQLModel.metadata.create_all(get_engine())


# --- bakes -------------------------------------------------------------------

def create_bake(bake: BakeRecord) -> BakeRecord:
    with Session(get_engine()) as s:
        s.add(bake)
        s.commit()
        s.refresh(bake)
        return bake


def get_bake(bake_id: str) -> BakeRecord | None:
    with Session(get_engine()) as s:
        return s.get(BakeRecord, bake_id)


def list_bakes(limit: int = 100) -> list[BakeRecord]:
    with Session(get_engine()) as s:
        return list(s.exec(select(BakeRecord).order_by(BakeRecord.created_at.desc()).limit(limit)))


def find_bake_by_idempotency(key: str) -> BakeRecord | None:
    with Session(get_engine()) as s:
        return s.exec(select(BakeRecord).where(BakeRecord.idempotency_key == key)).first()


def set_bake(bake_id: str, **changes: Any) -> BakeRecord | None:
    with Session(get_engine()) as s:
        bake = s.get(BakeRecord, bake_id)
        if bake is None:
            return None
        for k, v in changes.items():
            setattr(bake, k, v)
        bake.updated_at = _now()
        s.add(bake)
        s.commit()
        s.refresh(bake)
        return bake


# --- steps -------------------------------------------------------------------

def record_step(bake_id: str, idx: int, name: str, phase: str, state: str, retries: int = 0) -> None:
    """Insert or update the (bake_id, idx) step row (running → changed/no_change/failed)."""
    with Session(get_engine()) as s:
        existing = s.exec(
            select(StepRecord).where(StepRecord.bake_id == bake_id, StepRecord.idx == idx)
        ).first()
        if existing is None:
            s.add(StepRecord(bake_id=bake_id, idx=idx, name=name, phase=phase,
                             state=state, retries=retries))
        else:
            existing.state = state
            existing.retries = retries
            existing.name = name or existing.name
            existing.phase = phase or existing.phase
            s.add(existing)
        s.commit()


def list_steps(bake_id: str) -> list[StepRecord]:
    with Session(get_engine()) as s:
        return list(s.exec(select(StepRecord).where(StepRecord.bake_id == bake_id).order_by(StepRecord.idx)))


# --- images ------------------------------------------------------------------

def create_image(image: ImageRecord) -> ImageRecord:
    with Session(get_engine()) as s:
        s.add(image)
        s.commit()
        s.refresh(image)
        return image


def get_image(image_id: str) -> ImageRecord | None:
    with Session(get_engine()) as s:
        return s.get(ImageRecord, image_id)


def list_images(limit: int = 200) -> list[ImageRecord]:
    with Session(get_engine()) as s:
        return list(s.exec(select(ImageRecord).order_by(ImageRecord.created_at.desc()).limit(limit)))


def images_for_bake(bake_id: str) -> list[ImageRecord]:
    with Session(get_engine()) as s:
        return list(s.exec(select(ImageRecord).where(ImageRecord.bake_id == bake_id)))
