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
