"""Gunicorn settings for the API. Metrics from every worker are aggregated through
prometheus_client's multiprocess mode and served by the master on METRICS_PORT."""

import os
import shutil

# Must be set before prometheus_client is imported by anything.
MULTIPROC_DIR = os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", "/tmp/scrobbler-metrics")

wsgi_app = "scrobbler:create_app()"
bind = os.environ.get("BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
accesslog = "-"
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
# gunicorn 26's control socket lives in $HOME, which the container user doesn't have.
control_socket_disable = True


def on_starting(server):
    # Stale files from a previous run would double-count.
    shutil.rmtree(MULTIPROC_DIR, ignore_errors=True)
    os.makedirs(MULTIPROC_DIR, exist_ok=True)


def when_ready(server):
    from scrobbler.metrics.server import start_metrics_server

    start_metrics_server(
        int(os.environ.get("METRICS_PORT", "9100")), os.environ.get("DATABASE_URL")
    )


def child_exit(server, worker):
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(worker.pid)
