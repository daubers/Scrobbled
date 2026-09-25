"""The ActivityPub endpoints remote servers call. Served on the UI's domain: its nginx
forwards these paths to the API. Every ID is built from FEDERATION_BASE_URL, never from
the request, so a proxy misconfiguration can't produce wrong IDs."""

import json

from flask import Blueprint, Response, current_app, redirect, request

from scrobbler.extensions import db
from scrobbler.federation import followers as follower_state
from scrobbler.federation import inbox, keys, sharing
from scrobbler.federation import metrics as fed_metrics
from scrobbler.federation.models import FederationActivity
from scrobbler.federation.protocol import hostmeta, media, nodeinfo, vocab, webfinger

bp = Blueprint("federation", __name__)

_NOT_FOUND = {"error": "not found"}


def _config():
    return current_app.extensions["federation"]


def _profile_page(username: str) -> str:
    return f"{current_app.config['UI_BASE_URL']}/profile.html?u={username}"


def _urls(username: str) -> vocab.ActorUrls:
    return vocab.actor_urls(_config().base_url, username, _profile_page(username))


def _json(doc, content_type: str = media.ACTIVITY_JSON, status: int = 200, cache: int = 300):
    response = Response(json.dumps(doc), status=status, content_type=content_type)
    if status == 200 and cache:
        response.headers["Cache-Control"] = f"public, max-age={cache}"
    response.headers["Vary"] = "Accept"
    return response


def _not_found(kind: str):
    fed_metrics.lookups_total.labels(kind=kind, result="not_found").inc()
    return _json(_NOT_FOUND, content_type="application/json", status=404)


def _found(kind: str) -> None:
    fed_metrics.lookups_total.labels(kind=kind, result="found").inc()


# --- Discovery -----------------------------------------------------------------------


@bp.get("/.well-known/webfinger")
def webfinger_lookup():
    config = _config()
    try:
        username = webfinger.parse_resource(
            request.args.get("resource"), domain=config.domain, base_url=config.base_url
        )
    except webfinger.ResourceError as err:
        return _json({"error": str(err)}, content_type="application/json", status=400)

    if username.lower() == config.domain:  # the instance actor, as Mastodon names it
        actor_id, canonical = f"{config.base_url}/actor", config.domain
        profile = current_app.config["UI_BASE_URL"]
    else:
        found = sharing.shared_user(username)
        if found is None:
            return _not_found("webfinger")
        canonical = found[0].username
        actor_id, profile = _urls(canonical).id, _profile_page(canonical)
    _found("webfinger")
    doc = webfinger.jrd(
        username=canonical, domain=config.domain, actor_id=actor_id, profile_page=profile
    )
    response = _json(doc, content_type=media.JRD_JSON)
    response.headers["Access-Control-Allow-Origin"] = "*"  # RFC 7033 §5
    return response


@bp.get("/.well-known/host-meta")
def host_meta():
    return Response(hostmeta.xrd(_config().base_url), content_type=hostmeta.CONTENT_TYPE)


@bp.get("/.well-known/nodeinfo")
def nodeinfo_discovery():
    return _json(nodeinfo.discovery(_config().base_url), content_type="application/json")


@bp.get("/nodeinfo/2.1")
def nodeinfo_document():
    from scrobbler import __version__

    _found("nodeinfo")
    doc = nodeinfo.document(
        version=__version__, users_total=sharing.sharing_count(), open_registrations=True
    )
    schema = f'application/json; profile="{nodeinfo.SCHEMA_21}#"'
    return _json(doc, content_type=schema, cache=1800)


# --- Actors and collections -------------------------------------------------------------


