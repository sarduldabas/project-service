import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    topic: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    audience: Mapped[str | None] = mapped_column(String(200), nullable=True)
    slide_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    theme: Mapped[str | None] = mapped_column(String(30), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="CREATED", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    questions: Mapped[list] = mapped_column(JSON, default=list)
    answers: Mapped[list] = mapped_column(JSON, default=list)
    brief: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    research: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outline: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    deck: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    deck_filename: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Event(Base):
    """Activity log: one row per call project-service makes to another service."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    service: Mapped[str] = mapped_column(String(50))
    operation: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(10), default="RUNNING")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
