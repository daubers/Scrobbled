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
