"""Outbound HTTP safety (net.py) and remote actor fetching (remote.py)."""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from scrobbler.extensions import db
from scrobbler.federation import keys, remote
from scrobbler.federation.models import FederationRemoteActor
from scrobbler.federation.net import MAX_BYTES, Client, FetchError, is_public
from scrobbler.federation.protocol import signatures
from scrobbler.federation.protocol.signatures import SignedRequest

# --- Address checks ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # carrier-grade NAT
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fc00::1", False),
        ("fe80::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped loopback
        ("::ffff:8.8.8.8", True),
    ],
)
def test_is_public(address, public):
    assert is_public(address) is public


def test_http_is_refused_outside_testing():
    client = Client("test", resolver=lambda host, port: ["93.184.215.14"])
    with pytest.raises(FetchError) as err:
        client.request("GET", "http://example.com/users/bob")
    assert err.value.reason == "blocked"


@pytest.mark.parametrize("addresses", [["10.0.0.5"], ["93.184.215.14", "127.0.0.1"], ["::1"]])
def test_names_resolving_to_private_addresses_are_refused(addresses):
    """Even one private address among several is enough to refuse (no retry elsewhere)."""
    client = Client("test", resolver=lambda host, port: addresses)
    with pytest.raises(FetchError) as err:
        client.request("GET", "https://sneaky.example/users/bob")
    assert err.value.reason == "blocked"


def test_urls_with_credentials_are_refused():
    with pytest.raises(FetchError):
        Client("test").request("GET", "https://user:pass@example.com/")


# --- Against a local server (insecure testing allows 127.0.0.1 over http) ---------------------


@pytest.fixture
def local_client():
    client = Client("Scrobbler/test", insecure_testing=True)
    yield client
    client.close()


def test_get_json_and_user_agent(local_client, remote):
    remote.routes["/doc"] = (200, {}, {"id": "x"})
    assert local_client.get_json(remote.url("/doc")) == {"id": "x"}
    assert remote.requests[0]["headers"]["User-Agent"] == "Scrobbler/test"
    assert "application/activity+json" in remote.requests[0]["headers"]["Accept"]


def test_redirects_are_followed_for_get_up_to_a_limit(local_client, remote):
    remote.routes["/a"] = (302, {"Location": "/b"}, b"")
    remote.routes["/b"] = (301, {"Location": remote.url("/c")}, b"")
    remote.routes["/c"] = (200, {}, {"ok": True})
    assert local_client.get_json(remote.url("/a")) == {"ok": True}

    remote.routes["/loop"] = (302, {"Location": "/loop"}, b"")
    with pytest.raises(FetchError) as err:
        local_client.request("GET", remote.url("/loop"))
    assert err.value.reason == "blocked"


def test_posts_do_not_follow_redirects(local_client, remote):
    remote.routes["/inbox"] = (307, {"Location": "/elsewhere"}, b"")
    assert local_client.request("POST", remote.url("/inbox"), body=b"{}").status == 307
    assert [r["path"] for r in remote.requests] == ["/inbox"]


def test_redirect_to_a_refused_url_is_refused():
    client = Client("test", insecure_testing=False, resolver=lambda host, port: ["93.184.215.14"])
    with pytest.raises(FetchError):
        client._check_url("http://internal.example/")


def test_oversized_responses_are_cut_off(local_client, remote):
    remote.routes["/big"] = (200, {}, b"x" * (MAX_BYTES + 1))
    with pytest.raises(FetchError) as err:
        local_client.request("GET", remote.url("/big"))
    assert err.value.reason == "too_large"


def test_http_errors_and_bad_json(local_client, remote):
    remote.routes["/gone"] = (410, {}, b"")
    with pytest.raises(FetchError) as err:
        local_client.get_json(remote.url("/gone"))
    assert (err.value.reason, err.value.status) == ("http", 410)
    remote.routes["/html"] = (200, {"Content-Type": "text/html"}, b"<html>")
    with pytest.raises(FetchError) as err:
        local_client.get_json(remote.url("/html"))
    assert err.value.reason == "invalid"


# --- Remote actors ---------------------------------------------------------------------------


def make_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, pem


