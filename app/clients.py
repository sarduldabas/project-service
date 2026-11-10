"""HTTP calls to the other services, recorded in the activity log."""
import json
import logging
import time
from typing import Any

import httpx

from .config import service_urls
from .db import SessionLocal
from .models import Event

log = logging.getLogger("clients")


class ServiceError(RuntimeError):
    pass


def summarize(obj: Any, limit: int = 2500) -> str:
    """JSON text with long strings (e.g. base64 files) shortened, for the activity log."""

    def shrink(value):
        if isinstance(value, str) and len(value) > 240:
            return value[:200] + f"... ({len(value)} chars)"
        if isinstance(value, dict):
            return {k: shrink(v) for k, v in value.items()}
        if isinstance(value, list):
            return [shrink(v) for v in value[:30]] + (["..."] if len(value) > 30 else [])
        return value

    text = json.dumps(shrink(obj), ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"


def _start_event(project_id: str, service: str, operation: str, request: Any) -> int:
    with SessionLocal() as db:
        ev = Event(project_id=project_id, service=service, operation=operation, request=summarize(request))
        db.add(ev)
        db.commit()
        return ev.id


def _finish_event(event_id: int, status: str, ms: int, response: str) -> None:
    with SessionLocal() as db:
        ev = db.get(Event, event_id)
        if ev:
            ev.status, ev.duration_ms, ev.response = status, ms, response
            db.commit()


def call_service(project_id: str, service: str, path: str, payload: dict, timeout: float) -> dict:
    url = service_urls()[service] + path
    event_id = _start_event(project_id, service, f"POST {path}", payload)
    started = time.monotonic()
    response = None
    for attempt in range(3):  # retry only connection problems (pods may still be starting)
        try:
            response = httpx.post(url, json=payload, timeout=timeout)
            break
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            log.warning("%s unreachable (attempt %s): %s", service, attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
        except httpx.HTTPError as exc:
            ms = int((time.monotonic() - started) * 1000)
            _finish_event(event_id, "ERROR", ms, f"{type(exc).__name__}: {exc}")
            raise ServiceError(f"{service} call failed: {type(exc).__name__}: {exc}") from exc

    ms = int((time.monotonic() - started) * 1000)
    if response is None:
        _finish_event(event_id, "ERROR", ms, "service unreachable")
        raise ServiceError(f"{service} is unreachable")
    if response.status_code >= 400:
        _finish_event(event_id, "ERROR", ms, f"HTTP {response.status_code}: {response.text[:600]}")
        raise ServiceError(f"{service} returned HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    _finish_event(event_id, "OK", ms, summarize(data))
    return data
