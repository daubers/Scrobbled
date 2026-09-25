"""OpenAPI descriptions of the ActivityPub endpoints (served on the UI's domain)."""

from flask import Flask
from flask_smorest import Api

TAG = "ActivityPub"

_AP = {"application/activity+json": {"schema": {"type": "object"}}}
_NOT_FOUND = {"description": "Unknown, or not sharing (the two are indistinguishable)"}
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
        "The user's published activities.",
        [_USERNAME],
        {"200": {"description": "OrderedCollection", "content": _AP}, "404": _NOT_FOUND},
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
        "Receives activities for one user (signed deliveries).",
        [_USERNAME],
        {
            "202": {"description": "Accepted"},
            "404": _NOT_FOUND,
            "413": {"description": "Too large"},
        },
    ),
    "federation.shared_inbox": (
        "post",
        "Shared inbox",
        "Receives activities for any user on this server.",
        [],
        {"202": {"description": "Accepted"}, "413": {"description": "Too large"}},
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
