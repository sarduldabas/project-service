from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class QA(BaseModel):
    question: str
    answer: str = ""


Theme = Literal["executive", "graphite", "forest", "aubergine"]


class ProjectCreate(BaseModel):
    topic: str = Field(..., min_length=3, max_length=300, examples=["Solar power adoption in India"])
    description: str = Field("", max_length=8000)
    audience: str | None = Field(None, max_length=200)
    slide_count: int | None = Field(None, ge=3, le=15)
    theme: Theme | None = None


class RebuildIn(BaseModel):
    theme: Theme | None = None


class AnswersIn(BaseModel):
    answers: list[QA] = Field(default_factory=list)


class ProjectOut(BaseModel):
    id: str
    topic: str
    description: str
    audience: str | None
    slide_count: int | None
    theme: str | None = None
    status: str
    error: str | None
    questions: list[str]
    answers: list[QA]
    brief: dict[str, Any] | None
    outline: dict[str, Any] | None
    sources: list[dict[str, Any]]
    has_deck: bool
    created_at: datetime
    updated_at: datetime


class ProjectSummary(BaseModel):
    id: str
    topic: str
    status: str
    has_deck: bool
    created_at: datetime


class EventOut(BaseModel):
    id: int
    ts: datetime
    service: str
    operation: str
    status: str
    duration_ms: int | None
    request: str | None
    response: str | None
