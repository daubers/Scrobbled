import os

from dotenv import load_dotenv

# Settings are read at import time, so .env must be loaded first.
load_dotenv()


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://scrobbler:scrobbler@localhost:5432/scrobbler"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    CORS_ORIGINS = _csv(os.environ.get("CORS_ORIGINS", "http://localhost:8080"))
    UI_BASE_URL = os.environ.get("UI_BASE_URL", "http://localhost:8080").rstrip("/")

    METRICS_PORT = int(os.environ.get("METRICS_PORT", "9100"))
    # Start the metrics HTTP server from create_app (dev server). Under gunicorn the
    # server is started by gunicorn.conf.py instead, so this stays off there.
    START_METRICS_SERVER = os.environ.get("START_METRICS_SERVER", "0") == "1"

    # OpenAPI document and docs (flask-smorest)
    API_TITLE = "Scrobbler API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.1.0"
    OPENAPI_URL_PREFIX = "/api"
    OPENAPI_JSON_PATH = "openapi.json"
    OPENAPI_DOCS_ENABLED = os.environ.get("OPENAPI_DOCS_ENABLED", "1") == "1"
    OPENAPI_SWAGGER_UI_PATH = "docs" if OPENAPI_DOCS_ENABLED else None
    OPENAPI_SWAGGER_UI_URL = os.environ.get(
        "OPENAPI_SWAGGER_UI_URL", "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/"
    )
    OPENAPI_REDOC_PATH = "redoc" if OPENAPI_DOCS_ENABLED else None
    OPENAPI_REDOC_URL = os.environ.get(
        "OPENAPI_REDOC_URL", "https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"
    )
    API_SPEC_OPTIONS = {
        "info": {
            "description": (
                "Two APIs: `/2.0/` is Last.fm (Audioscrobbler 2.0) compatible, for players "
                "and scrobblers; `/api/v1/` is the JSON API used by the web UI."
            )
        },
    }

    # History imports. Pulling from Last.fm is offered only when an API key is set
    # (get one at https://www.last.fm/api/account/create).
    LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
    LASTFM_API_URL = os.environ.get("LASTFM_API_URL", "https://ws.audioscrobbler.com/2.0/")
    LASTFM_REQUEST_INTERVAL = float(os.environ.get("LASTFM_REQUEST_INTERVAL", "0.25"))
    IMPORT_MAX_BYTES = int(os.environ.get("IMPORT_MAX_BYTES", str(200 * 1024 * 1024)))
    MAX_CONTENT_LENGTH = IMPORT_MAX_BYTES + 1024 * 1024  # uploads are the largest requests
    # IMPORT_WORKER_METRICS_PORT is the pre-0.3 name, still honoured.
    WORKER_METRICS_PORT = int(
        os.environ.get("WORKER_METRICS_PORT", os.environ.get("IMPORT_WORKER_METRICS_PORT", "9101"))
    )

    # Last.fm behaviour
    AUTH_TOKEN_TTL_SECONDS = 60 * 60
    UI_TOKEN_TTL_DAYS = 30
    SCROBBLE_MAX_AGE_DAYS = 14
    SCROBBLE_MAX_FUTURE_SECONDS = 5 * 60
    NOW_PLAYING_DEFAULT_SECONDS = 10 * 60


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://scrobbler:scrobbler@localhost:5432/scrobbler_test",
    )
    START_METRICS_SERVER = False
    # Tests never inherit optional features from a developer's .env or environment;
    # tests that need them turn them on explicitly.
    LASTFM_API_KEY = ""
    FEDERATION_ENABLED = "0"
