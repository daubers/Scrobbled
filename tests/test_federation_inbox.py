"""Receiving activities: intake, signature verification, and the handlers."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from federation_helpers import RemoteAccount
from scrobbler import worker
from scrobbler.extensions import db
from scrobbler.federation import inbox, sharing
from scrobbler.federation.models import (
    FederationActivity,
    FederationBlock,
    FederationDelivery,
    FederationFollower,
    FederationInboxItem,
    FederationRemoteActor,
)


@pytest.fixture
def bob(sharing_user, remote):
    return RemoteAccount(remote, "bob")


def process_all():
    worker.run(once=True)
    db.session.expire_all()


def last_item() -> FederationInboxItem:
    return db.session.scalars(
        db.select(FederationInboxItem).order_by(FederationInboxItem.id.desc())
    ).first()


def followers_of(user):
    return db.session.scalars(db.select(FederationFollower).filter_by(user_id=user.id)).all()


def sent(activity_type):
    return db.session.scalars(
        db.select(FederationActivity).filter_by(activity_type=activity_type)
    ).all()


# --- Intake ---------------------------------------------------------------------------------


def test_intake_queues_and_answers_202(fed_client, bob, metric_delta):
    queued = metric_delta(
        "scrobbler_federation_inbox_activities_total", type="Follow", result="queued"
    )
    response = bob.deliver(fed_client, bob.follow())
    assert response.status_code == 202
    item = last_item()
    assert (item.status, item.activity_type, item.actor_uri) == ("queued", "Follow", bob.uri)
    assert set(item.headers) <= set(inbox.KEPT_HEADERS)
    assert queued.delta == 1


def test_duplicates_are_acknowledged_once(fed_client, bob, metric_delta):
    duplicate = metric_delta(
        "scrobbler_federation_inbox_activities_total", type="Follow", result="duplicate"
    )
    follow = bob.follow()
    assert bob.deliver(fed_client, follow).status_code == 202
    assert bob.deliver(fed_client, follow).status_code == 202
    assert db.session.scalar(db.select(db.func.count()).select_from(FederationInboxItem)) == 1
    assert duplicate.delta == 1


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"type": "Follow"}', b'{"id": "x", "type": "Follow", "actor": 7}'],
)
def test_malformed_deliveries_are_400(fed_client, sharing_user, body):
    response = fed_client.post("/inbox", data=body, content_type="application/activity+json")
    assert response.status_code == 400


def test_wrong_content_type_is_415(fed_client, bob):
    assert bob.deliver(fed_client, bob.follow(), content_type="text/plain").status_code == 415


def test_too_large_is_413(fed_client, sharing_user):
    response = fed_client.post(
        "/inbox", data=b"x" * (inbox.MAX_BYTES + 1), content_type="application/activity+json"
    )
    assert response.status_code == 413


def test_blocked_domains_are_refused(fed_client, fed_app, bob):
    config = fed_app.extensions["federation"]
    fed_app.extensions["federation"] = type(config)(
        **{**config.__dict__, "blocked_domains": ("127.0.0.1",)}
    )
    try:
        assert bob.deliver(fed_client, bob.follow()).status_code == 403
    finally:
        fed_app.extensions["federation"] = config


# --- Follows -----------------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["draft-cavage", "rfc9421"])
def test_follow_is_accepted(fed_client, bob, sharing_user, scheme, metric_delta):
    ok = metric_delta(
        "scrobbler_federation_signatures_total", direction="in", scheme=scheme, result="ok"
    )
    follow = bob.follow()
    bob.deliver(fed_client, follow, scheme=scheme)
    process_all()

    assert (last_item().status, last_item().reason) == ("processed", "follow_accepted")
    [follower] = followers_of(sharing_user)
    assert (follower.state, follower.actor.uri, follower.follow_activity_id) == (
        "accepted",
        bob.uri,
        follow["id"],
    )
    [accept] = sent("Accept")
    assert accept.document["actor"] == "https://scrobble.test/users/alice"
    assert accept.document["object"] == follow
    assert accept.document["to"] == [bob.uri]
    [delivery] = db.session.scalars(db.select(FederationDelivery)).all()
    assert delivery.inbox == f"{bob.uri}/inbox"  # sending is covered in test_federation_delivery
    assert ok.delta == 1


def test_manual_approval_leaves_follow_pending(fed_client, bob, sharing_user):
    sharing.update(sharing_user.id, {"manually_approves_followers": True})
    bob.deliver(fed_client, bob.follow())
    process_all()
    assert [f.state for f in followers_of(sharing_user)] == ["pending"]
    assert sent("Accept") == []


def test_repeat_follow_is_accepted_again(fed_client, bob, sharing_user):
    bob.deliver(fed_client, bob.follow())
    process_all()
    second = bob.follow()
    bob.deliver(fed_client, second)
    process_all()
    assert last_item().reason == "follow_refollowed"
    assert len(followers_of(sharing_user)) == 1
    assert followers_of(sharing_user)[0].follow_activity_id == second["id"]
    assert len(sent("Accept")) == 2


def test_follow_for_someone_not_sharing(fed_client, bob, make_user):
    make_user(username="carol")
    bob.deliver(fed_client, bob.follow("carol"), path="/inbox")
    process_all()
    assert (last_item().status, last_item().reason) == ("rejected", "not_our_user")


def test_blocked_account_gets_rejected(fed_client, bob, sharing_user):
    bob.deliver(fed_client, bob.follow())
    process_all()
    actor = db.session.scalar(db.select(FederationRemoteActor).filter_by(uri=bob.uri))
    db.session.add(FederationBlock(user_id=sharing_user.id, remote_actor_id=actor.id))
    db.session.delete(followers_of(sharing_user)[0])
    db.session.commit()
    bob.deliver(fed_client, bob.follow())
    process_all()
    assert last_item().reason == "follow_blocked"
    assert followers_of(sharing_user) == []
    assert len(sent("Reject")) == 1


# --- Undo, Delete, Update -----------------------------------------------------------------------


def test_undo_follow(fed_client, bob, sharing_user):
    follow = bob.follow()
    bob.deliver(fed_client, follow)
    process_all()
    bob.deliver(fed_client, bob.activity("Undo", follow))
    process_all()
    assert last_item().reason == "unfollowed"
    assert followers_of(sharing_user) == []


def test_undo_referencing_the_follow_by_id(fed_client, bob, sharing_user):
    follow = bob.follow()
    bob.deliver(fed_client, follow)
    process_all()
    bob.deliver(fed_client, bob.activity("Undo", follow["id"]))
    process_all()
    assert last_item().reason == "unfollowed"
    assert followers_of(sharing_user) == []


def test_rejects_reference_the_follow_by_id(fed_client, bob, sharing_user):
    """So a late Reject can't be matched to a newer request between the same accounts."""
    from scrobbler.federation import followers

    follow = bob.follow()
    bob.deliver(fed_client, follow)
    process_all()
    followers.remove("alice", followers_of(sharing_user)[0])
    db.session.commit()
    [reject] = sent("Reject")
    assert reject.document["object"] == follow["id"]


