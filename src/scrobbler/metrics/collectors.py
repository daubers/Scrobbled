"""Gauges computed from the database at scrape time, cached so frequent scrapes
don't load Postgres. Uses its own NullPool engine, so it's safe to run in the
gunicorn master process (no pooled connections to leak into forked workers)."""

import threading
import time

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scrobbler.metrics import COLLECTED

ACTIVE_WINDOWS = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}


class DatabaseStatsCollector(Collector):
    def __init__(self, database_url: str, ttl_seconds: float = 60):
        self.engine = create_engine(database_url, poolclass=NullPool)
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._values: dict | None = None

    def _query(self) -> dict:
        with self.engine.connect() as conn:
            users = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
            active = {
                window: conn.execute(
                    text(
                        "SELECT count(DISTINCT user_id) FROM scrobbles "
                        f"WHERE played_at > now() - interval '{interval}'"
                    )
                ).scalar_one()
                for window, interval in ACTIVE_WINDOWS.items()
            }
            now_playing = conn.execute(
                text("SELECT count(*) FROM now_playing WHERE expires_at > now()")
            ).scalar_one()
        return {"users": users, "active": active, "now_playing": now_playing}

    def values(self) -> dict | None:
        with self._lock:
            if self._values is None or time.monotonic() - self._cached_at > self.ttl:
                try:
                    self._values = self._query()
                    self._cached_at = time.monotonic()
                except Exception:  # database down: export nothing rather than fail the scrape
                    return None
            return self._values

    def collect(self):
        values = self.values()
        if values is None:
            return
        yield GaugeMetricFamily(COLLECTED["users"], "Registered users", value=values["users"])
        active = GaugeMetricFamily(
            COLLECTED["active_users"], "Users who scrobbled within the window", labels=["window"]
        )
        for window, count in values["active"].items():
            active.add_metric([window], count)
        yield active
        yield GaugeMetricFamily(
            COLLECTED["now_playing_active"],
            "Users currently playing a track",
            value=values["now_playing"],
        )
