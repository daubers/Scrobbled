"""Receiving activities.

Intake (in the request): cheap checks only, then store and answer 202. No network calls,
so a flood of deliveries can't make us fetch things.

Processing (the `federation.inbox` worker task): verify the HTTP signature (fetching the
sender's key), check the sender is who the activity says, then dispatch by type.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from flask import current_app
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from scrobbler.extensions import db
from scrobbler.federation import followers, ids, remote, sharing
from scrobbler.federation import metrics as fed_metrics
from scrobbler.federation.models import FederationInboxItem, FederationRemoteActor
from scrobbler.federation.net import FetchError
from scrobbler.federation.protocol import signatures
from scrobbler.federation.protocol.signatures import SignatureError, SignedRequest
from scrobbler.federation.protocol.vocab import object_id

log = logging.getLogger(__name__)

MAX_BYTES = 256 * 1024
MAX_ATTEMPTS = 3
KEPT_FOR = timedelta(days=30)
STALE_AFTER = timedelta(minutes=10)
# Only what signature checks need is kept from the request.
KEPT_HEADERS = (
    "host",
    "date",
    "digest",
    "content-digest",
    "signature",
    "signature-input",
    "authorization",
    "content-type",
    "content-length",
)
# Metric labels are limited to these; anything else is "other".
KNOWN_TYPES = {
    "Follow",
    "Undo",
    "Delete",
    "Update",
    "Accept",
    "Reject",
    "Like",
    "Announce",
    "Create",
    "Add",
    "Remove",
    "Move",
    "Block",
    "Flag",
    "EmojiReact",
}


def type_label(activity_type) -> str:
    return activity_type if activity_type in KNOWN_TYPES else "other"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _config():
    return current_app.extensions["federation"]


# --- Intake -------------------------------------------------------------------------------


def receive(inbox_path: str, headers: dict[str, str], body: bytes) -> tuple[int, str]:
    """Store a delivery for processing. Returns (HTTP status, outcome)."""
    if len(body) > MAX_BYTES:
        return 413, "too_large"
    try:
        doc = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        doc = None
    activity_id = doc.get("id") if isinstance(doc, dict) else None
    activity_type = doc.get("type") if isinstance(doc, dict) else None
    actor = object_id(doc.get("actor")) if isinstance(doc, dict) else None
    if not all(isinstance(v, str) and v for v in (activity_id, activity_type, actor)):
        fed_metrics.inbox_activities_total.labels(type="other", result="invalid").inc()
        return 400, "invalid"
    label = type_label(activity_type)
    if len(activity_id) > 2048 or len(actor) > 2048:
        fed_metrics.inbox_activities_total.labels(type=label, result="invalid").inc()
        return 400, "invalid"
    if _config().is_blocked(_host(actor)):
        fed_metrics.inbox_activities_total.labels(type=label, result="blocked").inc()
        return 403, "blocked"

    kept = {k.lower(): v for k, v in headers.items() if k.lower() in KEPT_HEADERS}
    db.session.add(
        FederationInboxItem(
            activity_id=activity_id,
            activity_type=activity_type[:64],
            actor_uri=actor,
            inbox_path=inbox_path[:512],
            headers=kept,
            body=body,
        )
    )
    try:
        db.session.commit()
    except IntegrityError:  # seen this activity before
        db.session.rollback()
        fed_metrics.inbox_activities_total.labels(type=label, result="duplicate").inc()
        return 202, "duplicate"
    fed_metrics.inbox_activities_total.labels(type=label, result="queued").inc()
    return 202, "queued"


# --- Processing -------------------------------------------------------------------------------


class Rejected(Exception):
    """The activity won't be acted on; `reason` says why."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


Handler = Callable[[dict, FederationRemoteActor], str]
HANDLERS: dict[str, Handler] = {}


def handles(activity_type: str):
    def register(fn: Handler) -> Handler:
        HANDLERS[activity_type] = fn
        return fn

    return register


def _verify(item: FederationInboxItem) -> FederationRemoteActor:
    """Check the signature (as of when it arrived); return the signing actor."""
    request = SignedRequest("POST", ids.base_url() + item.inbox_path, item.headers, item.body)
    try:
        scheme = signatures.detect(request).name
    except SignatureError:
        fed_metrics.signatures_total.labels(direction="in", scheme="none", result="missing").inc()
        raise Rejected("signature_missing") from None
    signer: dict[str, FederationRemoteActor] = {}

    def public_key_for(refresh: bool):
        def lookup(key_id: str):
            actor = remote.actor_for_key(key_id, refresh=refresh)
            signer["actor"] = actor
            return remote.load_public_key(actor.public_key_pem)

        return lookup

    try:
        try:
            signatures.verify(request, public_key_for(False), now=item.received_at)
        except SignatureError as err:
            if err.reason not in ("bad_signature", "unknown_key"):
                raise
            # Maybe they rotated their key: fetch the actor again, once
            signatures.verify(request, public_key_for(True), now=item.received_at)
    except SignatureError as err:
        fed_metrics.signatures_total.labels(direction="in", scheme=scheme, result=err.reason).inc()
        raise Rejected(f"signature_{err.reason}") from err
    except remote.ActorError as err:
        fed_metrics.signatures_total.labels(direction="in", scheme=scheme, result="bad_actor").inc()
        raise Rejected("bad_actor") from err
    fed_metrics.signatures_total.labels(direction="in", scheme=scheme, result="ok").inc()
    return signer["actor"]


