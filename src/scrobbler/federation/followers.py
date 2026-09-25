"""Follower state changes, from incoming activities and from the user's own actions.

Every change that the other server needs to know about queues an activity (Accept,
Reject or Block); the delivery task sends it. Callers commit.
"""

from datetime import UTC, datetime

from scrobbler.extensions import db
from scrobbler.federation import activities
from scrobbler.federation.models import FederationBlock, FederationFollower, FederationRemoteActor


def _now() -> datetime:
    return datetime.now(UTC)


def get(user_id: int, actor: FederationRemoteActor) -> FederationFollower | None:
    return db.session.scalar(
        db.select(FederationFollower).filter_by(user_id=user_id, remote_actor_id=actor.id)
    )


def is_blocked(user_id: int, actor: FederationRemoteActor) -> bool:
    return (
        db.session.scalar(
            db.select(FederationBlock).filter_by(user_id=user_id, remote_actor_id=actor.id)
        )
        is not None
    )


def follow_received(
    user_id: int, username: str, manual_approval: bool, actor: FederationRemoteActor, follow: dict
) -> str:
    """Handle a Follow. Returns what happened: accepted, pending, refollowed or blocked."""
    if is_blocked(user_id, actor):
        activities.reject_follow_activity(user_id, username, follow, actor)
        return "blocked"
    follower = get(user_id, actor)
    if follower is not None:
        # A repeat Follow (e.g. they lost our Accept): keep the newest activity and
        # answer again if we'd already accepted.
        follower.follow_activity_id, follower.follow_activity = follow["id"], follow
        if follower.state == "accepted":
            activities.accept_follow(username, follower)
            return "refollowed"
        return "pending"
    follower = FederationFollower(
        user_id=user_id,
        remote_actor_id=actor.id,
        actor=actor,
        state="pending" if manual_approval else "accepted",
        follow_activity_id=follow["id"],
        follow_activity=follow,
        accepted_at=None if manual_approval else _now(),
    )
    db.session.add(follower)
    db.session.flush()
    if not manual_approval:
        activities.accept_follow(username, follower)
        return "accepted"
    return "pending"


def undo_follow(user_id: int, actor: FederationRemoteActor) -> bool:
    follower = get(user_id, actor)
    if follower is None:
        return False
    db.session.delete(follower)
    return True


def actor_deleted(actor: FederationRemoteActor) -> int:
    """The remote account was deleted: it follows no one here any more."""
    followers = db.session.scalars(
        db.select(FederationFollower).filter_by(remote_actor_id=actor.id)
    ).all()
    for follower in followers:
        db.session.delete(follower)
    actor.gone = True
    return len(followers)


# --- The user's own actions --------------------------------------------------------------


def approve(username: str, follower: FederationFollower) -> None:
    if follower.state != "pending":
        return
    follower.state, follower.accepted_at = "accepted", _now()
    activities.accept_follow(username, follower)


def remove(username: str, follower: FederationFollower) -> None:
    """Decline a request or remove a follower: both are a Reject for the other server."""
    activities.reject_follow(username, follower)
    db.session.delete(follower)


def block(username: str, follower: FederationFollower) -> FederationBlock:
    blocked = FederationBlock(user_id=follower.user_id, remote_actor_id=follower.remote_actor_id)
    db.session.add(blocked)
    activities.block(follower.user_id, username, follower.actor)
    db.session.delete(follower)
    return blocked


def reject_all(user_id: int, username: str) -> int:
    """Sharing switched off: tell every follower, then forget them."""
    followers = db.session.scalars(db.select(FederationFollower).filter_by(user_id=user_id)).all()
    for follower in followers:
        remove(username, follower)
    return len(followers)


def counts(user_id: int) -> dict[str, int]:
    rows = db.session.execute(
        db.select(FederationFollower.state, db.func.count())
        .filter_by(user_id=user_id)
        .group_by(FederationFollower.state)
    ).all()
    found = dict(rows)
    return {"accepted": found.get("accepted", 0), "pending": found.get("pending", 0)}
