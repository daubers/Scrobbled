"""Self-hosted, Last.fm-compatible music scrobbling service."""

__version__ = "0.1.0"


def create_app(config_object=None):
    from flask import Flask

    from scrobbler import models  # noqa: F401  (register models with SQLAlchemy)
    from scrobbler.config import Config
    from scrobbler.extensions import db, migrate
    from scrobbler.metrics import http as http_metrics

    app = Flask(__name__, static_folder=None)  # API only: the UI is hosted separately
    app.config.from_object(config_object or Config)

    db.init_app(app)
    migrate.init_app(app, db, directory=_migrations_dir())
    http_metrics.init_app(app)

    from flask_cors import CORS

    from scrobbler import api
    from scrobbler.lastfm import bp as lastfm_bp

    app.register_blueprint(lastfm_bp)
    api.init_app(app)
    CORS(
        app,
        resources={r"/api/v1/*": {"origins": app.config["CORS_ORIGINS"]}},
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    if app.config["START_METRICS_SERVER"]:
        from scrobbler.metrics.server import start_metrics_server

        start_metrics_server(app.config["METRICS_PORT"])

    return app


def _migrations_dir() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parent / "migrations")
