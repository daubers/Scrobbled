import pytest
from flask_migrate import downgrade, upgrade
from prometheus_client import REGISTRY
from sqlalchemy import text

from scrobbler import create_app
from scrobbler.config import TestConfig
from scrobbler.extensions import db


@pytest.fixture(scope="session")
def app():
    app = create_app(TestConfig)
    with app.app_context():
        downgrade(revision="base")  # clean slate if a previous run was interrupted
        upgrade()
        yield app
        db.session.remove()
        downgrade(revision="base")


@pytest.fixture(autouse=True)
def _clean_tables(app):
    yield
    db.session.rollback()
    tables = ", ".join(t.name for t in db.metadata.sorted_tables)
    db.session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.session.commit()


@pytest.fixture
def client(app):
    return app.test_client()


class MetricDelta:
    """Reads a metric sample now and later, so tests can assert on the increase."""

    def __init__(self, name: str, **labels: str):
        self.name, self.labels = name, labels
        self.start = self._value()

    def _value(self) -> float:
        return REGISTRY.get_sample_value(self.name, self.labels) or 0.0

    @property
    def delta(self) -> float:
        return self._value() - self.start


@pytest.fixture
def metric_delta():
    return MetricDelta


# --- Factories ------------------------------------------------------------------

import secrets  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

from werkzeug.security import generate_password_hash  # noqa: E402

from scrobbler.lastfm.signature import sign  # noqa: E402
from scrobbler.models import ApiApp, Session, User  # noqa: E402


@pytest.fixture
def make_user():
    def make(username="alice", password="hunter22", email=None):
        user = User(
            username=username,
            email=email or f"{username}@example.com",
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.commit()
        return user

    return make


@pytest.fixture
def make_app(make_user):
    def make(owner=None, name="Test Player"):
        app_ = ApiApp(
            api_key=secrets.token_hex(16),
            shared_secret=secrets.token_hex(16),
            name=name,
            owner=owner or make_user(username=f"owner{secrets.token_hex(3)}"),
        )
        db.session.add(app_)
        db.session.commit()
        return app_

    return make


@pytest.fixture
def make_session():
    def make(user, api_app):
        session = Session(session_key=secrets.token_hex(16), user=user, api_app=api_app)
        db.session.add(session)
        db.session.commit()
        return session

    return make


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture
def api_app(make_app, user):
    return make_app(owner=user)


@pytest.fixture
def session_key(make_session, user, api_app):
    return make_session(user, api_app).session_key


def signed(params: dict, secret: str) -> dict:
    """Return params plus a valid api_sig."""
    return {**params, "api_sig": sign(params, secret)}


def days_ago(days: float) -> int:
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp())
