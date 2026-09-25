"""The background worker: runs tasks that modules register (`flask worker`).

Modules register work at app start-up; the worker itself knows nothing about them.

    register_task(name, fn)                 fn() -> bool: True if it did some work.
                                            Called repeatedly while there's work.
    register_periodic(name, seconds, fn)    fn() every `seconds` (and once at start).

A task that raises is logged, counted and rolled back; the other tasks keep running.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from scrobbler import metrics
from scrobbler.extensions import db

log = logging.getLogger(__name__)


@dataclass
class _Task:
    name: str
    fn: Callable[[], object]
    every: float | None = None  # seconds, for periodic tasks
    next_run: float = 0.0  # monotonic time; 0 = run at the first opportunity


_tasks: dict[str, _Task] = {}


def register_task(name: str, fn: Callable[[], bool]) -> None:
    _tasks[name] = _Task(name, fn)


def register_periodic(name: str, every_seconds: float, fn: Callable[[], object]) -> None:
    _tasks[name] = _Task(name, fn, every=every_seconds)


def registered() -> list[str]:
    return sorted(_tasks)


def _call(task: _Task) -> bool:
    try:
        worked = bool(task.fn())
    except Exception:
        log.exception("worker task %s failed", task.name)
        db.session.rollback()
        metrics.worker_tasks_total.labels(task=task.name, outcome="error").inc()
        return False
    if worked or task.every is not None:
        metrics.worker_tasks_total.labels(task=task.name, outcome="worked").inc()
    return worked


def run_once() -> bool:
    """One pass: due periodic tasks, then each queue task once. True if any queue
    task did work (so there may be more waiting)."""
    now = time.monotonic()
    worked = False
    for task in list(_tasks.values()):
        if task.every is not None:
            if now >= task.next_run:
                task.next_run = now + task.every
                _call(task)
        elif _call(task):
            worked = True
    return worked


def run(poll_seconds: float = 2.0, once: bool = False) -> None:
    """Run until stopped. With once=True, run until there's no more work, then return."""
    for task in _tasks.values():
        task.next_run = 0.0
    while True:
        if run_once():
            continue
        if once:
            return
        time.sleep(poll_seconds)
