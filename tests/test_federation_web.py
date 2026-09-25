import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import pytest

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
    assert doc["manuallyApprovesFollowers"] is True  # follows are requests until phase 2
    assert (doc["discoverable"], doc["indexable"]) == (False, False)
    assert doc["url"] == "http://localhost:8080/profile.html?u=alice"


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
    assert (outbox["type"], outbox["totalItems"], outbox["orderedItems"]) == (
        "OrderedCollection",
        0,
        [],
    )
    followers = fed_client.get("/users/alice/followers", headers=AP).get_json()
    assert followers["totalItems"] == 0 and "orderedItems" not in followers


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


def test_inboxes_accept_and_count_deliveries(fed_client, sharing_user, metric_delta):
    shared = metric_delta("scrobbler_federation_inbox_requests_total", inbox="shared")
    body = b'{"type": "Follow"}'
    assert (
        fed_client.post("/inbox", data=body, content_type="application/activity+json").status_code
        == 202
    )
    assert (
        fed_client.post(
            "/users/alice/inbox", data=body, content_type="application/activity+json"
        ).status_code
        == 202
    )
    assert fed_client.post("/users/nobody/inbox", data=body).status_code == 404
    too_big = fed_client.post("/inbox", data=b"x" * (256 * 1024 + 1))
    assert too_big.status_code == 413
    assert shared.delta == 1


def test_disabled_app_has_none_of_these_routes(client):
    for path in (
        "/.well-known/webfinger?resource=acct:alice@scrobble.test",
        "/users/alice",
        "/nodeinfo/2.1",
    ):
        assert client.get(path, headers=AP).status_code == 404
