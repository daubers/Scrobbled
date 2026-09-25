import pytest


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
    assert body["handle"] == "@alice@scrobble.test"
    assert body["actor_url"] == "https://scrobble.test/users/alice"
    assert body["profile_url"] == "http://localhost:8080/profile.html?u=alice"


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
