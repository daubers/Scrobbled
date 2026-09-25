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
