"""Publishing core: idempotent posting, addressing, fan-out and deletion."""

import pytest

from federation_helpers import RemoteAccount
from scrobbler.extensions import db
from scrobbler.federation import followers as follower_state
from scrobbler.federation import inbox, publishing, sharing
from scrobbler.federation.models import FederationActivity, FederationDelivery, FederationPost


def all_activities():
    return db.session.scalars(
        db.select(FederationActivity).order_by(FederationActivity.created_at)
    ).all()


def deliveries_for(activity_id):
    return db.session.scalars(
        db.select(FederationDelivery).filter_by(activity_id=activity_id)
    ).all()


def approved_follower(fed_client, remote, sharing_user, name):
    """A remote account following sharing_user, already accepted."""
    account = RemoteAccount(remote, name)
    remote.routes[f"/users/{name}/inbox"] = (202, {}, b"")
    account.deliver(fed_client, account.follow())
    while inbox.work_once():
        pass
    db.session.expire_all()
    return account


def test_publish_creates_a_post_and_a_create_activity(fed_ctx, user, metric_delta):
    sharing.update(user.id, {"enabled": True})
    posted = metric_delta("scrobbler_federation_posts_total", kind="weekly", visibility="followers")
    post = publishing.publish(
        user.id, "alice", "weekly", "2026-W01", text="312 plays", html="<p>312 plays</p>"
    )
    assert post is not None
    assert posted.delta == 1
    assert (post.kind, post.key, post.content_text) == ("weekly", "weekly:2026-W01", "312 plays")
    assert post.visibility == "followers"
    assert post.deleted_at is None

    [activity] = all_activities()
    assert activity.id == post.activity_id
    assert activity.activity_type == "Create"
    assert activity.document["object"]["type"] == "Note"
    assert activity.document["object"]["content"] == "<p>312 plays</p>"
    assert activity.document["object"]["contentMap"] == {"en": "<p>312 plays</p>"}
    assert activity.document["object"]["tag"] == [{"type": "Hashtag", "name": "#Scrobbler"}]
    assert activity.public is False  # followers-only, the default


def test_publish_is_idempotent(fed_ctx, user):
    sharing.update(user.id, {"enabled": True})
    first = publishing.publish(user.id, "alice", "weekly", "2026-W01", text="a", html="a")
    second = publishing.publish(user.id, "alice", "weekly", "2026-W01", text="b", html="b")
    assert first is not None
    assert second is None
    assert db.session.scalar(db.select(db.func.count()).select_from(FederationPost)) == 1
    # The first call's content wins; a later call never overwrites it
    assert db.session.get(FederationPost, first.id).content_text == "a"


def test_publish_keys_are_scoped_to_kind_and_user(fed_ctx, user, make_user):
    sharing.update(user.id, {"enabled": True})
    same_key_different_kind = publishing.publish(
        user.id, "alice", "milestone", "2026-W01", text="x", html="x"
    )
    assert publishing.publish(user.id, "alice", "weekly", "2026-W01", text="y", html="y")
    assert same_key_different_kind is not None  # "weekly:2026-W01" != "milestone:2026-W01"

    other = make_user(username="bob")
    sharing.update(other.id, {"enabled": True})
    assert publishing.publish(other.id, "bob", "weekly", "2026-W01", text="z", html="z")
    assert db.session.scalar(db.select(db.func.count()).select_from(FederationPost)) == 3


def test_publish_refuses_when_not_sharing(fed_ctx, user):
    assert publishing.publish(user.id, "alice", "weekly", "2026-W01", text="x", html="x") is None
    sharing.update(user.id, {"enabled": True})
    sharing.update(user.id, {"enabled": False})
    assert publishing.publish(user.id, "alice", "weekly", "2026-W02", text="x", html="x") is None
    assert db.session.scalar(db.select(db.func.count()).select_from(FederationPost)) == 0


@pytest.mark.parametrize(
    ("visibility", "to", "cc"),
    [
        ("followers", "FOLLOWERS", []),
        ("unlisted", "FOLLOWERS", "PUBLIC"),
        ("public", "PUBLIC", "FOLLOWERS"),
    ],
)
def test_publish_addresses_by_the_users_visibility(fed_ctx, user, visibility, to, cc):
    from scrobbler.federation.protocol.vocab import PUBLIC

    sharing.update(user.id, {"enabled": True, "visibility": visibility})
    post = publishing.publish(user.id, "alice", "weekly", "2026-W01", text="x", html="<p>x</p>")
    activity = db.session.get(FederationActivity, post.activity_id)
    expected_to = [PUBLIC] if to == "PUBLIC" else ["https://scrobble.test/users/alice/followers"]
    expected_cc = (
        []
        if cc == []
        else [PUBLIC]
        if cc == "PUBLIC"
        else ["https://scrobble.test/users/alice/followers"]
    )
    assert activity.document["to"] == expected_to
    assert activity.document["cc"] == expected_cc
    assert activity.document["object"]["to"] == expected_to  # the Note matches the Create
    assert activity.document["object"]["cc"] == expected_cc
    assert activity.public is (visibility != "followers")


