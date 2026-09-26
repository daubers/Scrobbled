from datetime import UTC, datetime, timedelta

import pytest

from scrobbler.extensions import db
from scrobbler.federation import publishing, sharing
from scrobbler.federation.models import FederationPost
from scrobbler.models import Scrobble


@pytest.fixture
def fed_auth(fed_ctx, user):
    from scrobbler.services.accounts import issue_ui_token

    return {"Authorization": f"Bearer {issue_ui_token(user)}"}


def test_settings_start_private(fed_client, fed_auth):
    body = fed_client.get("/api/v1/federation/settings", headers=fed_auth).get_json()
    assert body["enabled"] is False
    assert body["visibility"] == "followers"
    assert (body["discoverable"], body["indexable"], body["manually_approves_followers"]) == (
        False,
        False,
        False,
    )
    assert (body["timezone"], body["post_weekly_summary"], body["post_milestones"]) == (
        "UTC",
        True,
        True,
    )
    assert body["handle"] == "@alice@scrobble.test"
    assert body["actor_url"] == "https://scrobble.test/users/alice"
    assert body["profile_url"] == "http://localhost:8080/profile.html?u=alice"


def test_timezone_and_post_toggles_can_be_changed(fed_client, fed_auth):
    response = fed_client.patch(
        "/api/v1/federation/settings",
        json={"timezone": "Europe/London", "post_weekly_summary": False},
        headers=fed_auth,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert (body["timezone"], body["post_weekly_summary"], body["post_milestones"]) == (
        "Europe/London",
        False,
        True,
    )


def test_an_unknown_timezone_is_rejected(fed_client, fed_auth):
    response = fed_client.patch(
        "/api/v1/federation/settings", json={"timezone": "Mars/Phobos"}, headers=fed_auth
    )
    assert response.status_code == 422


def test_enable_and_change_settings(fed_client, fed_auth, metric_delta):
    enabled = metric_delta("scrobbler_federation_sharing_changes_total", change="enabled")
    response = fed_client.patch(
        "/api/v1/federation/settings",
        json={"enabled": True, "visibility": "unlisted", "display_name": "Alice", "bio": "Hi"},
        headers=fed_auth,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert (body["enabled"], body["visibility"], body["display_name"]) == (
        True,
        "unlisted",
        "Alice",
    )
    assert enabled.delta == 1
    # Now findable
    doc = fed_client.get("/users/alice", headers={"Accept": "application/activity+json"}).get_json()
    assert doc["name"] == "Alice"
    # A partial update leaves the rest alone
    body = fed_client.patch(
        "/api/v1/federation/settings", json={"bio": None}, headers=fed_auth
    ).get_json()
    assert (body["visibility"], body["bio"]) == ("unlisted", None)


@pytest.mark.parametrize(
    "changes",
    [
        {"visibility": "everyone"},
        {"handle": "@x@y"},
        {"display_name": "x" * 101},
        {"enabled": "maybe"},
    ],
)
def test_invalid_settings_are_rejected(fed_client, fed_auth, changes):
    response = fed_client.patch("/api/v1/federation/settings", json=changes, headers=fed_auth)
    assert response.status_code == 422


def test_settings_need_auth(fed_client):
    assert fed_client.get("/api/v1/federation/settings").status_code == 401


def test_public_profile(fed_client, sharing_user, make_user):
    body = fed_client.get("/api/v1/federation/profiles/Alice").get_json()
    assert body == {
        "username": "alice",
        "display_name": "Alice",
        "bio": "Listening.",
        "handle": "@alice@scrobble.test",
        "actor_url": "https://scrobble.test/users/alice",
    }
    make_user(username="bob")
    assert fed_client.get("/api/v1/federation/profiles/bob").status_code == 404


def test_disabled_server_has_no_federation_api(client, auth):
    assert client.get("/api/v1/federation/settings", headers=auth).status_code == 404


def test_public_post_serves_a_public_or_unlisted_note(fed_client, fed_ctx, sharing_user):
    sharing.update(sharing_user.id, {"visibility": "unlisted"})
    post = publishing.publish(sharing_user.id, "alice", "weekly", "a", text="t", html="<p>t</p>")
    note_uuid = post.activity.document["object"]["id"].rsplit("/", 1)[-1]

    body = fed_client.get(f"/api/v1/federation/profiles/alice/posts/{note_uuid}").get_json()
    assert body == {
        "text": "t",
        "html": "<p>t</p>",
        "created_at": body["created_at"],
        "display_name": "Alice",
        "handle": "@alice@scrobble.test",
        "profile_url": "http://localhost:8080/profile.html?u=alice",
    }


def test_public_post_404s_for_followers_only_deleted_or_unknown(fed_client, fed_ctx, sharing_user):
    post = publishing.publish(sharing_user.id, "alice", "weekly", "a", text="t", html="<p>t</p>")
    note_uuid = post.activity.document["object"]["id"].rsplit("/", 1)[-1]
    # followers-only (the default)
    assert fed_client.get(f"/api/v1/federation/profiles/alice/posts/{note_uuid}").status_code == 404

    sharing.update(post.user_id, {"visibility": "public"})
    publishing.delete_post(post.user_id, "alice", post)
    db.session.commit()
    assert fed_client.get(f"/api/v1/federation/profiles/alice/posts/{note_uuid}").status_code == 404

    assert fed_client.get("/api/v1/federation/profiles/alice/posts/not-a-uuid").status_code == 404
    assert fed_client.get("/api/v1/federation/profiles/nobody/posts/not-a-uuid").status_code == 404


# --- Posts and preview ------------------------------------------------------------------


def test_preview_weekly_reflects_the_week_in_progress(fed_client, fed_auth, fed_ctx, user):
    now = datetime.now(UTC)
    monday = now.date() - timedelta(days=now.weekday())
    db.session.add(
        Scrobble(
            user_id=user.id,
            artist="Radiohead",
            track="Reckoner",
            played_at=datetime(monday.year, monday.month, monday.day, 12, tzinfo=UTC),
        )
    )
    db.session.commit()

    response = fed_client.get("/api/v1/federation/preview/weekly", headers=fed_auth)
    assert response.status_code == 200
    body = response.get_json()
    assert "1 play" in body["text"]
    assert "<p>" in body["html"]
    # Never stored or posted
    assert db.session.scalar(db.select(db.func.count()).select_from(FederationPost)) == 0


def test_preview_weekly_needs_auth(fed_client):
    assert fed_client.get("/api/v1/federation/preview/weekly").status_code == 401


def test_list_posts_newest_first_including_deleted(fed_client, fed_auth, fed_ctx, user):
    sharing.update(user.id, {"enabled": True, "visibility": "public"})
    first = publishing.publish(user.id, "alice", "weekly", "a", text="first", html="<p>1</p>")
    second = publishing.publish(user.id, "alice", "weekly", "b", text="second", html="<p>2</p>")
    publishing.delete_post(user.id, "alice", second)
    db.session.commit()

    body = fed_client.get("/api/v1/federation/posts", headers=fed_auth).get_json()
    assert [p["id"] for p in body] == [second.id, first.id]
    assert body[0]["text"] == "second"
    assert body[0]["deleted_at"] is not None
    assert body[1]["deleted_at"] is None


def test_list_posts_needs_auth(fed_client):
    assert fed_client.get("/api/v1/federation/posts").status_code == 401


def test_delete_post(fed_client, fed_auth, fed_ctx, user):
    sharing.update(user.id, {"enabled": True, "visibility": "public"})
    post = publishing.publish(user.id, "alice", "weekly", "a", text="t", html="<p>t</p>")
    db.session.commit()

    response = fed_client.delete(f"/api/v1/federation/posts/{post.id}", headers=fed_auth)
    assert response.status_code == 204
    assert db.session.get(FederationPost, post.id).deleted_at is not None


def test_deleting_an_already_deleted_post_conflicts(fed_client, fed_auth, fed_ctx, user):
    sharing.update(user.id, {"enabled": True, "visibility": "public"})
    post = publishing.publish(user.id, "alice", "weekly", "a", text="t", html="<p>t</p>")
    db.session.commit()
    fed_client.delete(f"/api/v1/federation/posts/{post.id}", headers=fed_auth)

    response = fed_client.delete(f"/api/v1/federation/posts/{post.id}", headers=fed_auth)
    assert response.status_code == 409


def test_deleting_someone_elses_post_is_not_found(fed_client, fed_auth, fed_ctx, user, make_user):
    other = make_user(username="bob")
    sharing.update(other.id, {"enabled": True, "visibility": "public"})
    post = publishing.publish(other.id, "bob", "weekly", "a", text="t", html="<p>t</p>")
    db.session.commit()

    response = fed_client.delete(f"/api/v1/federation/posts/{post.id}", headers=fed_auth)
    assert response.status_code == 404


def test_deleting_an_unknown_post_is_not_found(fed_client, fed_auth):
    assert fed_client.delete("/api/v1/federation/posts/999999", headers=fed_auth).status_code == 404
