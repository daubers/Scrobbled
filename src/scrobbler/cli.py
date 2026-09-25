import logging

import click
from flask import current_app
from flask.cli import AppGroup

imports_cli = AppGroup("imports", help="History imports.")


@imports_cli.command("worker")
@click.option("--once", is_flag=True, help="Process waiting jobs, then exit.")
@click.option("--poll", default=2.0, show_default=True, help="Seconds between checks when idle.")
@click.option("--metrics/--no-metrics", default=True, help="Serve Prometheus metrics.")
def worker(once: bool, poll: float, metrics: bool) -> None:
    """Process import jobs: uploaded Last.fm exports and pulls from Last.fm."""
    from scrobbler.services.imports import run_worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if metrics:
        from scrobbler.metrics.server import start_metrics_server

        start_metrics_server(current_app.config["IMPORT_WORKER_METRICS_PORT"])
    run_worker(poll_seconds=poll, once=once)
