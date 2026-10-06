"""The background worker: runs tasks that modules register (`flask worker`).

Modules register work on an app at start-up; the worker itself knows nothing about them.
Each app has its own tasks, so apps with different modules enabled never share them.

    register_task(app, name, fn)                fn() -> bool: True if it did some work.
                                                Called repeatedly while there's work.
    register_periodic(app, name, seconds, fn)   fn() every `seconds` (and once at start).

A task that raises is logged, counted and rolled back; the other tasks keep running.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from flask import Flask, current_app

from scrobbler import metrics
from scrobbler.extensions import db

log = logging.getLogger(__name__)


@dataclass
class _Task:
    name: str
    fn: Callable[[], object]
    every: float | None = None  # seconds, for periodic tasks
    next_run: float = 0.0  # monotonic time; 0 = run at the first opportunity


def tasks(app: Flask | None = None) -> dict[str, _Task]:
    return (app or current_app).extensions.setdefault("worker_tasks", {})


def register_task(app: Flask, name: str, fn: Callable[[], bool]) -> None:
    tasks(app)[name] = _Task(name, fn)


def register_periodic(
    app: Flask, name: str, every_seconds: float, fn: Callable[[], object]
) -> None:
    tasks(app)[name] = _Task(name, fn, every=every_seconds)


def registered(app: Flask | None = None) -> list[str]:
    return sorted(tasks(app))


def _call(task: _Task) -> bool:
    started = time.monotonic()
    try:
        result = task.fn()
    except Exception:
        log.exception(
            "worker task %s failed after %.0f ms",
            task.name,
            _elapsed_ms(started),
            extra={"task": task.name, "outcome": "error", "duration_ms": _elapsed_ms(started)},
        )
        db.session.rollback()
        metrics.worker_tasks_total.labels(task=task.name, outcome="error").inc()
        return False
    worked = bool(result)
    if worked or task.every is not None:
        metrics.worker_tasks_total.labels(task=task.name, outcome="worked").inc()
    duration = _elapsed_ms(started)
    # Queue tasks are polled every couple of seconds, so only passes that did work are
    # worth an INFO line; periodic tasks say so only when they report work (a truthy result).
    log.log(
        logging.INFO if worked else logging.DEBUG,
        "worker task %s %s in %.0f ms",
        task.name,
        "worked" if worked else "ran (nothing to do)",
        duration,
        extra={
            "task": task.name,
            "outcome": "worked" if worked else "idle",
            "duration_ms": duration,
        },
    )
    return worked


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def run_once() -> bool:
    """One pass: due periodic tasks, then each queue task once. True if any queue
    task did work (so there may be more waiting)."""
    now = time.monotonic()
    worked = False
    for task in list(tasks().values()):
        if task.every is not None:
            if now >= task.next_run:
                task.next_run = now + task.every
                _call(task)
        elif _call(task):
            worked = True
    return worked


def run(poll_seconds: float = 2.0, once: bool = False) -> None:
    """Run until stopped. With once=True, run until there's no more work, then return."""
    for task in tasks().values():
        task.next_run = 0.0
    while True:
        if run_once():
            continue
        if once:
            return
        time.sleep(poll_seconds)
