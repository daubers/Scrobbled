from sqlalchemy import inspect

from scrobbler import __version__
from scrobbler.extensions import db


def test_migrations_create_all_tables(app):
    tables = set(inspect(db.engine).get_table_names())
    assert {
        "users",
        "api_apps",
        "ui_tokens",
        "auth_tokens",
        "sessions",
        "scrobbles",
        "now_playing",
    } <= tables


def test_unmatched_requests_use_a_fixed_endpoint_label(client, metric_delta):
    requests = metric_delta(
        "scrobbler_http_requests_total", endpoint="unmatched", http_method="GET", status="404"
    )
    client.get("/no/such/path/123")
    client.get("/another/random/path")
    assert requests.delta == 2


def test_build_info_is_exported(metric_delta):
    from prometheus_client import REGISTRY

    samples = [s for m in REGISTRY.collect() if m.name == "scrobbler_build_info" for s in m.samples]
    assert samples and samples[0].labels["version"] == __version__
