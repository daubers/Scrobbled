import xml.etree.ElementTree as ET

import pytest

from scrobbler.federation.protocol import addressing, hostmeta, media, nodeinfo, vocab, webfinger

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
    assert vocab.ordered_collection("x", 5, first="x?page=1")["first"] == "x?page=1"
    assert "orderedItems" not in vocab.ordered_collection("x", 5, first="x?page=1")


def test_ordered_collection_page():
    page = vocab.ordered_collection_page(
        f"{BASE}/users/alice/outbox?page=2",
        part_of=f"{BASE}/users/alice/outbox",
        items=["a", "b"],
        next_page=f"{BASE}/users/alice/outbox?page=3",
    )
    assert page["type"] == "OrderedCollectionPage"
    assert page["partOf"] == f"{BASE}/users/alice/outbox"
    assert page["orderedItems"] == ["a", "b"]
    assert page["next"] == f"{BASE}/users/alice/outbox?page=3"
    assert "prev" not in page

    with_prev = vocab.ordered_collection_page("x", part_of="y", items=[], prev_page="z")
    assert with_prev["prev"] == "z" and "next" not in with_prev


@pytest.mark.parametrize(
    ("visibility", "to", "cc"),
    [
        ("followers", ["FOLLOWERS"], []),
        ("unlisted", ["FOLLOWERS"], [vocab.PUBLIC]),
        ("public", [vocab.PUBLIC], ["FOLLOWERS"]),
    ],
)
def test_addressing(visibility, to, cc):
    assert addressing.address(visibility, "FOLLOWERS") == (to, cc)


def test_addressing_rejects_unknown_visibility():
    with pytest.raises(ValueError, match="visibility"):
        addressing.address("everyone", "FOLLOWERS")


def test_is_public_or_unlisted():
    assert addressing.is_public_or_unlisted("public") is True
    assert addressing.is_public_or_unlisted("unlisted") is True
    assert addressing.is_public_or_unlisted("followers") is False


def test_hashtag():
    assert vocab.hashtag("Scrobbler") == {"type": "Hashtag", "name": "#Scrobbler"}


def test_note():
    doc = vocab.note(
        f"{BASE}/users/alice/posts/1",
        actor_id=f"{BASE}/users/alice",
        content_html="<p>Hello</p>",
        published="2026-01-05T09:00:00Z",
        url=f"{BASE}/post.html?id=1",
        to=["FOLLOWERS"],
        cc=[],
        tags=["Scrobbler"],
    )
    assert doc["type"] == "Note"
    assert doc["attributedTo"] == f"{BASE}/users/alice"
    assert doc["content"] == "<p>Hello</p>"
    assert doc["contentMap"] == {"en": "<p>Hello</p>"}
    assert (doc["to"], doc["cc"]) == (["FOLLOWERS"], [])
    assert doc["tag"] == [{"type": "Hashtag", "name": "#Scrobbler"}]
    assert doc["sensitive"] is False
    assert "@context" not in doc  # carried by the wrapping Create instead
    assert "attachment" not in doc  # omitted entirely, not an empty list, when there's none


def test_note_with_attachment():
    image = {
        "type": "Image",
        "mediaType": "image/jpeg",
        "url": f"{BASE}/art.jpg",
        "name": "Cover art",
    }
    doc = vocab.note(
        f"{BASE}/users/alice/posts/1",
        actor_id=f"{BASE}/users/alice",
        content_html="<p>Hello</p>",
        published="2026-01-05T09:00:00Z",
        url=f"{BASE}/post.html?id=1",
        to=["FOLLOWERS"],
        cc=[],
        attachment=[image],
    )
    assert doc["attachment"] == [image]


def test_create_wraps_a_note_with_the_same_addressing():
    inner = vocab.note(
        "note-id",
        actor_id="actor",
        content_html="<p>x</p>",
        published="2026-01-05T00:00:00Z",
        url="url",
        to=["FOLLOWERS"],
        cc=["PUBLIC"],
    )
    doc = vocab.create("activity-id", "actor", inner, to=["FOLLOWERS"], cc=["PUBLIC"])
    assert (doc["type"], doc["id"], doc["actor"]) == ("Create", "activity-id", "actor")
    assert doc["object"] == inner
    assert (doc["to"], doc["cc"]) == (["FOLLOWERS"], ["PUBLIC"])
    assert doc["published"] == "2026-01-05T00:00:00Z"
    assert doc["@context"] == vocab.AS_CONTEXT


def test_delete_is_a_tombstone():
    doc = vocab.delete("activity-id", "actor", "note-id", to=["FOLLOWERS"], cc=["PUBLIC"])
    assert doc["type"] == "Delete"
    assert doc["object"] == {"id": "note-id", "type": "Tombstone"}
    assert (doc["to"], doc["cc"]) == (["FOLLOWERS"], ["PUBLIC"])


def test_update_wraps_the_actor_document():
    actor_doc = {"id": "actor", "type": "Person", "attachment": []}
    doc = vocab.update("activity-id", "actor", actor_doc, to=["FOLLOWERS"])
    assert (doc["type"], doc["id"], doc["actor"]) == ("Update", "activity-id", "actor")
    assert doc["object"] == actor_doc
    assert doc["to"] == ["FOLLOWERS"]
    assert doc["@context"] == vocab.AS_CONTEXT


def test_nodeinfo():
    assert nodeinfo.discovery(BASE)["links"][0]["href"] == f"{BASE}/nodeinfo/2.1"
    doc = nodeinfo.document(version="0.2.0", users_total=3, open_registrations=True)
    assert doc["software"] == {"name": "scrobbler", "version": "0.2.0"}
    assert doc["protocols"] == ["activitypub"]
    assert doc["usage"]["users"]["total"] == 3


def test_host_meta_points_at_webfinger():
    link = ET.fromstring(hostmeta.xrd(BASE)).find("{http://docs.oasis-open.org/ns/xri/xrd-1.0}Link")
    assert link.get("template") == f"{BASE}/.well-known/webfinger?resource={{uri}}"
