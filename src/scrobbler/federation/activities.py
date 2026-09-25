"""Activities we send: stored (so they have a stable id) and queued for delivery."""

import uuid

from scrobbler.extensions import db
from scrobbler.federation import ids
from scrobbler.federation.models import (
    FederationActivity,
    FederationDelivery,
    FederationFollower,
    FederationRemoteActor,
)
from scrobbler.federation.protocol import vocab


def _store(user_id: int, document_for, public: bool = False) -> FederationActivity:
    """Create an activity. `document_for(activity_url)` builds its JSON."""
    activity_uuid = str(uuid.uuid4())
    document = document_for(ids.activity_url(activity_uuid))
    activity = FederationActivity(
        id=activity_uuid,
        user_id=user_id,
        activity_type=document["type"],
        document=document,
        public=public,
    )
    db.session.add(activity)
    return activity


def queue(activity: FederationActivity, inboxes: list[str]) -> list[FederationDelivery]:
    deliveries = [
        FederationDelivery(activity_id=activity.id, inbox=inbox)
        for inbox in dict.fromkeys(inboxes)  # one per distinct inbox
    ]
    db.session.add_all(deliveries)
    return deliveries


def accept_follow(username: str, follower: FederationFollower) -> FederationActivity:
    activity = _store(
        follower.user_id,
        lambda url: vocab.accept(url, ids.actor_id(username), follower.follow_activity),
    )
    queue(activity, [follower.actor.inbox])  # replies go to their own inbox
    return activity


def reject_follow(username: str, follower: FederationFollower) -> FederationActivity:
    activity = _store(
        follower.user_id,
        lambda url: vocab.reject(url, ids.actor_id(username), follower.follow_activity),
    )
    queue(activity, [follower.actor.inbox])
    return activity


def reject_follow_activity(
    user_id: int, username: str, follow: dict, actor: FederationRemoteActor
) -> FederationActivity:
    """Reject a Follow that never became a follower (e.g. from a blocked account)."""
    activity = _store(user_id, lambda url: vocab.reject(url, ids.actor_id(username), follow))
    queue(activity, [actor.inbox])
    return activity


def block(user_id: int, username: str, actor: FederationRemoteActor) -> FederationActivity:
    activity = _store(user_id, lambda url: vocab.block(url, ids.actor_id(username), actor.uri))
    queue(activity, [actor.inbox])
    return activity


def undo_block(
    user_id: int, username: str, actor: FederationRemoteActor
) -> FederationActivity | None:
    """Tell them they're unblocked: Undo our most recent Block of them (if we sent one)."""
    block_activity = db.session.scalars(
        db.select(FederationActivity)
        .filter_by(user_id=user_id, activity_type="Block")
        .filter(FederationActivity.document["object"].as_string() == actor.uri)
        .order_by(FederationActivity.created_at.desc())
    ).first()
    if block_activity is None:
        return None
    activity = _store(
        user_id, lambda url: vocab.undo(url, ids.actor_id(username), block_activity.document)
    )
    queue(activity, [actor.inbox])
    return activity
