"""OpenAPI descriptions of the ActivityPub endpoints (served on the UI's domain)."""

from flask import Flask
from flask_smorest import Api

TAG = "ActivityPub"

_AP = {"application/activity+json": {"schema": {"type": "object"}}}
_NOT_FOUND = {"description": "Unknown, or not sharing (the two are indistinguishable)"}
_INBOX_RESPONSES = {
    "202": {"description": "Accepted for processing (also for a repeated activity)"},
    "400": {"description": "Not an activity with an id, type and actor"},
    "403": {"description": "The sender's domain is blocked"},
    "404": {"description": "No such user sharing (user inbox)"},
    "413": {"description": "Larger than 256 KB"},
    "415": {"description": "Not ActivityPub JSON"},
}
_USERNAME = {"name": "username", "in": "path", "required": True, "schema": {"type": "string"}}

# endpoint -> (method, summary, description, extra parameters, responses)
_OPERATIONS = {
    "federation.webfinger_lookup": (
        "get",
        "WebFinger",
        "Resolves `acct:user@domain` (or an actor URL) to the user's actor. Only users "
        "sharing their listening can be found.",
        [
            {
                "name": "resource",
                "in": "query",
                "required": True,
                "schema": {"type": "string"},
                "example": "acct:alice@scrobble.example",
            }
        ],
        {
            "200": {
                "description": "JRD",
                "content": {"application/jrd+json": {"schema": {"type": "object"}}},
            },
            "400": {"description": "Not a resource on this server"},
            "404": _NOT_FOUND,
        },
    ),
    "federation.host_meta": (
        "get",
        "host-meta",
        "XRD pointing at WebFinger (RFC 6415).",
        [],
        {
            "200": {
                "description": "XRD",
                "content": {"application/xrd+xml": {"schema": {"type": "string"}}},
            }
        },
    ),
    "federation.nodeinfo_discovery": (
        "get",
        "NodeInfo discovery",
        "Links to the NodeInfo 2.1 document.",
        [],
        {
            "200": {
                "description": "Links",
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        },
    ),
    "federation.nodeinfo_document": (
        "get",
        "NodeInfo 2.1",
        "Software name and version, protocols, and users sharing.",
        [],
        {
            "200": {
                "description": "NodeInfo",
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        },
    ),
    "federation.actor": (
        "get",
        "Actor",
        "A user's `Person` actor when asked for ActivityPub JSON; browsers are redirected to "
        "the web UI's profile page.",
        [_USERNAME],
        {
            "200": {"description": "Person", "content": _AP},
            "302": {"description": "Redirect to the profile page (non-ActivityPub Accept)"},
            "404": _NOT_FOUND,
        },
    ),
    "federation.outbox": (
        "get",
        "Outbox",
        "The user's public and unlisted posts (followers-only posts are never listed "
        "here, since this endpoint isn't authenticated). Without `page`, an "
        "OrderedCollection pointing at the first page; with it, an OrderedCollectionPage "
        "of up to 20 `Create`s, newest first.",
        [
            _USERNAME,
            {
                "name": "page",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "minimum": 1},
            },
        ],
        {
            "200": {
                "description": "OrderedCollection or OrderedCollectionPage",
                "content": _AP,
            },
            "404": _NOT_FOUND,
        },
    ),
    "federation.post_note": (
        "get",
        "Post",
        "One public or unlisted post's `Note`, when asked for ActivityPub JSON; browsers "
        "are redirected to the web UI's post page. Followers-only and deleted posts "
        "answer 404 here (they're delivered directly to followers instead).",
        [
            _USERNAME,
            {"name": "post_uuid", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        {
            "200": {"description": "Note", "content": _AP},
            "302": {"description": "Redirect to the post page (non-ActivityPub Accept)"},
            "404": {"description": "Not found, not listed, or deleted"},
        },
    ),
    "federation.followers": (
        "get",
        "Followers",
        "Follower count (members aren't listed).",
        [_USERNAME],
        {"200": {"description": "OrderedCollection", "content": _AP}, "404": _NOT_FOUND},
    ),
    "federation.following": (
        "get",
        "Following",
        "Accounts the user follows.",
        [_USERNAME],
        {"200": {"description": "OrderedCollection", "content": _AP}, "404": _NOT_FOUND},
    ),
    "federation.featured": (
        "get",
        "Featured",
        "Pinned posts (always empty for now).",
        [_USERNAME],
        {"200": {"description": "OrderedCollection", "content": _AP}, "404": _NOT_FOUND},
    ),
    "federation.instance_actor": (
        "get",
        "Instance actor",
        "The server's `Application` actor.",
        [],
        {"200": {"description": "Application", "content": _AP}},
    ),
    "federation.instance_outbox": (
        "get",
        "Instance outbox",
        "Always empty.",
        [],
        {"200": {"description": "OrderedCollection", "content": _AP}},
    ),
    "federation.user_inbox": (
        "post",
        "User inbox",
        "Receives signed activities for one user. They're stored and answered with 202, then "
        "verified and processed in the background.",
        [_USERNAME],
        _INBOX_RESPONSES,
    ),
    "federation.shared_inbox": (
        "post",
        "Shared inbox",
        "Receives signed activities for any user on this server (as for the user inbox).",
        [],
        _INBOX_RESPONSES,
    ),
    "federation.activity": (
        "get",
        "Activity",
        "One of our activities, when public. Replies to follows aren't public.",
        [{"name": "activity_uuid", "in": "path", "required": True, "schema": {"type": "string"}}],
        {
            "200": {"description": "Activity", "content": _AP},
            "404": {"description": "Not found or not public"},
        },
    ),
}


def register(app: Flask, api: Api) -> None:
    api.spec.tag({"name": TAG, "description": "Fediverse endpoints, served on the web UI's domain"})
    rules = {rule.endpoint: rule for rule in app.url_map.iter_rules()}
    for endpoint, (method, summary, description, parameters, responses) in _OPERATIONS.items():
        operation = {
            "tags": [TAG],
            "summary": summary,
            "description": description,
            "operationId": endpoint.replace(".", "_"),
            "responses": responses,
        }
        if parameters:
            operation["parameters"] = parameters
        if method == "post":
            operation["requestBody"] = {"content": _AP}
        api.spec.path(rule=rules[endpoint], operations={method: operation})
