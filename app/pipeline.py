"""Workflow orchestration (runs as FastAPI background tasks).

State machine:
  CREATED -> CHECKING_BRIEF -> NEEDS_INPUT -> (answers) -> CHECKING_BRIEF
          -> RESEARCHING -> BUILDING -> COMPLETED          (any step may end in FAILED)
"""
import base64
import logging
import os

from .clients import ServiceError, call_service
from .db import SessionLocal
from .models import Project

log = logging.getLogger("pipeline")

def _seconds(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else default


INTAKE_TIMEOUT = _seconds("INTAKE_TIMEOUT", 120)
RESEARCH_TIMEOUT = _seconds("RESEARCH_TIMEOUT", 600)
BUILD_TIMEOUT = _seconds("BUILD_TIMEOUT", 420)


def _snapshot(pid: str) -> dict | None:
    with SessionLocal() as db:
        p = db.get(Project, pid)
        if p is None:
            return None
        return {
            "topic": p.topic,
            "description": p.description,
            "audience": p.audience,
            "slide_count": p.slide_count,
            "theme": p.theme,
            "answers": list(p.answers or []),
            "brief": p.brief,
            "research": p.research,
        }


def _update(pid: str, **fields) -> bool:
    with SessionLocal() as db:
        p = db.get(Project, pid)
        if p is None:
            return False
        for key, value in fields.items():
            setattr(p, key, value)
        db.commit()
        return True


def _fail(pid: str, message: str) -> None:
    log.error("project %s failed: %s", pid, message)
    _update(pid, status="FAILED", error=message)


def start(pid: str) -> None:
    """First run, or a retry: begin with the intake check."""
    _update(pid, questions=[], answers=[], research=None, outline=None, deck=None, deck_filename=None)
    _run(pid, final_round=False)


def resume_with_answers(pid: str) -> None:
    _run(pid, final_round=True)


def _run(pid: str, final_round: bool) -> None:
    try:
        snap = _snapshot(pid)
        if snap is None:
            return
        _update(pid, status="CHECKING_BRIEF", error=None)

        intake = call_service(
            pid,
            "intake-service",
            "/intake/evaluate",
            {
                "topic": snap["topic"],
                "description": snap["description"],
                "audience": snap["audience"],
                "slide_count": snap["slide_count"],
                "answers": snap["answers"],
                "final_round": final_round,
            },
            INTAKE_TIMEOUT,
        )
        if intake["status"] == "NEEDS_INPUT" and not final_round:
            _update(pid, status="NEEDS_INPUT", questions=intake["questions"], brief=intake["brief"])
            return

        brief = intake["brief"]
        if not _update(pid, brief=brief, status="RESEARCHING"):
            return
        research = call_service(pid, "research-service", "/research", {"brief": brief}, RESEARCH_TIMEOUT)

        if not _update(pid, research=research, status="BUILDING"):
            return
        _build(pid, brief, research, snap["theme"])
    except ServiceError as exc:
        _fail(pid, str(exc))
    except Exception as exc:  # noqa: BLE001 - never leave a project stuck in a running state
        _fail(pid, f"Unexpected error: {type(exc).__name__}: {exc}")


def _build(pid: str, brief: dict, research: dict, theme: str | None) -> None:
    built = call_service(
        pid,
        "builder-service",
        "/build",
        {
            "brief": brief,
            "overview": research.get("overview", ""),
            "notes": research["notes"],
            "sources": research["sources"],
            "images": research.get("images", []),
            "theme": theme,
        },
        BUILD_TIMEOUT,
    )
    _update(
        pid,
        outline=built["outline"],
        deck=base64.b64decode(built["pptx_base64"]),
        deck_filename=built["filename"],
        status="COMPLETED",
        error=None,
    )


def rebuild(pid: str) -> None:
    """Build the deck again from the stored brief and research (new theme, no new web search)."""
    try:
        snap = _snapshot(pid)
        if snap is None or not snap["research"] or not snap["brief"]:
            return _fail(pid, "Nothing to rebuild: the research is missing.")
        _build(pid, snap["brief"], snap["research"], snap["theme"])
    except ServiceError as exc:
        _fail(pid, str(exc))
    except Exception as exc:  # noqa: BLE001
        _fail(pid, f"Unexpected error: {type(exc).__name__}: {exc}")