def test_publish_fans_out_only_to_accepted_followers(fed_client, remote, sharing_user):
    bob = approved_follower(fed_client, remote, sharing_user, "bob")
    # A pending request: not yet let in, so it shouldn't receive posts
    sharing.update(sharing_user.id, {"manually_approves_followers": True})
    carol = RemoteAccount(remote, "carol")
    remote.routes["/users/carol/inbox"] = (202, {}, b"")
    carol.deliver(fed_client, carol.follow())
    while inbox.work_once():
        pass
    db.session.expire_all()
    assert follower_state.counts(sharing_user.id) == {"accepted": 1, "pending": 1}

    post = publishing.publish(sharing_user.id, "alice", "weekly", "2026-W01", text="x", html="x")
    deliveries = deliveries_for(post.activity_id)
    assert [d.inbox for d in deliveries] == [bob.server.url("/inbox")]


def test_publish_with_no_followers_still_creates_the_post(fed_ctx, user):
    sharing.update(user.id, {"enabled": True})
    post = publishing.publish(user.id, "alice", "weekly", "2026-W01", text="x", html="x")
    assert post is not None
    assert deliveries_for(post.activity_id) == []


def test_delete_post_sends_delete_and_tombstones(fed_client, remote, sharing_user, metric_delta):
    bob = approved_follower(fed_client, remote, sharing_user, "bob")
    post = publishing.publish(
        sharing_user.id, "alice", "weekly", "2026-W01", text="x", html="<p>x</p>"
    )
    note_id = db.session.get(FederationActivity, post.activity_id).document["object"]["id"]
    deleted = metric_delta("scrobbler_federation_post_deletions_total", reason="manual")
    activity = publishing.delete_post(sharing_user.id, "alice", post)
    db.session.commit()

    assert activity is not None
    assert activity.activity_type == "Delete"
    assert activity.document["object"] == {"id": note_id, "type": "Tombstone"}
    assert db.session.get(FederationPost, post.id).deleted_at is not None
    assert [d.inbox for d in deliveries_for(activity.id)] == [bob.server.url("/inbox")]
    assert deleted.delta == 1


def test_deleting_an_already_deleted_post_is_a_noop(fed_ctx, user):
    sharing.update(user.id, {"enabled": True})
    post = publishing.publish(user.id, "alice", "weekly", "2026-W01", text="x", html="x")
    first = publishing.delete_post(user.id, "alice", post)
    db.session.commit()
    second = publishing.delete_post(user.id, "alice", post)
    assert first is not None
    assert second is None
    assert len([a for a in all_activities() if a.activity_type == "Delete"]) == 1


def test_delete_uses_the_posts_own_visibility_not_the_current_setting(fed_ctx, user):
    from scrobbler.federation.protocol.vocab import PUBLIC

    sharing.update(user.id, {"enabled": True, "visibility": "public"})
    post = publishing.publish(user.id, "alice", "weekly", "2026-W01", text="x", html="x")
    sharing.update(user.id, {"visibility": "followers"})  # changed after posting
    activity = publishing.delete_post(user.id, "alice", post)
    assert activity.document["to"] == [PUBLIC]  # still addressed as it was posted


def test_delete_all_for_deletes_every_live_post(fed_ctx, user, metric_delta):
    sharing.update(user.id, {"enabled": True})
    a = publishing.publish(user.id, "alice", "weekly", "2026-W01", text="a", html="a")
    b = publishing.publish(user.id, "alice", "milestone", "scrobbles:1000", text="b", html="b")
    already_deleted = publishing.publish(user.id, "alice", "weekly", "2026-W02", text="c", html="c")
    publishing.delete_post(user.id, "alice", already_deleted)
    db.session.commit()

    sharing_off = metric_delta("scrobbler_federation_post_deletions_total", reason="sharing_off")
    count = publishing.delete_all_for(user.id, "alice")
    db.session.commit()

    assert count == 2  # not the one already deleted
    assert sharing_off.delta == 2
    assert db.session.get(FederationPost, a.id).deleted_at is not None
    assert db.session.get(FederationPost, b.id).deleted_at is not None
    # 3 Deletes total: one from the already_deleted post above, plus a and b just now
    assert len([act for act in all_activities() if act.activity_type == "Delete"]) == 3


def test_turning_sharing_off_deletes_posts_before_rejecting(fed_client, remote, sharing_user):
    bob = approved_follower(fed_client, remote, sharing_user, "bob")
    from scrobbler.services.accounts import issue_ui_token

    auth = {"Authorization": f"Bearer {issue_ui_token(sharing_user)}"}
    publishing.publish(sharing_user.id, "alice", "weekly", "2026-W01", text="x", html="x")
    db.session.commit()

    response = fed_client.patch(
        "/api/v1/federation/settings", json={"enabled": False}, headers=auth
    )
    assert response.status_code == 200
    types = [a.activity_type for a in all_activities()]
    assert types.count("Delete") == 1
    assert types.count("Reject") == 1
    assert types.index("Delete") < types.index("Reject")  # Delete queued before the Reject

    [post] = db.session.scalars(db.select(FederationPost)).all()
    assert post.deleted_at is not None
    delete_activity = next(a for a in all_activities() if a.activity_type == "Delete")
    assert [d.inbox for d in deliveries_for(delete_activity.id)] == [bob.server.url("/inbox")]
