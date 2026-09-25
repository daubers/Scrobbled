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
    if not hasattr(pool, "size"):  # e.g. NullPool in tools: nothing worth reporting
        return
    checked_out = metrics.db_pool_connections.labels(state="checked_out")
    overflow = metrics.db_pool_connections.labels(state="overflow")
    metrics.db_pool_connections.labels(state="size").set(pool.size())

    # Pool events fire before the pool updates its own counters, so reading
    # pool.checkedout() inside them is off by one; count the events instead.
    @event.listens_for(pool, "checkout")
    def _checkout(*_args):
        checked_out.inc()

    @event.listens_for(pool, "checkin")
    def _checkin(*_args):
        checked_out.dec()

    @event.listens_for(pool, "connect")
    def _connect(*_args):
        overflow.set(max(0, pool.overflow()))

    @event.listens_for(pool, "close")
    def _close(*_args):
        overflow.set(max(0, pool.overflow()))
