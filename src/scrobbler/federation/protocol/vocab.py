"""ActivityStreams / ActivityPub document builders (plain dicts, ready for JSON)."""

import html
from dataclasses import dataclass

AS_CONTEXT = "https://www.w3.org/ns/activitystreams"
SECURITY_CONTEXT = "https://w3id.org/security/v1"
PUBLIC = "https://www.w3.org/ns/activitystreams#Public"

# The extension terms Mastodon and friends understand on actors.
ACTOR_CONTEXT = [
    AS_CONTEXT,
    SECURITY_CONTEXT,
    {
        "manuallyApprovesFollowers": "as:manuallyApprovesFollowers",
        "toot": "http://joinmastodon.org/ns#",
        "discoverable": "toot:discoverable",
        "indexable": "toot:indexable",
        "featured": {"@id": "toot:featured", "@type": "@id"},
        "schema": "http://schema.org#",
        "PropertyValue": "schema:PropertyValue",
        "value": "schema:value",
    },
]


@dataclass(frozen=True)
class ActorUrls:
    id: str
    inbox: str
    outbox: str
    followers: str
    following: str
    featured: str
    shared_inbox: str
    key_id: str
    profile_page: str


def actor_urls(base_url: str, username: str, profile_page: str) -> ActorUrls:
    actor = f"{base_url}/users/{username}"
    return ActorUrls(
        id=actor,
        inbox=f"{actor}/inbox",
        outbox=f"{actor}/outbox",
        followers=f"{actor}/followers",
        following=f"{actor}/following",
        featured=f"{actor}/collections/featured",
        shared_inbox=f"{base_url}/inbox",
        key_id=f"{actor}#main-key",
        profile_page=profile_page,
    )


def text_to_html(text: str | None) -> str:
    """Plain text (e.g. a bio) as the small HTML subset Mastodon keeps."""
    if not text or not text.strip():
        return ""
    paragraphs = [p for p in text.strip().replace("\r\n", "\n").split("\n\n") if p.strip()]
    return "".join(
        "<p>" + "<br>".join(html.escape(line) for line in p.split("\n")) + "</p>"
        for p in paragraphs
    )


def public_key(urls: ActorUrls, public_key_pem: str) -> dict:
    return {"id": urls.key_id, "owner": urls.id, "publicKeyPem": public_key_pem}


def person(
    urls: ActorUrls,
    *,
    username: str,
    name: str,
    summary: str | None,
    public_key_pem: str,
    manually_approves_followers: bool,
    discoverable: bool,
    indexable: bool,
    published: str | None = None,
    fields: list[tuple[str, str]] = (),
) -> dict:
    """A user's actor document. `fields` are (name, plain-text value) profile fields."""
    doc = {
        "@context": ACTOR_CONTEXT,
        "id": urls.id,
        "type": "Person",
        "preferredUsername": username,
        "name": name,
        "summary": text_to_html(summary),
        "url": urls.profile_page,
        "inbox": urls.inbox,
        "outbox": urls.outbox,
        "followers": urls.followers,
        "following": urls.following,
        "featured": urls.featured,
        "endpoints": {"sharedInbox": urls.shared_inbox},
        "publicKey": public_key(urls, public_key_pem),
        "manuallyApprovesFollowers": manually_approves_followers,
        "discoverable": discoverable,
        "indexable": indexable,
        "attachment": [
            {"type": "PropertyValue", "name": field, "value": html.escape(value)}
            for field, value in fields
        ],
    }
    if published:
        doc["published"] = published
    return doc


def application(
    *,
    actor_id: str,
    username: str,
    inbox: str,
    outbox: str,
    shared_inbox: str,
    key_id: str,
    public_key_pem: str,
    url: str,
) -> dict:
    """The instance actor: signs fetches on the server's behalf."""
    return {
        "@context": ACTOR_CONTEXT,
        "id": actor_id,
        "type": "Application",
        "preferredUsername": username,
        "name": username,
        "url": url,
        "inbox": inbox,
        "outbox": outbox,
        "endpoints": {"sharedInbox": shared_inbox},
        "publicKey": {"id": key_id, "owner": actor_id, "publicKeyPem": public_key_pem},
        "manuallyApprovesFollowers": True,
        "discoverable": False,
        "indexable": False,
    }


def ordered_collection(
    collection_id: str, total: int, items: list | None = None, first: str | None = None
) -> dict:
    """A collection. With items=None only the count is published (e.g. followers). With
    `first`, the collection just points at its first page rather than listing items
    itself (e.g. the outbox, paged by ordered_collection_page)."""
    doc = {
        "@context": AS_CONTEXT,
        "id": collection_id,
        "type": "OrderedCollection",
        "totalItems": total,
    }
    if items is not None:
        doc["orderedItems"] = items
    if first:
        doc["first"] = first
    return doc