@bp.get("/users/<username>")
def actor(username: str):
    if not media.wants_activitypub(request.headers.get("Accept")):
        # A person following a link: show them the profile page (for everyone, so
        # redirects don't reveal which accounts exist).
        return redirect(_profile_page(username), code=302)
    found = sharing.shared_user(username)
    if found is None:
        leaving = sharing.farewell_user(username)
        if leaving is None:
            return _not_found("actor")
        # Stopped sharing, but Rejects are still on their way: just enough for other
        # servers to verify them. No name, bio or other profile details.
        _found("actor")
        doc = vocab.person(
            _urls(leaving.username),
            username=leaving.username,
            name=leaving.username,
            summary=None,
            public_key_pem=keys.key_for(leaving.id).public_key_pem,
            manually_approves_followers=True,
            discoverable=False,
            indexable=False,
        )
        return _json(doc, cache=0)
    user, settings = found
    _found("actor")
    urls = _urls(user.username)
    doc = vocab.person(
        urls,
        username=user.username,
        name=settings.display_name or user.username,
        summary=settings.bio,
        public_key_pem=keys.key_for(user.id).public_key_pem,
        manually_approves_followers=settings.manually_approves_followers,
        discoverable=settings.discoverable,
        indexable=settings.indexable,
        published=settings.created_at.isoformat().replace("+00:00", "Z"),
    )
    return _json(doc)


def _collection(username: str, which: str, items: list | None, total: int = 0):
    found = sharing.shared_user(username)
    if found is None:
        return _not_found("actor")
    urls = _urls(found[0].username)
    return _json(vocab.ordered_collection(getattr(urls, which), total, items))


@bp.get("/users/<username>/outbox")
def outbox(username: str):
    return _collection(username, "outbox", [])


@bp.get("/users/<username>/followers")
def followers(username: str):
    found = sharing.shared_user(username)
    total = follower_state.counts(found[0].id)["accepted"] if found else 0
    return _collection(username, "followers", None, total)  # the count, not who


@bp.get("/users/<username>/collections/featured")
def featured(username: str):
    return _collection(username, "featured", [])  # pinned posts: none (yet)


@bp.get("/users/<username>/following")
def following(username: str):
    return _collection(username, "following", [])


# --- Instance actor -------------------------------------------------------------------


@bp.get("/actor")
def instance_actor():
    config = _config()
    actor_id = f"{config.base_url}/actor"
    doc = vocab.application(
        actor_id=actor_id,
        username=config.domain,
        inbox=f"{config.base_url}/inbox",
        outbox=f"{actor_id}/outbox",
        shared_inbox=f"{config.base_url}/inbox",
        key_id=f"{actor_id}#main-key",
        public_key_pem=keys.key_for(None).public_key_pem,
        url=current_app.config["UI_BASE_URL"],
    )
    return _json(doc)


@bp.get("/actor/outbox")
def instance_outbox():
    return _json(vocab.ordered_collection(f"{_config().base_url}/actor/outbox", 0, []))


# --- Inboxes ----------------------------------------------------------------------------


def _receive(inbox_path: str, kind: str):
    if (request.content_length or 0) > inbox.MAX_BYTES:
        return _json({"error": "too large"}, content_type="application/json", status=413)
    if not media.is_activitypub_content_type(request.content_type):
        return _json(
            {"error": "expected ActivityPub JSON"}, content_type="application/json", status=415
        )
    fed_metrics.inbox_requests_total.labels(inbox=kind).inc()
    status, outcome = inbox.receive(inbox_path, dict(request.headers), request.get_data())
    if status == 202:
        return Response(status=202)
    return _json({"error": outcome}, content_type="application/json", status=status)


def _path() -> str:
    return request.full_path.rstrip("?")


@bp.post("/users/<username>/inbox")
def user_inbox(username: str):
    if sharing.shared_user(username) is None:
        return _not_found("actor")
    return _receive(_path(), "user")


@bp.post("/inbox")
def shared_inbox():
    return _receive(_path(), "shared")


# --- Our activities ------------------------------------------------------------------------


@bp.get("/activities/<activity_uuid>")
def activity(activity_uuid: str):
    """Activities are fetchable when public; follow responses aren't."""
    found = db.session.get(FederationActivity, activity_uuid)
    if found is None or not found.public:
        return _json(_NOT_FOUND, content_type="application/json", status=404)
    return _json(found.document)
