# project-service

**Business capability:** project management and workflow orchestration. The only service the outside world talks to.
It stores projects, answers, research and the generated `.pptx` in PostgreSQL and drives
`intake-service -> research-service -> builder-service`, recording every call in an activity log.

## API (Swagger at `/docs`)
| Type | Operation | Endpoint |
|---|---|---|
| Command | CreateProject | `POST /projects` `{topic, description, audience?, slide_count?, theme?}` (returns immediately; workflow runs in background) |
| Command | SubmitAnswers | `POST /projects/{id}/answers` |
| Command | RetryProject | `POST /projects/{id}/retry` |
| Command | RebuildDeck | `POST /projects/{id}/rebuild` `{theme?}` (builder only, reuses stored research, no new web search) |
| Command | DeleteProject | `DELETE /projects/{id}` |
| Query | ListProjects | `GET /projects` |
| Query | GetProject | `GET /projects/{id}` |
| Query | GetResearch | `GET /projects/{id}/research` |
| Query | GetActivity | `GET /projects/{id}/events` |
| Query | DownloadDeck | `GET /projects/{id}/download` |
| Ops | health / readiness | `GET /health`, `GET /ready` |

State machine: `CREATED -> CHECKING_BRIEF -> NEEDS_INPUT -> CHECKING_BRIEF -> RESEARCHING -> BUILDING -> COMPLETED` (or `FAILED`).
Rebuild: `COMPLETED/FAILED -> BUILDING -> COMPLETED`. `theme` is one of `executive`, `graphite`, `forest`, `aubergine`.
Existing databases get the new `theme` column added automatically on startup.

## Configuration
| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | built from `POSTGRES_*` | full SQLAlchemy URL (tests use SQLite) |
| `POSTGRES_HOST/PORT/USER/PASSWORD/DB` | `postgres/5432/deckpilot/...` | PostgreSQL connection |
| `INTAKE_URL`, `RESEARCH_URL`, `BUILDER_URL` | `http://<service>:8000` | downstream services |
| `INTAKE_TIMEOUT`, `RESEARCH_TIMEOUT`, `BUILD_TIMEOUT` | `120`, `600`, `420` | seconds to wait for each agent |
| `DEFAULT_THEME` | `executive` | theme when the request has none |

## Develop and test
```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                      # SQLite + faked downstream services
```

## Container and Kubernetes
`docker build -t deckpilot/project-service:1.0 .` · manifests in `k8s/` (`project-service.yaml`, `postgres.yaml`).
Needs the `deckpilot` namespace, `deckpilot-config` ConfigMap and `postgres-secret` from the `deckpilot-deploy` repo.

CI: `.github/workflows/ci.yml` runs the tests, then builds the image and pushes it to GHCR on `main`.
