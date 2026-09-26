import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import pytest

from scrobbler.extensions import db
from scrobbler.federation import publishing, sharing

AP = {"Accept": "application/activity+json"}
BASE = "https://scrobble.test"


def webfinger(client, resource):
    return client.get("/.well-known/webfinger", query_string={"resource": resource})


def test_webfinger_finds_sharing_users(fed_client, sharing_user, metric_delta):
    found = metric_delta("scrobbler_federation_lookups_total", kind="webfinger", result="found")
    response = webfinger(fed_client, "acct:ALICE@scrobble.test")
    assert response.status_code == 200
    assert response.content_type == "application/jrd+json"
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    doc = response.get_json()
    assert doc["subject"] == "acct:alice@scrobble.test"
    assert {
        "rel": "self",
        "type": "application/activity+json",
        "href": f"{BASE}/users/alice",
    } in doc["links"]
    assert found.delta == 1


def test_unknown_and_private_users_look_identical(fed_client, fed_ctx, make_user):
    make_user(username="bob")  # exists, never enabled sharing
    unknown = webfinger(fed_client, "acct:nobody@scrobble.test")
    private = webfinger(fed_client, "acct:bob@scrobble.test")
    assert unknown.status_code == private.status_code == 404
    assert unknown.data == private.data
    ap_unknown = fed_client.get("/users/nobody", headers=AP)
    ap_private = fed_client.get("/users/bob", headers=AP)
    assert ap_unknown.status_code == ap_private.status_code == 404
    assert ap_unknown.data == ap_private.data


def test_webfinger_rejects_other_domains(fed_client, sharing_user):
    assert webfinger(fed_client, "acct:alice@elsewhere.example").status_code == 400
    assert fed_client.get("/.well-known/webfinger").status_code == 400


def test_webfinger_instance_actor(fed_client, fed_ctx):
    doc = webfinger(fed_client, "acct:scrobble.test@scrobble.test").get_json()
    assert doc["links"][0]["href"] == f"{BASE}/actor"


def test_actor_document(fed_client, sharing_user):
    response = fed_client.get("/users/alice", headers=AP)
    assert response.status_code == 200
    assert response.content_type == "application/activity+json"
    assert response.headers["Vary"] == "Accept"
    doc = response.get_json()
    assert doc["id"] == f"{BASE}/users/alice"
    assert (doc["type"], doc["preferredUsername"], doc["name"]) == ("Person", "alice", "Alice")
    assert doc["summary"] == "<p>Listening.</p>"
    assert doc["publicKey"]["id"] == f"{BASE}/users/alice#main-key"
    assert doc["publicKey"]["publicKeyPem"].startswith("-----BEGIN PUBLIC KEY-----")
    assert doc["manuallyApprovesFollowers"] is False  # the user's setting (off by default)
    assert (doc["discoverable"], doc["indexable"]) == (False, False)
    assert doc["url"] == "http://localhost:8080/profile.html?u=alice"
    assert doc["attachment"] == []  # nothing playing


def test_actor_document_shows_now_playing_live(fed_client, fed_ctx, sharing_user):
    from scrobbler.services.scrobbles import TrackInput, update_now_playing

    update_now_playing(sharing_user, TrackInput(artist="Radiohead", track="Reckoner"))
    doc = fed_client.get("/users/alice", headers=AP).get_json()
    assert doc["attachment"] == [
        {"type": "PropertyValue", "name": "Now playing", "value": "Reckoner by Radiohead"}
    ]


def test_ids_come_from_config_not_the_request(fed_client, sharing_user):
    doc = fed_client.get("/users/alice", headers={**AP, "Host": "evil.example"}).get_json()
    assert urlsplit(doc["id"]).netloc == "scrobble.test"


@pytest.mark.parametrize("username", ["alice", "nobody"])
def test_browsers_are_sent_to_the_profile_page(fed_client, sharing_user, username):
    response = fed_client.get(f"/users/{username}", headers={"Accept": "text/html"})
    assert response.status_code == 302
    assert response.headers["Location"] == f"http://localhost:8080/profile.html?u={username}"


def test_collections(fed_client, sharing_user):
    outbox = fed_client.get("/users/alice/outbox", headers=AP).get_json()
    assert (outbox["type"], outbox["totalItems"], outbox["first"]) == (
        "OrderedCollection",
        0,
        f"{BASE}/users/alice/outbox?page=1",
    )
    assert "orderedItems" not in outbox  # paged: points at the first page instead
    followers = fed_client.get("/users/alice/followers", headers=AP).get_json()
    assert followers["totalItems"] == 0 and "orderedItems" not in followers