def test_a_late_undo_of_an_old_follow_keeps_the_new_one(fed_client, bob, sharing_user):
    """Deliveries race: an Undo of an earlier Follow can arrive after a newer Follow
    (seen against GoToSocial). Only an Undo of the current Follow removes the follower."""
    old_follow = bob.follow()
    bob.deliver(fed_client, old_follow)
    process_all()
    new_follow = bob.follow()
    bob.deliver(fed_client, new_follow)
    bob.deliver(fed_client, bob.activity("Undo", old_follow))  # arrives after the new Follow
    process_all()
    assert last_item().reason == "undo_stale"
    [follower] = followers_of(sharing_user)
    assert follower.follow_activity_id == new_follow["id"]


def test_undo_of_someone_elses_follow_is_rejected(fed_client, bob, remote, sharing_user):
    carol = RemoteAccount(remote, "carol")
    follow = carol.follow()
    carol.deliver(fed_client, follow)
    process_all()
    bob.deliver(fed_client, bob.activity("Undo", follow))
    process_all()
    assert (last_item().status, last_item().reason) == ("rejected", "undo_other_actors_follow")
    assert len(followers_of(sharing_user)) == 1


def test_deleted_account_stops_following(fed_client, bob, remote, sharing_user):
    bob.deliver(fed_client, bob.follow())
    process_all()
    remote.routes[f"/users/{bob.name}"] = (410, {}, b"")  # the account is gone now
    bob.deliver(fed_client, bob.activity("Delete", bob.uri))
    process_all()
    assert last_item().reason == "actor_deleted"
    assert followers_of(sharing_user) == []
    assert db.session.scalar(db.select(FederationRemoteActor).filter_by(uri=bob.uri)).gone


