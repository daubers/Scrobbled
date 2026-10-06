"""Sending queued activities (the `federation.deliver` worker task).

Each delivery is one activity to one inbox, signed as the user. Failures are retried
with backoff (1m, 5m, 30m, 2h, 6h, then every 12h) and abandoned after 2 days. A claimed
delivery is leased (its next attempt pushed forward), so one a crashed worker was
sending simply becomes due again.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy import func
from sqlalchemy.orm import lazyload

from scrobbler.extensions import db
from scrobbler.federation import ids, outbound
from scrobbler.federation import metrics as fed_metrics
from scrobbler.federation.models import (
    FederationDelivery,
    FederationFollower,
    FederationInboxItem,
    FederationRemoteActor,
)
from scrobbler.federation.net import FetchError

log = logging.getLogger(__name__)

RETRY_DELAYS = [
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
]
LATER_DELAY = timedelta(hours=12)
GIVE_UP_AFTER = timedelta(days=2)
LEASE = timedelta(minutes=10)
BATCH = 50
RETRYABLE_4XX = {401, 408, 429}


def _now() -> datetime:
    return datetime.now(UTC)


def claim(limit: int = BATCH) -> list[int]:
    now = _now()
    due = db.session.scalars(
        db.select(FederationDelivery)
        # No eager join here: Postgres can't lock rows on the nullable side of an outer join
        .options(lazyload(FederationDelivery.activity))
        .filter(FederationDelivery.status == "pending", FederationDelivery.next_attempt_at <= now)
        .order_by(FederationDelivery.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for delivery in due:
        delivery.next_attempt_at = now + LEASE  # our lease while we send
        delivery.attempts += 1
    db.session.commit()
    return [d.id for d in due]


def _retry_or_abandon(delivery: FederationDelivery, error: str, status: int | None) -> str:
    delivery.last_status, delivery.last_error = status, error[:1000]
    if _now() - delivery.created_at >= GIVE_UP_AFTER:
        delivery.status = "abandoned"
        log.warning(
            "delivery %s to %s abandoned after %s attempts: %s",
            delivery.id,
            delivery.inbox,
            delivery.attempts,
            error[:200],
        )
        return "abandoned"
    delay = (
        RETRY_DELAYS[delivery.attempts - 1]
        if delivery.attempts <= len(RETRY_DELAYS)
        else LATER_DELAY
    )
    delivery.next_attempt_at = _now() + delay
    log.warning(
        "delivery %s to %s failed (attempt %s), retrying in %s: %s",
        delivery.id,
        delivery.inbox,
        delivery.attempts,
        delay,
        error[:200],
    )
    return "retry"


def _gone(inbox: str) -> None:
    """410 from an inbox: whoever it belongs to is gone and follows no one here."""
    actors = db.session.scalars(
        db.select(FederationRemoteActor).filter(
            (FederationRemoteActor.inbox == inbox) | (FederationRemoteActor.shared_inbox == inbox)
        )
    ).all()
    for actor in actors:
        actor.gone = True
        db.session.execute(db.delete(FederationFollower).filter_by(remote_actor_id=actor.id))


def deliver(delivery_id: int) -> str:
    """Send one delivery. Returns the outcome (also its metric label)."""
    delivery = db.session.get(FederationDelivery, delivery_id)
    document = delivery.activity.document
    username = ids.username_from_actor_id(document.get("actor"))
    started = time.perf_counter()
    try:
        response, scheme = outbound.signed_post(
            delivery.inbox, document, user_id=delivery.activity.user_id, username=username
        )
    except FetchError as err:
        if err.reason == "blocked":  # would never be allowed: don't retry
            delivery.status, delivery.last_error = "abandoned", str(err)[:1000]
            outcome = "abandoned"
        else:
            outcome = _retry_or_abandon(delivery, str(err), err.status)
        db.session.commit()
        return outcome
    fed_metrics.delivery_seconds.observe(time.perf_counter() - started)
    fed_metrics.signatures_total.labels(direction="out", scheme=scheme, result="sent").inc()

    status = response.status
    delivery.last_status = status
    if 200 <= status < 300:
        delivery.status, delivery.delivered_at, delivery.last_error = "delivered", _now(), None
        outcome = "delivered"
    elif status == 410:
        delivery.status, delivery.last_error = "abandoned", "410 Gone"
        _gone(delivery.inbox)
        outcome = "gone"
    elif 400 <= status < 500 and status not in RETRYABLE_4XX:
        delivery.status = "abandoned"
        delivery.last_error = f"{status}: {response.body[:500].decode('utf-8', 'replace')}"
        outcome = "abandoned"
    else:
        outcome = _retry_or_abandon(delivery, f"HTTP {status}", status)
    db.session.commit()
    return outcome


def _deliver_in_context(app, delivery_id: int) -> str:
    with app.app_context():
        try:
            return deliver(delivery_id)
        except Exception:
            log.exception("delivery %s failed unexpectedly (will be retried)", delivery_id)
            db.session.rollback()
            return "retry"  # the lease expires and it becomes due again
        finally:
            db.session.remove()


def work_once() -> bool:
    """Worker task: send a batch of due deliveries, several at a time."""
    claimed = claim()
    if not claimed:
        return False
    app = current_app._get_current_object()
    workers = min(current_app.extensions["federation"].delivery_concurrency, len(claimed))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for outcome in pool.map(lambda d: _deliver_in_context(app, d), claimed):
            fed_metrics.deliveries_total.labels(result=outcome).inc()
    return True


QUEUE_STATUSES = {
    "inbox": (FederationInboxItem, ("queued", "processing", "processed", "rejected", "failed")),
    "deliveries": (FederationDelivery, ("pending", "delivered", "abandoned")),
}


def maintenance() -> None:
    """Worker task (periodic): publish queue and follower gauges."""
    for queue, (model, statuses) in QUEUE_STATUSES.items():
        counts = dict(
            db.session.execute(db.select(model.status, func.count()).group_by(model.status)).all()
        )
        for status in statuses:
            fed_metrics.queue.labels(queue=queue, status=status).set(counts.get(status, 0))
    states = dict(
        db.session.execute(
            db.select(FederationFollower.state, func.count()).group_by(FederationFollower.state)
        ).all()
    )
    for state in ("accepted", "pending"):
        fed_metrics.followers.labels(state=state).set(states.get(state, 0))
    db.session.rollback()
