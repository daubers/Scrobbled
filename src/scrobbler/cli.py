import logging

import click
from flask import current_app
from flask.cli import AppGroup, with_appcontext


def _run_worker(once: bool, poll: float, serve_metrics: bool) -> None:
    from scrobbler import worker

    if serve_metrics:
        from scrobbler.metrics.server import start_metrics_server

        start_metrics_server(current_app.config["WORKER_METRICS_PORT"])
    logging.getLogger(__name__).info("worker tasks: %s", ", ".join(worker.registered()))
    worker.run(poll_seconds=poll, once=once)


def _worker_options(fn):
    fn = click.option(
        "--metrics/--no-metrics", "serve_metrics", default=True, help="Serve Prometheus metrics."
    )(fn)
    fn = click.option(
        "--poll", default=2.0, show_default=True, help="Seconds between checks when idle."
    )(fn)
    return click.option("--once", is_flag=True, help="Do all waiting work, then exit.")(fn)


@click.command("worker")
@_worker_options
@with_appcontext
def worker_command(once: bool, poll: float, serve_metrics: bool) -> None:
    """Run background work: imports, and tasks registered by optional modules."""
    _run_worker(once, poll, serve_metrics)


imports_cli = AppGroup("imports", help="History imports.")


@imports_cli.command("worker")
@_worker_options
def imports_worker(once: bool, poll: float, serve_metrics: bool) -> None:
    """Alias for `flask worker` (kept for existing deployments)."""
    _run_worker(once, poll, serve_metrics)