def ordered_collection_page(
    page_id: str,
    *,
    part_of: str,
    items: list,
    next_page: str | None = None,
    prev_page: str | None = None,
) -> dict:
    doc = {
        "@context": AS_CONTEXT,
        "id": page_id,
        "type": "OrderedCollectionPage",
        "partOf": part_of,
        "orderedItems": items,
    }
    if next_page:
        doc["next"] = next_page
    if prev_page:
        doc["prev"] = prev_page
    return doc


def _response(kind: str, activity_id: str, actor_id: str, obj, to: str) -> dict:
    return {
        "@context": AS_CONTEXT,
        "id": activity_id,
        "type": kind,
        "actor": actor_id,
        "object": obj,
        "to": [to],
    }


def accept(activity_id: str, actor_id: str, follow: dict) -> dict:
    """Accept a Follow (the original Follow is embedded, as Mastodon expects)."""
    return _response("Accept", activity_id, actor_id, follow, follow["actor"])


def reject(activity_id: str, actor_id: str, follow: dict) -> dict:
    """Decline a follow request, or end an existing follow.

    The Follow is referenced by id rather than embedded: some servers match an embedded
    Follow by the pair of accounts, so a late Reject of an old Follow would cancel a
    newer request between the same accounts. An id matches exactly that Follow."""
    return _response("Reject", activity_id, actor_id, follow["id"], follow["actor"])


def block(activity_id: str, actor_id: str, blocked_actor: str) -> dict:
    return _response("Block", activity_id, actor_id, blocked_actor, blocked_actor)


def undo(activity_id: str, actor_id: str, undone: dict) -> dict:
    """Withdraw one of our earlier activities (e.g. a Block when unblocking)."""
    to = undone.get("to") or []
    return {
        "@context": AS_CONTEXT,
        "id": activity_id,
        "type": "Undo",
        "actor": actor_id,
        "object": undone,
        "to": list(to),
    }


def update(activity_id: str, actor_id: str, obj: dict, *, to: list[str]) -> dict:
    """Tells already-following servers something about the actor changed (their now
    playing field, here), so they don't have to re-fetch it to notice."""
    return {
        "@context": AS_CONTEXT,
        "id": activity_id,
        "type": "Update",
        "actor": actor_id,
        "object": obj,
        "to": list(to),
    }


def hashtag(name: str) -> dict:
    """A Hashtag tag, e.g. #Scrobbler. No href: we don't serve tag pages, and Mastodon
    still recognises and indexes the tag from `name` alone."""
    return {"type": "Hashtag", "name": f"#{name}"}


def note(
    note_id: str,
    *,
    actor_id: str,
    content_html: str,
    published: str,
    url: str,
    to: list[str],
    cc: list[str],
    tags: list[str] = (),
    attachment: list[dict] = (),
) -> dict:
    """A post (now playing, weekly summary or milestone). No `@context`: it's always
    embedded in the Create that carries the same addressing, as Mastodon does."""
    doc = {
        "id": note_id,
        "type": "Note",
        "attributedTo": actor_id,
        "content": content_html,
        "contentMap": {"en": content_html},
        "published": published,
        "url": url,
        "to": list(to),
        "cc": list(cc),
        "tag": [hashtag(t) for t in tags],
        "sensitive": False,
    }
    if attachment:
        doc["attachment"] = list(attachment)
    return doc


def create(activity_id: str, actor_id: str, obj: dict, *, to: list[str], cc: list[str]) -> dict:
    """Wraps a Note (or other object) for publishing, with the same addressing."""
    return {
        "@context": AS_CONTEXT,
        "id": activity_id,
        "type": "Create",
        "actor": actor_id,
        "object": obj,
        "published": obj.get("published"),
        "to": list(to),
        "cc": list(cc),
    }


def delete(activity_id: str, actor_id: str, note_id: str, *, to: list[str], cc: list[str]) -> dict:
    """Withdraws one of our Notes, as a Tombstone (the shape Mastodon sends)."""
    return {
        "@context": AS_CONTEXT,
        "id": activity_id,
        "type": "Delete",
        "actor": actor_id,
        "object": {"id": note_id, "type": "Tombstone"},
        "to": list(to),
        "cc": list(cc),
    }


def object_id(value) -> str | None:
    """An object's id, whether it's given inline (a dict) or as a bare URI."""
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) and value else None
