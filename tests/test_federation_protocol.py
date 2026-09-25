import xml.etree.ElementTree as ET

import pytest

from scrobbler.federation.protocol import hostmeta, media, nodeinfo, vocab, webfinger

BASE = "https://scrobble.test"


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        ("application/activity+json", True),
        ('application/ld+json; profile="https://www.w3.org/ns/activitystreams"', True),
        ("application/ld+json", True),
        ('application/ld+json; profile="https://example.com/other"', False),
        ("text/html,application/xhtml+xml,*/*;q=0.8", False),
        ("application/activity+json;q=0", False),
        ("text/html, application/activity+json;q=0.5", True),
        ("", False),
        (None, False),
    ],
)
def test_accept_negotiation(accept, expected):
    assert media.wants_activitypub(accept) is expected


@pytest.mark.parametrize(
    ("resource", "username"),
    [
        ("acct:alice@scrobble.test", "alice"),
        ("acct:Alice@SCROBBLE.TEST", "Alice"),
        ("alice@scrobble.test", "alice"),
        ("@alice@scrobble.test", "alice"),
        ("  acct:alice@scrobble.test ", "alice"),
        ("https://scrobble.test/users/alice", "alice"),
    ],
)
def test_webfinger_resources(resource, username):
    assert webfinger.parse_resource(resource, domain="scrobble.test", base_url=BASE) == username


@pytest.mark.parametrize(
    "resource",
    [
        "acct:alice@elsewhere.example",
        "acct:alice",
        "",
        None,
        "acct:al ice@scrobble.test",
        "https://elsewhere.example/users/alice",
        "https://scrobble.test/users/alice/../../admin",
    ],
)
def test_webfinger_rejects_other_resources(resource):
    with pytest.raises(webfinger.ResourceError):
        webfinger.parse_resource(resource, domain="scrobble.test", base_url=BASE)


def test_jrd_shape():
    doc = webfinger.jrd(
        username="alice",
        domain="scrobble.test",
        actor_id=f"{BASE}/users/alice",
        profile_page=f"{BASE}/profile.html?u=alice",
    )
    assert doc["subject"] == "acct:alice@scrobble.test"
    [self_link] = [link for link in doc["links"] if link["rel"] == "self"]
    assert self_link == {
        "rel": "self",
        "type": "application/activity+json",
        "href": f"{BASE}/users/alice",
    }


def test_person_document():
    urls = vocab.actor_urls(BASE, "alice", f"{BASE}/profile.html?u=alice")
    doc = vocab.person(
        urls,
        username="alice",
        name="Alice",
        summary="Listens a lot.\n\n<script>x</script>",
        public_key_pem="PEM",
        manually_approves_followers=True,
        discoverable=False,
        indexable=False,
        fields=[("Scrobbles", "1,234 & counting")],
    )
    assert doc["id"] == f"{BASE}/users/alice"
    assert doc["type"] == "Person"
    assert doc["inbox"] == f"{BASE}/users/alice/inbox"
    assert doc["endpoints"] == {"sharedInbox": f"{BASE}/inbox"}
    assert doc["publicKey"] == {
        "id": f"{BASE}/users/alice#main-key",
        "owner": doc["id"],
        "publicKeyPem": "PEM",
    }
    assert doc["summary"] == "<p>Listens a lot.</p><p>&lt;script&gt;x&lt;/script&gt;</p>"
    assert doc["attachment"] == [
        {"type": "PropertyValue", "name": "Scrobbles", "value": "1,234 &amp; counting"}
    ]
    assert (doc["manuallyApprovesFollowers"], doc["discoverable"], doc["indexable"]) == (
        True,
        False,
        False,
    )
    assert vocab.AS_CONTEXT in doc["@context"] and vocab.SECURITY_CONTEXT in doc["@context"]


def test_collections():
    assert vocab.ordered_collection("x", 3) == {
        "@context": vocab.AS_CONTEXT,
        "id": "x",
        "type": "OrderedCollection",
        "totalItems": 3,
    }
    assert vocab.ordered_collection("x", 0, items=[])["orderedItems"] == []


def test_nodeinfo():
    assert nodeinfo.discovery(BASE)["links"][0]["href"] == f"{BASE}/nodeinfo/2.1"
    doc = nodeinfo.document(version="0.2.0", users_total=3, open_registrations=True)
    assert doc["software"] == {"name": "scrobbler", "version": "0.2.0"}
    assert doc["protocols"] == ["activitypub"]
    assert doc["usage"]["users"]["total"] == 3


def test_host_meta_points_at_webfinger():
    link = ET.fromstring(hostmeta.xrd(BASE)).find("{http://docs.oasis-open.org/ns/xri/xrd-1.0}Link")
    assert link.get("template") == f"{BASE}/.well-known/webfinger?resource={{uri}}"