def test_outbox_paging_lists_public_and_unlisted_but_not_followers_only(
    fed_client, fed_ctx, sharing_user
):
    sharing.update(sharing_user.id, {"visibility": "public"})
    publishing.publish(sharing_user.id, "alice", "weekly", "a", text="first", html="<p>first</p>")
    sharing.update(sharing_user.id, {"visibility": "unlisted"})
    publishing.publish(sharing_user.id, "alice", "weekly", "b", text="second", html="<p>second</p>")
    sharing.update(sharing_user.id, {"visibility": "followers"})
    publishing.publish(sharing_user.id, "alice", "weekly", "c", text="third", html="<p>third</p>")

    root = fed_client.get("/users/alice/outbox", headers=AP).get_json()
    assert root["totalItems"] == 2  # the followers-only one isn't counted here

    page = fed_client.get("/users/alice/outbox?page=1", headers=AP).get_json()
    assert page["type"] == "OrderedCollectionPage"
    assert page["partOf"] == f"{BASE}/users/alice/outbox"
    # Newest first: "second" (unlisted) was published after "first" (public).
    assert [item["object"]["content"] for item in page["orderedItems"]] == [
        "<p>second</p>",
        "<p>first</p>",
    ]
    assert "next" not in page
    assert "prev" not in page


def test_outbox_paging_links(fed_client, fed_ctx, sharing_user, monkeypatch):
    monkeypatch.setattr(publishing, "OUTBOX_PAGE_SIZE", 1)
    sharing.update(sharing_user.id, {"visibility": "public"})
    for key in ("a", "b"):
        publishing.publish(sharing_user.id, "alice", "weekly", key, text="t", html="<p>t</p>")

    first = fed_client.get("/users/alice/outbox?page=1", headers=AP).get_json()
    assert len(first["orderedItems"]) == 1
    assert first["next"] == f"{BASE}/users/alice/outbox?page=2"
    assert "prev" not in first

    second = fed_client.get("/users/alice/outbox?page=2", headers=AP).get_json()
    assert len(second["orderedItems"]) == 1
    assert "next" not in second
    assert second["prev"] == f"{BASE}/users/alice/outbox?page=1"


def test_outbox_missing_user_is_not_found(fed_client, fed_ctx):
    assert fed_client.get("/users/nobody/outbox", headers=AP).status_code == 404
    assert fed_client.get("/users/nobody/outbox?page=1", headers=AP).status_code == 404


def test_post_note_serves_a_public_note(fed_client, fed_ctx, sharing_user):
    sharing.update(sharing_user.id, {"visibility": "public"})
    post = publishing.publish(sharing_user.id, "alice", "weekly", "a", text="t", html="<p>t</p>")

    note_uuid = post.activity.document["object"]["id"].rsplit("/", 1)[-1]
    response = fed_client.get(f"/users/alice/posts/{note_uuid}", headers=AP)
    assert response.status_code == 200
    doc = response.get_json()
    assert (doc["type"], doc["content"]) == ("Note", "<p>t</p>")


def test_post_note_404s_for_followers_only_deleted_or_unknown(fed_client, fed_ctx, sharing_user):
    post = publishing.publish(sharing_user.id, "alice", "weekly", "a", text="t", html="<p>t</p>")
    note_uuid = post.activity.document["object"]["id"].rsplit("/", 1)[-1]
    # followers-only (the default): not listed to an unauthenticated fetch
    assert fed_client.get(f"/users/alice/posts/{note_uuid}", headers=AP).status_code == 404

    sharing.update(post.user_id, {"visibility": "public"})
    publishing.delete_post(post.user_id, "alice", post)
    db.session.commit()
    assert fed_client.get(f"/users/alice/posts/{note_uuid}", headers=AP).status_code == 404

    assert fed_client.get("/users/alice/posts/not-a-real-uuid", headers=AP).status_code == 404
    assert fed_client.get("/users/nobody/posts/not-a-real-uuid", headers=AP).status_code == 404


def test_post_note_browser_is_redirected_to_the_ui_page(fed_client, fed_ctx, sharing_user):
    post = publishing.publish(sharing_user.id, "alice", "weekly", "a", text="t", html="<p>t</p>")
    note_uuid = post.activity.document["object"]["id"].rsplit("/", 1)[-1]
    response = fed_client.get(f"/users/alice/posts/{note_uuid}", headers={"Accept": "text/html"})
    assert response.status_code == 302
    assert response.headers["Location"] == f"http://localhost:8080/post.html?u=alice&id={note_uuid}"


def test_nodeinfo(fed_client, sharing_user):
    link = fed_client.get("/.well-known/nodeinfo").get_json()["links"][0]
    assert link["href"] == f"{BASE}/nodeinfo/2.1"
    doc = fed_client.get("/nodeinfo/2.1").get_json()
    assert doc["software"]["name"] == "scrobbler"
    assert doc["usage"]["users"]["total"] == 1


def test_host_meta(fed_client):
    root = ET.fromstring(fed_client.get("/.well-known/host-meta").data)
    assert "webfinger" in root[0].get("template")


def test_instance_actor(fed_client, fed_ctx):
    doc = fed_client.get("/actor", headers=AP).get_json()
    assert (doc["type"], doc["id"]) == ("Application", f"{BASE}/actor")


def test_disabled_app_has_none_of_these_routes(client):
    for path in (
        "/.well-known/webfinger?resource=acct:alice@scrobble.test",
        "/users/alice",
        "/nodeinfo/2.1",
    ):
        assert client.get(path, headers=AP).status_code == 404
