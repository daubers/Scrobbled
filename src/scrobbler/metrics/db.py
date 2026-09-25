"""SQLAlchemy hooks: statement latency and connection-pool state."""

import time

from sqlalchemy import event
from sqlalchemy.engine import Engine

from scrobbler import metrics

_OPERATIONS = {"select", "insert", "update", "delete"}


def _operation(statement: str) -> str:
    word = statement.lstrip().split(None, 1)[0].lower() if statement.strip() else ""
    return word if word in _OPERATIONS else "other"


def instrument(engine: Engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("_query_start", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        start = conn.info["_query_start"].pop()
        metrics.db_query_duration_seconds.labels(operation=_operation(statement)).observe(
            time.perf_counter() - start
        )

    @event.listens_for(engine, "handle_error")
    def _error(context):
        stack = context.connection.info.get("_query_start") if context.connection else None
        if stack:
            stack.pop()

    pool = engine.pool

    def _report_pool(*_args):
        # QueuePool exposes these; other pools (e.g. NullPool in tools) don't.
        if not hasattr(pool, "checkedout"):
            return
        metrics.db_pool_connections.labels(state="checked_out").set(pool.checkedout())
        metrics.db_pool_connections.labels(state="idle").set(pool.checkedin())
        metrics.db_pool_connections.labels(state="overflow").set(max(0, pool.overflow()))
        metrics.db_pool_connections.labels(state="size").set(pool.size())

    for name in ("connect", "checkout", "checkin", "close"):
        event.listen(pool, name, _report_pool)