def _handle(item: FederationInboxItem) -> str:
    actor = _verify(item)
    doc = json.loads(item.body)
    if object_id(doc.get("actor")) != actor.uri:
        raise Rejected("actor_mismatch")  # signed by someone other than the actor
    if _host(doc["id"]) != actor.host:
        raise Rejected("id_host_mismatch")
    if _config().is_blocked(actor.host):
        raise Rejected("blocked_domain")
    handler = HANDLERS.get(doc.get("type"))
    return handler(doc, actor) if handler else "recorded"


def claim_next() -> FederationInboxItem | None:
    item = db.session.scalar(
        db.select(FederationInboxItem)
        .filter_by(status="queued")
        .order_by(FederationInboxItem.received_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if item is None:
        db.session.rollback()
        return None
    item.status = "processing"  # processing commits (e.g. caching actors), so mark it
    item.attempts += 1
    db.session.commit()
    return item


def process(item: FederationInboxItem) -> None:
    label = type_label(item.activity_type)
    try:
        outcome = _handle(item)
    except Rejected as rejected:
        db.session.rollback()
        _finish(item, "rejected", rejected.reason)
        fed_metrics.inbox_activities_total.labels(type=label, result="rejected").inc()
        return
    except Exception as err:
        db.session.rollback()
        transient = isinstance(err, FetchError) and err.reason == "network"
        if not transient:
            log.exception("inbox item %s failed", item.id)
        if item.attempts < MAX_ATTEMPTS:
            _finish(item, "queued", f"retrying: {err}"[:255], done=False)
            return
        _finish(item, "failed", str(err)[:255])
        fed_metrics.inbox_activities_total.labels(type=label, result="failed").inc()
        return
    db.session.commit()
    _finish(item, "processed", outcome)
    fed_metrics.inbox_activities_total.labels(type=label, result="processed").inc()


def _finish(item: FederationInboxItem, status: str, reason: str, done: bool = True) -> None:
    item = db.session.get(FederationInboxItem, item.id)
    item.status, item.reason = status, reason
    if done:
        item.processed_at = datetime.now(UTC)
    db.session.commit()


def work_once() -> bool:
    """Worker task: process the oldest queued delivery."""
    item = claim_next()
    if item is None:
        return False
    process(item)
    return True


def maintenance() -> None:
    """Worker task (periodic): re-queue items a dead worker left behind; drop old ones."""
    now = datetime.now(UTC)
    db.session.execute(
        update(FederationInboxItem)
        .where(FederationInboxItem.status == "processing")
        .where(FederationInboxItem.received_at < now - STALE_AFTER)
        .values(status="queued")
    )
    db.session.execute(
        delete(FederationInboxItem)
        .where(FederationInboxItem.status != "queued")
        .where(FederationInboxItem.received_at < now - KEPT_FOR)
    )
    db.session.commit()


# --- Handlers ----------------------------------------------------------------------------------


def _local_user(target):
    """The sharing user an activity is addressed to, or Rejected."""
    username = ids.username_from_actor_id(object_id(target))
    found = sharing.shared_user(username) if username else None
    if found is None:
        raise Rejected("not_our_user")
    return found


@handles("Follow")
def on_follow(doc: dict, actor: FederationRemoteActor) -> str:
    user, settings = _local_user(doc.get("object"))
    outcome = followers.follow_received(
        user.id, user.username, settings.manually_approves_followers, actor, doc
    )
    return f"follow_{outcome}"


@handles("Undo")
def on_undo(doc: dict, actor: FederationRemoteActor) -> str:
    inner = doc.get("object")
    if not isinstance(inner, dict) or inner.get("type") != "Follow":
        return "undo_ignored"
    if object_id(inner.get("actor")) not in (None, actor.uri):
        raise Rejected("undo_other_actors_follow")
    user, _ = _local_user(inner.get("object"))
    return "unfollowed" if followers.undo_follow(user.id, actor) else "undo_not_following"


@handles("Delete")
def on_delete(doc: dict, actor: FederationRemoteActor) -> str:
    if object_id(doc.get("object")) != actor.uri:
        return "delete_ignored"  # a post or similar: we don't store remote content
    followers.actor_deleted(actor)
    return "actor_deleted"


@handles("Update")
def on_update(doc: dict, actor: FederationRemoteActor) -> str:
    obj = doc.get("object")
    if not isinstance(obj, dict) or obj.get("type") not in remote.ACTOR_TYPES:
        return "update_ignored"
    if object_id(obj) != actor.uri:
        raise Rejected("update_other_actor")
    remote.get_actor(actor.uri, refresh=True)
    return "actor_updated"
