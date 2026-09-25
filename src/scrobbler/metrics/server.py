import logging
import os

from prometheus_client import REGISTRY, CollectorRegistry, multiprocess, start_http_server

log = logging.getLogger(__name__)


def metrics_registry() -> CollectorRegistry:
    """Registry to expose: aggregated across workers when running under gunicorn."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY


def start_metrics_server(port: int, registry: CollectorRegistry | None = None) -> bool:
    try:
        start_http_server(port, registry=registry or metrics_registry())
    except OSError as exc:  # e.g. the dev-server reloader's second process
        log.warning("metrics server not started on port %s: %s", port, exc)
        return False
    log.info("metrics server listening on :%s/metrics", port)
    return True
