"""Remote actors: fetched with a signed GET, validated, cached for a day.

Validation (a document must really be the actor it claims to be):
  * its `id` equals the URL it was fetched from, and the host matches;
  * it's an actor type with an inbox on the same host;
  * its `publicKey` is owned by it (`publicKey.owner == id`).
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urldefrag, urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from scrobbler.extensions import db
from scrobbler.federation import outbound
from scrobbler.federation.models import FederationRemoteActor
from scrobbler.federation.net import FetchError

ACTOR_TYPES = {"Person", "Service", "Application", "Group", "Organization"}
MAX_AGE = timedelta(hours=24)
MAX_URL = 1024


class ActorError(Exception):
    """A remote document that isn't a valid actor (or key)."""


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _text(value, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _parse_actor(uri: str, doc: dict) -> dict:
    if len(uri) > 2048:
        raise ActorError("actor URI too long")
    if doc.get("id") != uri:
        raise ActorError(f"{uri} claims to be {doc.get('id')!r}")
    if doc.get("type") not in ACTOR_TYPES:
        raise ActorError(f"{uri} isn't an actor ({doc.get('type')!r})")
    host = _host(uri)
    inbox = doc.get("inbox")
    if not isinstance(inbox, str) or _host(inbox) != host or len(inbox) > MAX_URL:
        raise ActorError(f"{uri} has no inbox on its own host")
    endpoints = doc.get("endpoints") if isinstance(doc.get("endpoints"), dict) else {}
    shared = endpoints.get("sharedInbox")
    if not isinstance(shared, str) or _host(shared) != host or len(shared) > MAX_URL:
        shared = None
    key = doc.get("publicKey")
    if isinstance(key, list):  # some servers list several
        key = next((k for k in key if isinstance(k, dict) and k.get("owner") == uri), None)
    if not isinstance(key, dict) or key.get("owner") != uri or not key.get("publicKeyPem"):
        raise ActorError(f"{uri} has no public key of its own")
    key_id = key.get("id")
    if not isinstance(key_id, str) or _host(key_id) != host:
        raise ActorError(f"{uri}'s key id isn't on its host")
    load_public_key(key["publicKeyPem"])  # must parse
    return {
        "host": host,
        "inbox": inbox,
        "shared_inbox": shared,
        "username": _text(doc.get("preferredUsername"), 255),
        "display_name": _text(doc.get("name"), 255),
        "public_key_id": key_id,
        "public_key_pem": key["publicKeyPem"],
    }


def load_public_key(pem: str) -> RSAPublicKey:
    try:
        key = serialization.load_pem_public_key(pem.encode())
    except (ValueError, TypeError) as err:
        raise ActorError("unreadable public key") from err
    if not isinstance(key, RSAPublicKey):
        raise ActorError("only RSA keys are supported")
    return key


def get_actor(uri: str, refresh: bool = False) -> FederationRemoteActor:
    """The actor at `uri`, from cache if fresh. Raises ActorError or FetchError."""
    actor = db.session.scalar(db.select(FederationRemoteActor).filter_by(uri=uri))
    fresh = actor is not None and datetime.now(UTC) - actor.fetched_at < MAX_AGE
    if actor is not None and not refresh and (fresh or actor.gone):
        return actor
    try:
        fields = _parse_actor(uri, outbound.signed_get_json(uri))
    except FetchError as err:
        if err.status == 410 and actor is not None:  # deleted account
            actor.gone = True
            db.session.commit()
        raise
    if actor is None:
        actor = FederationRemoteActor(uri=uri, **fields)
        db.session.add(actor)
    else:
        for name, value in fields.items():
            setattr(actor, name, value)
        actor.gone = False
    actor.fetched_at = datetime.now(UTC)
    db.session.commit()
    return actor


def actor_for_key(key_id: str, refresh: bool = False) -> FederationRemoteActor:
    """The actor that owns `key_id`: usually `<actor>#main-key`, sometimes a separate
    key document naming its owner."""
    cached = db.session.scalar(db.select(FederationRemoteActor).filter_by(public_key_id=key_id))
    if cached is not None and not refresh:
        return get_actor(cached.uri)
    document_url = urldefrag(key_id).url
    try:
        return _matching(get_actor(document_url, refresh=True), key_id)
    except ActorError:
        # Not an actor: a standalone key document pointing at its owner
        doc = outbound.signed_get_json(document_url)
        owner = doc.get("owner")
        if doc.get("id") != key_id or not isinstance(owner, str) or _host(owner) != _host(key_id):
            raise
        return _matching(get_actor(owner, refresh=True), key_id)


def _matching(actor: FederationRemoteActor, key_id: str) -> FederationRemoteActor:
    if actor.public_key_id != key_id:
        raise ActorError(f"{actor.uri} doesn't use key {key_id}")
    return actor
