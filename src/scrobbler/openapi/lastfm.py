"""OpenAPI description of the Last.fm-compatible /2.0/ endpoint.

/2.0/ is a single endpoint that dispatches on the `method` parameter, which OpenAPI
can't express as one operation per method. It's documented as one GET and one POST
operation, with `method` as an enum and every method's parameters, auth needs,
notes and example responses generated from the handler registry.
"""

from flask import Flask
from flask_smorest import Api

from scrobbler.lastfm.errors import MESSAGES
from scrobbler.lastfm.registry import METHODS, LastfmMethod

TAG = "Last.fm"

COMMON_PARAMS = [
    ("method", "The API method to call, e.g. `track.scrobble`", True),
    ("api_key", "API key of one of your applications", True),
    ("api_sig", "Request signature; required by signed methods (see below)", False),
    ("sk", "Session key; required by methods that act as a user", False),
    ("format", "`xml` (default) or `json`", False),
]

INTRO = """\
Last.fm (Audioscrobbler 2.0) compatible API. Point a scrobbler's "custom Last.fm
server" setting at this server's `/2.0/`.

**Signing.** Signed methods need `api_sig`: take every parameter except `format`,
`callback` and `api_sig`, sort by name, concatenate each name followed by its
value, append your application's shared secret, and take the md5 hex digest.

**Sessions.** Methods that act as a user need `sk`, a session key from
`auth.getMobileSession` or the desktop flow (`auth.getToken`, approval at
`/api/auth/`, then `auth.getSession`).

**Errors** use Last.fm's codes: {errors}.

## Methods
"""


def _method_section(method: LastfmMethod) -> str:
    needs = []
    if method.signed:
        needs.append("signed")
    if method.session:
        needs.append("session key")
    lines = [f"### `{method.name}`", "", method.summary + ".", ""]
    lines.append(f"*Requires:* {', '.join(needs) if needs else 'api_key only'}.")
    if method.params:
        lines += ["", "| Parameter | Required | Description |", "|---|---|---|"]
        lines += [
            f"| `{p.name}` | {'yes' if p.required else 'no'} | {p.description} |"
            for p in method.params
        ]
    if method.notes:
        lines += ["", method.notes]
    return "\n".join(lines)


def _params_schema(method: LastfmMethod) -> dict:
    properties = {
        "method": {"type": "string", "const": method.name},
        "api_key": {"type": "string"},
    }
    required = ["method", "api_key"]
    if method.signed:
        properties["api_sig"] = {"type": "string", "description": "md5 request signature"}
        required.append("api_sig")
    if method.session:
        properties["sk"] = {"type": "string", "description": "Session key"}
        required.append("sk")
    for param in method.params:
        prop = {"type": "string", "description": param.description}
        if param.example is not None:
            prop["examples"] = [param.example]
        properties[param.name] = prop
        if param.required:
            required.append(param.name)
    properties["format"] = {"type": "string", "enum": ["xml", "json"], "default": "xml"}
    return {
        "type": "object",
        "title": method.name,
        "description": method.summary,
        "properties": properties,
        "required": required,
    }


def _schema_name(method: LastfmMethod) -> str:
    return "Lastfm" + "".join(part[:1].upper() + part[1:] for part in method.name.split("."))


def register(app: Flask, api: Api) -> None:
    rules = {rule.endpoint: rule for rule in app.url_map.iter_rules()}
    methods = sorted(METHODS.values(), key=lambda m: m.name)
    spec = api.spec

    spec.tag({"name": TAG, "description": "Last.fm-compatible API for players and scrobblers"})
    spec.components.schema(
        "LastfmError",
        {
            "type": "object",
            "properties": {
                "error": {"type": "integer", "examples": [6]},
                "message": {"type": "string"},
            },
            "required": ["error", "message"],
        },
    )
    for method in methods:
        spec.components.schema(f"{_schema_name(method)}Request", _params_schema(method))

    errors = ", ".join(f"{code} ({message.split(' - ')[0]})" for code, message in MESSAGES.items())
    description = INTRO.format(errors=errors) + "\n\n".join(map(_method_section, methods))

    # Every non-array parameter any method takes, so "Try it out" works for all of them.
    method_params = {}
    for method in methods:
        for param in method.params:
            if not param.name.endswith("[i]"):
                method_params.setdefault(param.name, param)

    query_params = [
        {
            "name": name,
            "in": "query",
            "required": required,
            "description": desc,
            "schema": (
                {"type": "string", "enum": [m.name for m in methods]}
                if name == "method"
                else {"type": "string", "enum": ["xml", "json"]}
                if name == "format"
                else {"type": "string"}
            ),
        }
        for name, desc, required in COMMON_PARAMS
    ] + [
        {
            "name": p.name,
            "in": "query",
            "required": False,
            "description": f"{p.description} (only for methods that take it)",
            "schema": {"type": "string"},
        }
        for p in method_params.values()
    ]

    responses = {
        "200": {
            "description": '`<lfm status="ok">` wrapping the method\'s result, or JSON',
            "content": {
                "application/xml": {
                    "schema": {"type": "string"},
                    "examples": {
                        m.name: {"summary": m.name, "value": m.example_xml}
                        for m in methods
                        if m.example_xml
                    },
                },
                "application/json": {"schema": {"type": "object"}},
            },
        },
        "400": _error_response("Unknown method or invalid parameters (codes 3, 6)"),
        "403": _error_response("Authentication failed (codes 4, 9, 10, 13, 14, 15)"),
    }

    request_schemas = [{"$ref": f"#/components/schemas/{_schema_name(m)}Request"} for m in methods]
    spec.path(
        rule=rules["lastfm.dispatch"],
        operations={
            "get": {
                "tags": [TAG],
                "summary": "Call a Last.fm API method (query string)",
                "description": description,
                "operationId": "lastfmGet",
                "parameters": query_params,
                "responses": responses,
            },
            "post": {
                "tags": [TAG],
                "summary": "Call a Last.fm API method (form body)",
                "description": (
                    "Same as GET, with parameters in an `application/x-www-form-urlencoded` "
                    "body. Write methods (`track.*`, `auth.getMobileSession`) should use POST."
                ),
                "operationId": "lastfmPost",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/x-www-form-urlencoded": {"schema": {"oneOf": request_schemas}}
                    },
                },
                "responses": responses,
            },
        },
    )
    spec.path(
        rule=rules["lastfm.approve_redirect"],
        operations={
            "get": {
                "tags": [TAG],
                "summary": "Approve a desktop-auth token",
                "description": (
                    "Where Last.fm clients send the user after `auth.getToken`. Redirects to "
                    "the web UI's approve page, which asks the user to allow access."
                ),
                "operationId": "lastfmApproveRedirect",
                "parameters": [
                    {
                        "name": "api_key",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "token",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {"302": {"description": "Redirect to the web UI's approve page"}},
            }
        },
    )


def _error_response(description: str) -> dict:
    return {
        "description": description,
        "content": {
            "application/xml": {
                "schema": {"type": "string"},
                "example": '<lfm status="failed"><error code="6">Invalid parameters</error></lfm>',
            },
            "application/json": {"schema": {"$ref": "#/components/schemas/LastfmError"}},
        },
    }
