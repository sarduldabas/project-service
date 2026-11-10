import os
from urllib.parse import quote_plus


def database_url() -> str:
    """DATABASE_URL wins (used by tests/SQLite); otherwise build a Postgres URL from parts."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = quote_plus(os.getenv("POSTGRES_USER", "deckpilot"))
    password = quote_plus(os.getenv("POSTGRES_PASSWORD", "deckpilot"))
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "deckpilot")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


def service_urls() -> dict[str, str]:
    return {
        "intake-service": os.getenv("INTAKE_URL", "http://intake-service:8000"),
        "research-service": os.getenv("RESEARCH_URL", "http://research-service:8000"),
        "builder-service": os.getenv("BUILDER_URL", "http://builder-service:8000"),
    }