def actor_doc(remote, name="bob", pem=None, **overrides):
    uri = remote.url(f"/users/{name}")
    doc = {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": uri,
        "type": "Person",
        "preferredUsername": name,
        "name": name.title(),
        "inbox": f"{uri}/inbox",
        "endpoints": {"sharedInbox": remote.url("/inbox")},
        "publicKey": {"id": f"{uri}#main-key", "owner": uri, "publicKeyPem": pem or make_key()[1]},
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def fed_remote(fed_ctx, remote):
    return remote


def test_actor_is_fetched_signed_validated_and_cached(fed_remote):
    fed_remote.routes["/users/bob"] = (200, {}, actor_doc(fed_remote))
    actor = remote.get_actor(fed_remote.url("/users/bob"))
    assert (actor.username, actor.display_name, actor.host) == ("bob", "Bob", "127.0.0.1")
    assert actor.handle == "@bob@127.0.0.1"
    assert actor.delivery_inbox == fed_remote.url("/inbox")

    # The fetch was signed with our instance actor's key, verifiably
    sent = fed_remote.requests[0]
    request = SignedRequest("GET", fed_remote.url("/users/bob"), sent["headers"], b"")
    instance_public = serialization.load_pem_public_key(keys.key_for(None).public_key_pem.encode())
    verified = signatures.verify(request, lambda key_id: instance_public)
    assert verified.key_id == "https://scrobble.test/actor#main-key"

    # Cached: a second lookup doesn't fetch again
    remote.get_actor(fed_remote.url("/users/bob"))
    assert len(fed_remote.requests) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "https://elsewhere.example/users/bob"},
        {"type": "Note"},
        {"inbox": "https://elsewhere.example/inbox"},
        {
            "publicKey": {
                "id": "x#main-key",
                "owner": "https://elsewhere.example/users/eve",
                "publicKeyPem": "x",
            }
        },
        {"publicKey": None},
    ],
)
def test_invalid_actors_are_refused(fed_remote, overrides):
    fed_remote.routes["/users/bob"] = (200, {}, actor_doc(fed_remote, **overrides))
    with pytest.raises(remote.ActorError):
        remote.get_actor(fed_remote.url("/users/bob"))


def test_unreadable_key_is_refused(fed_remote):
    uri = fed_remote.url("/users/bob")
    doc = actor_doc(fed_remote)
    doc["publicKey"]["publicKeyPem"] = "not a key"
    fed_remote.routes["/users/bob"] = (200, {}, doc)
    with pytest.raises(remote.ActorError):
        remote.get_actor(uri)


def test_refresh_picks_up_a_rotated_key(fed_remote):
    uri = fed_remote.url("/users/bob")
    fed_remote.routes["/users/bob"] = (200, {}, actor_doc(fed_remote, pem=make_key()[1]))
    first = remote.get_actor(uri).public_key_pem
    new_pem = make_key()[1]
    fed_remote.routes["/users/bob"] = (200, {}, actor_doc(fed_remote, pem=new_pem))
    assert remote.get_actor(uri).public_key_pem == first  # cached
    assert remote.get_actor(uri, refresh=True).public_key_pem == new_pem


def test_stale_cache_is_refetched(fed_remote):
    uri = fed_remote.url("/users/bob")
    fed_remote.routes["/users/bob"] = (200, {}, actor_doc(fed_remote))
    actor = remote.get_actor(uri)
    actor.fetched_at = datetime.now(UTC) - timedelta(hours=25)
    db.session.commit()
    remote.get_actor(uri)
    assert len(fed_remote.requests) == 2


def test_actor_for_key(fed_remote):
    fed_remote.routes["/users/bob"] = (200, {}, actor_doc(fed_remote))
    actor = remote.actor_for_key(fed_remote.url("/users/bob#main-key"))
    assert actor.username == "bob"
    with pytest.raises(remote.ActorError):
        remote.actor_for_key(fed_remote.url("/users/bob#other-key"))


def test_actor_for_a_standalone_key_document(fed_remote):
    uri = fed_remote.url("/users/carol")
    doc = actor_doc(fed_remote, name="carol")
    key_id = fed_remote.url("/keys/carol")
    doc["publicKey"]["id"] = key_id
    fed_remote.routes["/users/carol"] = (200, {}, doc)
    fed_remote.routes["/keys/carol"] = (
        200,
        {},
        {"id": key_id, "owner": uri, "publicKeyPem": doc["publicKey"]["publicKeyPem"]},
    )
    assert remote.actor_for_key(key_id).uri == uri


def test_gone_actors_are_marked(fed_remote):
    uri = fed_remote.url("/users/bob")
    fed_remote.routes["/users/bob"] = (200, {}, actor_doc(fed_remote))
    remote.get_actor(uri)
    fed_remote.routes["/users/bob"] = (410, {}, b"")
    with pytest.raises(FetchError):
        remote.get_actor(uri, refresh=True)
    assert db.session.scalar(db.select(FederationRemoteActor).filter_by(uri=uri)).gone is True


def test_actor_for_a_key_url_that_serves_the_actor(fed_remote):
    """GoToSocial: the key id is <actor>/main-key, and fetching it returns the actor."""
    uri = fed_remote.url("/users/dana")
    doc = actor_doc(fed_remote, name="dana")
    key_id = f"{uri}/main-key"
    doc["publicKey"]["id"] = key_id
    fed_remote.routes["/users/dana"] = (200, {}, doc)
    fed_remote.routes["/users/dana/main-key"] = (200, {}, doc)  # same document
    actor = remote.actor_for_key(key_id)
    assert (actor.uri, actor.public_key_id) == (uri, key_id)


def test_key_owner_on_another_host_is_refused(fed_remote):
    key_id = fed_remote.url("/keys/eve")
    fed_remote.routes["/keys/eve"] = (
        200,
        {},
        {"id": key_id, "owner": "https://elsewhere.example/users/eve", "publicKeyPem": "x"},
    )
    with pytest.raises(remote.ActorError):
        remote.actor_for_key(key_id)
