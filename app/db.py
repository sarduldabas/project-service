import logging
import time

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import database_url

log = logging.getLogger("db")

_url = database_url()
_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
engine = create_engine(_url, pool_pre_ping=True, connect_args=_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db(retries: int = 30, delay: float = 2.0) -> None:
    """Create tables; wait for Postgres to come up (pods start in no fixed order)."""
    from . import models  # noqa: F401  (register tables)

    for attempt in range(1, retries + 1):
        try:
            Base.metadata.create_all(engine)
            _add_missing_columns()
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("database not ready (%s/%s): %s", attempt, retries, str(exc)[:120])
            time.sleep(delay)
    raise RuntimeError("database never became ready")


def _add_missing_columns() -> None:
    """Tiny migration: databases created by an earlier version get the columns added later."""
    existing = {c["name"] for c in inspect(engine).get_columns("projects")}
    with engine.begin() as conn:
        if "theme" not in existing:
            conn.execute(text("ALTER TABLE projects ADD COLUMN theme VARCHAR(30)"))
            log.info("added column projects.theme")


def ping() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
