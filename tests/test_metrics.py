import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from scrobbler.metrics.collectors import DatabaseStatsCollector
from scrobbler.services import scrobbles
from scrobbler.services.scrobbles import TrackInput

ROOT = Path(__file__).resolve().parents[1]


def test_http_metrics_use_the_flask_endpoint_name(client, auth, metric_delta):
    ok = metric_delta(
        "scrobbler_http_requests_total", endpoint="Auth.me", http_method="GET", status="200"
    )
    denied = metric_delta(
        "scrobbler_http_requests_total", endpoint="Auth.me", http_method="GET", status="401"
    )
    latency = metric_delta(
        "scrobbler_http_request_duration_seconds_count", endpoint="Auth.me", http_method="GET"
    )
    client.get("/api/v1/auth/me", headers=auth)
    client.get("/api/v1/auth/me")
    assert (ok.delta, denied.delta, latency.delta) == (1, 1, 2)


def test_in_progress_gauge_returns_to_zero(client, metric_delta):
    in_progress = metric_delta("scrobbler_http_requests_in_progress", endpoint="lastfm.dispatch")
    client.get("/2.0/?method=nope")
    assert in_progress.delta == 0


def test_db_query_latency_is_recorded(client, user, metric_delta):
    selects = metric_delta("scrobbler_db_query_duration_seconds_count", operation="select")
    client.post("/api/v1/auth/login", json={"username": "alice", "password": "hunter22"})
    assert selects.delta >= 1


def test_pool_gauges_are_exported(client, user):
    from prometheus_client import REGISTRY

    client.get("/api/v1/auth/me")
    states = {
        s.labels["state"]
        for m in REGISTRY.collect()
        if m.name == "scrobbler_db_pool_connections"
        for s in m.samples
    }
    assert {"checked_out", "overflow", "size"} <= states


def test_checked_out_connections_return_to_zero(client, user):
    from prometheus_client import REGISTRY

    from scrobbler.extensions import db

    db.session.remove()  # return the test session's connection
    before = REGISTRY.get_sample_value("scrobbler_db_pool_connections", {"state": "checked_out"})
    client.post("/api/v1/auth/login", json={"username": "alice", "password": "hunter22"})
    # The request shares the test's app context, so end its session as teardown would.
    db.session.remove()
    after = REGISTRY.get_sample_value("scrobbler_db_pool_connections", {"state": "checked_out"})
    assert after == before


def test_database_stats_collector(app, user, make_user):
    make_user(username="bob")
    scrobbles.submit_scrobbles(
        user, None, [TrackInput(artist="A", track="T", timestamp=int(time.time()) - 60)]
    )
    scrobbles.update_now_playing(user, TrackInput(artist="A", track="U"))

    registry = CollectorRegistry()
    registry.register(DatabaseStatsCollector(app.config["SQLALCHEMY_DATABASE_URI"]))
    families = {
        f.name: f for f in text_string_to_metric_families(generate_latest(registry).decode())
    }
    assert families["scrobbler_users"].samples[0].value == 2
    active = {s.labels["window"]: s.value for s in families["scrobbler_active_users"].samples}
    assert active == {"1h": 1, "24h": 1, "7d": 1}
    assert families["scrobbler_now_playing_active"].samples[0].value == 1
    cache = {s.labels["status"]: s.value for s in families["scrobbler_art_cache"].samples}
    assert cache == {"pending": 0, "found": 0, "not_found": 0, "error": 0}
    assert families["scrobbler_art_oldest_pending_seconds"].samples[0].value == 0
    overrides = {
        (s.labels["kind"], s.labels["status"]): s.value
        for s in families["scrobbler_art_overrides"].samples
    }
    assert len(overrides) == 6 and not any(overrides.values())
    assert families["scrobbler_art_override_oldest_pending_seconds"].samples[0].value == 0
    assert families["scrobbler_art_unresolved_albums"].samples[0].value == 0


def test_database_stats_collector_caches(app, user):
    collector = DatabaseStatsCollector(app.config["SQLALCHEMY_DATABASE_URI"], ttl_seconds=60)
    first = collector.values()
    user_count = first["users"]
    from scrobbler.services.accounts import register

    register("carol", "carol@example.com", "password123")
    assert collector.values()["users"] == user_count  # still cached


def test_database_stats_collector_survives_db_outage():
    collector = DatabaseStatsCollector("postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    assert list(collector.collect()) == []


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str, retries: int = 50) -> str:
    for _ in range(retries):
        try:
            return urllib.request.urlopen(url, timeout=2).read().decode()
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"{url} never answered")


@pytest.mark.skipif(sys.platform == "win32", reason="gunicorn is POSIX only")
def test_gunicorn_aggregates_worker_metrics(app, tmp_path):
    api_port, metrics_port = _free_port(), _free_port()
    env = {
        **os.environ,
        "BIND": f"127.0.0.1:{api_port}",
        "WEB_CONCURRENCY": "2",
        "METRICS_PORT": str(metrics_port),
        "PROMETHEUS_MULTIPROC_DIR": str(tmp_path / "metrics"),
        "DATABASE_URL": app.config["SQLALCHEMY_DATABASE_URI"],
        "START_METRICS_SERVER": "0",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "-c", str(ROOT / "gunicorn.conf.py")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _get(f"http://127.0.0.1:{api_port}/api/openapi.json")
        for _ in range(9):  # spread over both workers
            _get(f"http://127.0.0.1:{api_port}/api/openapi.json")
        body = _get(f"http://127.0.0.1:{metrics_port}/metrics")
    finally:
        server.terminate()
        server.wait(timeout=10)

    families = {f.name: f for f in text_string_to_metric_families(body)}
    [openapi] = [
        s
        for s in families["scrobbler_http_requests"].samples
        if s.name == "scrobbler_http_requests_total"
        and s.labels.get("endpoint") == "api-docs.openapi_json"
    ]
    assert openapi.value == 10
    assert "scrobbler_users" in families


def test_database_stats_collector_counts_art_backlog(app, user):
    from scrobbler.extensions import db
    from scrobbler.models import AlbumArt, UserAlbumArt

    db.session.add_all(
        [
            AlbumArt(artist="A", album="Lost", status="not_found"),
            AlbumArt(artist="A", album="Fixed", status="error"),
            AlbumArt(artist="A", album="Fine", status="found"),
            UserAlbumArt(user_id=user.id, artist="a", album="fixed", kind="upload", status="found"),
            UserAlbumArt(
                user_id=user.id, artist="A", album="Waiting", kind="correction", status="pending"
            ),
        ]
    )
    db.session.commit()
    values = DatabaseStatsCollector(app.config["SQLALCHEMY_DATABASE_URI"]).values()
    assert values["art_unresolved"] == 1  # "Lost"; "Fixed" has a user's upload
    assert values["art_overrides"][("upload", "found")] == 1
    assert values["art_overrides"][("correction", "pending")] == 1
    assert values["art_override_oldest_pending"] >= 0
