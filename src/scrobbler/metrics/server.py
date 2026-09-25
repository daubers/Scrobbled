import logging
import os

from prometheus_client import REGISTRY, CollectorRegistry, multiprocess, start_http_server

from scrobbler.metrics.collectors import DatabaseStatsCollector

log = logging.getLogger(__name__)


def metrics_registry(database_url: str | None = None) -> CollectorRegistry:
    """Registry to expose. Under gunicorn (PROMETHEUS_MULTIPROC_DIR set) it aggregates
    every worker's metrics; otherwise it's this process's default registry."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    if database_url:
        registry.register(DatabaseStatsCollector(database_url))
    return registry


def start_metrics_server(port: int, database_url: str | None = None) -> bool:
    try:
        start_http_server(port, registry=metrics_registry(database_url))
    except OSError as exc:  # e.g. the dev-server reloader's second process
        log.warning("metrics server not started on port %s: %s", port, exc)
        return False
    log.info("metrics server listening on :%s/metrics", port)
    return True
