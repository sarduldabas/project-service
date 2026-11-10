import base64

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

BRIEF = {"topic": "Solar", "audience": "Students", "slide_count": 6}
RESEARCH = {
    "queries": ["q1"],
    "notes": [{"heading": "h", "findings": ["f"], "source_urls": ["https://e.com"]}],
    "sources": [{"title": "E", "url": "https://e.com"}],
    "images": [],
}
OUTLINE = {"title": "Solar", "subtitle": "", "slides": [{"title": "s", "bullets": ["b"]}]}
LONG = "word " * 30
FILE = b"PPTX-BYTES" * 100  # large enough to be shortened in the activity log


class FakeServices:
    def __init__(self):
        self.fail_research = False
        self.last_build = None
        self.build_calls = 0
        self.research_calls = 0

    def post(self, url, json=None, timeout=None, **_):
        req = httpx.Request("POST", url)
        if url.endswith("/intake/evaluate"):
            short = len(json["description"].split()) < 10 and not json["answers"] and not json["final_round"]
            body = (
                {"status": "NEEDS_INPUT", "questions": ["Who is the audience?"], "brief": BRIEF, "rationale": ""}
                if short
                else {"status": "READY", "questions": [], "brief": BRIEF, "rationale": ""}
            )
        elif url.endswith("/research"):
            if self.fail_research:
                return httpx.Response(502, json={"detail": "search down"}, request=req)
            self.research_calls += 1
            body = RESEARCH
        elif url.endswith("/build"):
            self.last_build = json
            self.build_calls += 1
            body = {
                "outline": OUTLINE,
                "filename": "solar.pptx",
                "pptx_base64": base64.b64encode(FILE).decode(),
                "images_embedded": 0,
            }
        else:
            return httpx.Response(404, request=req)
        return httpx.Response(200, json=body, request=req)


@pytest.fixture()
def fake(monkeypatch):
    f = FakeServices()
    monkeypatch.setattr(httpx, "post", f.post)
    return f


@pytest.fixture()
def client():
    with TestClient(app) as c:  # runs lifespan (creates tables)
        yield c


def test_health_and_ready(client):
    assert client.get("/health").json()["service"] == "project-service"
    assert client.get("/ready").status_code == 200


def test_happy_path_completes_and_downloads(client, fake):
    r = client.post("/projects", json={"topic": "Solar power", "description": LONG})
    assert r.status_code == 201
    pid = r.json()["id"]

    p = client.get(f"/projects/{pid}").json()  # background task already ran
    assert p["status"] == "COMPLETED"
    assert p["has_deck"] and p["sources"][0]["url"] == "https://e.com"

    events = client.get(f"/projects/{pid}/events").json()
    assert [e["service"] for e in events] == ["intake-service", "research-service", "builder-service"]
    assert all(e["status"] == "OK" for e in events)
    assert "pptx_base64" in events[2]["response"] and "chars)" in events[2]["response"]  # file is shortened

    d = client.get(f"/projects/{pid}/download")
    assert d.status_code == 200 and d.content == FILE
    assert "solar.pptx" in d.headers["content-disposition"]
    assert client.get(f"/projects/{pid}/research").json()["queries"] == ["q1"]


def test_needs_input_then_answers(client, fake):
    pid = client.post("/projects", json={"topic": "Solar power", "description": "short"}).json()["id"]
    p = client.get(f"/projects/{pid}").json()
    assert p["status"] == "NEEDS_INPUT" and p["questions"] == ["Who is the audience?"]

    r = client.post(f"/projects/{pid}/answers", json={"answers": [{"question": "Who?", "answer": "Kids"}]})
    assert r.status_code == 202
    assert client.get(f"/projects/{pid}").json()["status"] == "COMPLETED"
    # answering twice is rejected
    assert client.post(f"/projects/{pid}/answers", json={"answers": []}).status_code == 409


def test_failure_is_recorded_and_retry_works(client, fake):
    fake.fail_research = True
    pid = client.post("/projects", json={"topic": "Solar power", "description": LONG}).json()["id"]
    p = client.get(f"/projects/{pid}").json()
    assert p["status"] == "FAILED" and "research-service" in p["error"]
    events = client.get(f"/projects/{pid}/events").json()
    assert events[-1]["status"] == "ERROR"

    fake.fail_research = False
    assert client.post(f"/projects/{pid}/retry").status_code == 202
    assert client.get(f"/projects/{pid}").json()["status"] == "COMPLETED"


def test_retry_rejected_unless_failed(client, fake):
    pid = client.post("/projects", json={"topic": "Solar power", "description": LONG}).json()["id"]
    assert client.post(f"/projects/{pid}/retry").status_code == 409


def test_list_and_delete(client, fake):
    pid = client.post("/projects", json={"topic": "Delete me", "description": LONG}).json()["id"]
    assert any(p["id"] == pid for p in client.get("/projects").json())
    assert client.delete(f"/projects/{pid}").status_code == 204
    assert client.get(f"/projects/{pid}").status_code == 404
    assert client.get(f"/projects/{pid}/events").status_code == 404


def test_validation(client):
    assert client.post("/projects", json={"topic": "ab"}).status_code == 422
    assert client.post("/projects", json={"topic": "Valid topic", "slide_count": 99}).status_code == 422
    assert client.get("/projects/nope/download").status_code == 404


def test_theme_is_passed_to_builder_and_rebuild_reuses_research(client, fake):
    pid = client.post("/projects", json={"topic": "Solar power", "description": LONG, "theme": "forest"}).json()["id"]
    assert fake.last_build["theme"] == "forest"
    assert fake.last_build["overview"] == ""  # research stub has no overview; field is always sent
    assert client.get(f"/projects/{pid}").json()["theme"] == "forest"

    r = client.post(f"/projects/{pid}/rebuild", json={"theme": "aubergine"})
    assert r.status_code == 202
    p = client.get(f"/projects/{pid}").json()
    assert p["status"] == "COMPLETED" and p["theme"] == "aubergine"
    assert fake.last_build["theme"] == "aubergine"
    assert fake.build_calls == 2 and fake.research_calls == 1  # no second web search


def test_rebuild_rejected_without_research(client, fake):
    pid = client.post("/projects", json={"topic": "Solar power", "description": "short"}).json()["id"]  # NEEDS_INPUT
    assert client.post(f"/projects/{pid}/rebuild", json={}).status_code == 409


def test_unknown_theme_rejected(client):
    assert client.post("/projects", json={"topic": "Valid topic", "theme": "neon"}).status_code == 422


def test_old_database_gets_theme_column():
    from sqlalchemy import create_engine, inspect, text

    import app.db as database

    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY)"))
    original = database.engine
    database.engine = eng
    try:
        database._add_missing_columns()
        assert "theme" in {c["name"] for c in inspect(eng).get_columns("projects")}
    finally:
        database.engine = original