def test_update_refreshes_the_actor(fed_client, bob, sharing_user):
    bob.deliver(fed_client, bob.follow())
    process_all()
    bob.serve(name="Robert")
    bob.deliver(fed_client, bob.activity("Update", bob.document(name="Robert")))
    process_all()
    assert last_item().reason == "actor_updated"
    assert (
        db.session.scalar(db.select(FederationRemoteActor).filter_by(uri=bob.uri)).display_name
        == "Robert"
    )


def test_other_activities_are_recorded(fed_client, bob, sharing_user):
    bob.deliver(fed_client, bob.activity("Like", "https://scrobble.test/activities/x"))
    process_all()
    assert (last_item().status, last_item().reason) == ("processed", "recorded")


# --- Signatures and identity ---------------------------------------------------------------------


def test_unsigned_deliveries_are_rejected(fed_client, bob):
    bob.deliver(fed_client, bob.follow(), scheme=None)
    process_all()
    assert (last_item().status, last_item().reason) == ("rejected", "signature_missing")


def test_wrong_key_is_rejected_after_one_refresh(fed_client, bob, remote, metric_delta):
    failed = metric_delta(
        "scrobbler_federation_signatures_total",
        direction="in",
        scheme="draft-cavage",
        result="bad_signature",
    )
    impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bob.deliver(fed_client, bob.follow(), key=impostor)
    process_all()
    assert (last_item().status, last_item().reason) == ("rejected", "signature_bad_signature")
    assert [r["path"] for r in remote.requests].count("/users/bob") == 2  # fetched, then refreshed
    assert failed.delta == 1


def test_rotated_key_is_picked_up(fed_client, bob, sharing_user):
    bob.deliver(fed_client, bob.follow())
    process_all()
    bob.rotate_key()
    bob.serve()  # the new key is published...
    bob.deliver(fed_client, bob.activity("Undo", followers_of(sharing_user)[0].follow_activity))
    process_all()  # ...and our cached old key fails once, then we refetch
    assert last_item().reason == "unfollowed"


def test_signed_by_one_actor_claiming_another_is_rejected(fed_client, bob, remote, sharing_user):
    carol = RemoteAccount(remote, "carol")
    forged = carol.follow()  # carol's Follow...
    bob.deliver(fed_client, forged)  # ...signed with bob's key
    process_all()
    assert (last_item().status, last_item().reason) == ("rejected", "actor_mismatch")
    assert followers_of(sharing_user) == []


def test_activity_id_on_another_host_is_rejected(fed_client, bob, sharing_user):
    follow = bob.follow()
    follow["id"] = "https://elsewhere.example/activities/1"
    bob.deliver(fed_client, follow)
    process_all()
    assert (last_item().status, last_item().reason) == ("rejected", "id_host_mismatch")


def test_signature_age_is_judged_by_arrival_time(fed_client, bob, sharing_user):
    """A backlog mustn't turn valid deliveries into expired ones, and vice versa."""
    bob.deliver(fed_client, bob.follow())
    item = last_item()
    item.received_at = datetime.now(UTC) + timedelta(hours=13)
    db.session.commit()
    process_all()
    assert last_item().reason == "signature_expired"


# --- Robustness ----------------------------------------------------------------------------------


def test_unreachable_sender_is_retried_then_failed(fed_client, bob, remote):
    bob.deliver(fed_client, bob.follow())
    remote.close()  # their server is down: fetching the key fails
    for _ in range(inbox.MAX_ATTEMPTS):
        process_all()
        item = last_item()
        if item.status != "queued":
            break
    assert (item.status, item.attempts) == ("failed", inbox.MAX_ATTEMPTS)


def test_maintenance_requeues_stale_and_prunes_old(fed_client, bob):
    bob.deliver(fed_client, bob.follow())
    item = last_item()
    item.status, item.received_at = "processing", datetime.now(UTC) - timedelta(minutes=30)
    db.session.commit()
    inbox.maintenance()
    db.session.expire_all()
    assert last_item().status == "queued"

    item = last_item()
    item.status, item.received_at = "processed", datetime.now(UTC) - timedelta(days=31)
    db.session.commit()
    inbox.maintenance()
    assert db.session.scalar(db.select(db.func.count()).select_from(FederationInboxItem)) == 0


def test_activities_are_only_fetchable_when_public(fed_client, bob, sharing_user):
    bob.deliver(fed_client, bob.follow())
    process_all()
    [accept] = sent("Accept")
    assert fed_client.get(f"/activities/{accept.id}").status_code == 404
    accept.public = True
    db.session.commit()
    assert json.loads(fed_client.get(f"/activities/{accept.id}").data)["type"] == "Accept"
