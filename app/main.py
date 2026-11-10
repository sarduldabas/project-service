import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import db as database
from . import pipeline
from .models import Event, Project
from .schemas import (
    AnswersIn,
    EventOut,
    ProjectCreate,
    ProjectOut,
    ProjectSummary,
    RebuildIn,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("project-service")

IN_PROGRESS = ("CREATED", "CHECKING_BRIEF", "RESEARCHING", "BUILDING")
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    # background tasks die with their pod: flag anything that was mid-flight
    with database.SessionLocal() as s:
        stuck = s.scalars(select(Project).where(Project.status.in_(IN_PROGRESS))).all()
        for p in stuck:
            p.status, p.error = "FAILED", "Interrupted by a service restart. Use retry."
        s.commit()
    yield


app = FastAPI(
    title="DeckPilot project-service",
    version="1.0.0",
    description="Business capability: project management and workflow orchestration. The only public API: "
    "stores projects/decks in PostgreSQL and drives intake -> research -> builder.",
    lifespan=lifespan,
)


def get_db():
    with database.SessionLocal() as session:
        yield session


def _get_or_404(db: Session, project_id: str) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    return p


def _out(p: Project) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        topic=p.topic,
        description=p.description,
        audience=p.audience,
        slide_count=p.slide_count,
        theme=p.theme,
        status=p.status,
        error=p.error,
        questions=p.questions or [],
        answers=p.answers or [],
        brief=p.brief,
        outline=p.outline,
        sources=(p.research or {}).get("sources", []),
        has_deck=p.deck is not None,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


# ------------------------------------------------------------------------- ops
@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok", "service": "project-service"}


@app.get("/ready", tags=["ops"])
def ready():
    if not database.ping():
        raise HTTPException(503, "database unavailable")
    return {"status": "ready"}


# -------------------------------------------------------------------- commands
@app.post("/projects", response_model=ProjectOut, status_code=201, tags=["commands"])
def create_project(body: ProjectCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    """CreateProject: stores the brief and starts the workflow in the background."""
    p = Project(
        topic=body.topic.strip(),
        description=body.description,
        audience=body.audience,
        slide_count=body.slide_count,
        theme=body.theme,
        questions=[],
        answers=[],
    )
    db.add(p)
    db.commit()
    background.add_task(pipeline.start, p.id)
    return _out(p)


@app.post("/projects/{project_id}/answers", response_model=ProjectOut, status_code=202, tags=["commands"])
def submit_answers(project_id: str, body: AnswersIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    """SubmitAnswers: answer the intake agent's questions (blank answers = proceed with assumptions)."""
    p = _get_or_404(db, project_id)
    if p.status != "NEEDS_INPUT":
        raise HTTPException(409, f"project is {p.status}, not NEEDS_INPUT")
    p.answers = [a.model_dump() for a in body.answers]
    p.status = "CHECKING_BRIEF"
    db.commit()
    background.add_task(pipeline.resume_with_answers, p.id)
    return _out(p)


@app.post("/projects/{project_id}/retry", response_model=ProjectOut, status_code=202, tags=["commands"])
def retry_project(project_id: str, background: BackgroundTasks, db: Session = Depends(get_db)):
    """RetryProject: re-run a FAILED project from the start."""
    p = _get_or_404(db, project_id)
    if p.status != "FAILED":
        raise HTTPException(409, f"project is {p.status}, only FAILED projects can be retried")
    p.status, p.error = "CREATED", None
    db.commit()
    background.add_task(pipeline.start, p.id)
    return _out(p)


@app.post("/projects/{project_id}/rebuild", response_model=ProjectOut, status_code=202, tags=["commands"])
def rebuild_deck(project_id: str, body: RebuildIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    """RebuildDeck: build the deck again from the stored research (e.g. with another theme). No new web search."""
    p = _get_or_404(db, project_id)
    if p.status not in ("COMPLETED", "FAILED") or not p.research or not p.brief:
        raise HTTPException(409, "rebuild needs a finished project that already has research")
    if body.theme:
        p.theme = body.theme
    p.status, p.error = "BUILDING", None
    db.commit()
    background.add_task(pipeline.rebuild, p.id)
    return _out(p)


@app.delete("/projects/{project_id}", status_code=204, tags=["commands"])
def delete_project(project_id: str, db: Session = Depends(get_db)):
    """DeleteProject: remove the project, its deck and its activity log."""
    p = _get_or_404(db, project_id)
    db.execute(delete(Event).where(Event.project_id == p.id))
    db.delete(p)
    db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------- queries
@app.get("/projects", response_model=list[ProjectSummary], tags=["queries"])
def list_projects(db: Session = Depends(get_db)):
    """ListProjects (newest first)."""
    rows = db.scalars(select(Project).order_by(Project.created_at.desc()).limit(100)).all()
    return [
        ProjectSummary(id=p.id, topic=p.topic, status=p.status, has_deck=p.deck is not None, created_at=p.created_at)
        for p in rows
    ]


@app.get("/projects/{project_id}", response_model=ProjectOut, tags=["queries"])
def get_project(project_id: str, db: Session = Depends(get_db)):
    """GetProject: status, questions, refined brief, outline and sources."""
    return _out(_get_or_404(db, project_id))


@app.get("/projects/{project_id}/research", tags=["queries"])
def get_research(project_id: str, db: Session = Depends(get_db)):
    """GetResearch: the full notes, queries and image candidates from research-service."""
    p = _get_or_404(db, project_id)
    if p.research is None:
        raise HTTPException(404, "no research yet")
    return p.research


@app.get("/projects/{project_id}/events", response_model=list[EventOut], tags=["queries"])
def get_events(project_id: str, db: Session = Depends(get_db)):
    """GetActivity: every call made to intake/research/builder for this project."""
    _get_or_404(db, project_id)
    return db.scalars(select(Event).where(Event.project_id == project_id).order_by(Event.id)).all()


@app.get("/projects/{project_id}/download", tags=["queries"])
def download_deck(project_id: str, db: Session = Depends(get_db)):
    """DownloadDeck: the generated .pptx."""
    p = _get_or_404(db, project_id)
    if p.deck is None:
        raise HTTPException(404, "deck not ready")
    return Response(
        content=p.deck,
        media_type=PPTX,
        headers={"Content-Disposition": f'attachment; filename="{p.deck_filename or "deck.pptx"}"'},
    )
