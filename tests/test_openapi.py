import json
import re
from pathlib import Path

import pytest
from openapi_spec_validator import validate

from scrobbler.lastfm.registry import METHODS

COMMITTED_SPEC = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
DOCUMENTED_PREFIXES = (
    "/api/v1/",
    "/2.0/",
    "/api/auth/",
    "/healthz",
    "/.well-known/",
    "/nodeinfo/",
    "/users/",
    "/inbox",
    "/actor",
)
HTTP_VERBS = {"get", "put", "post", "delete", "patch", "head", "options"}


@pytest.fixture(scope="module")
def spec(app):
    return app.test_client().get("/api/openapi.json").get_json()


def openapi_path(rule: str) -> str:
    return re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", rule)


def test_spec_is_valid_openapi(spec):
    validate(spec)


@pytest.fixture(scope="module")
def fed_spec(fed_app):
    return fed_app.test_client().get("/api/openapi.json").get_json()


def test_every_api_route_is_documented(app, spec, fed_app, fed_spec):
    _check_documented(app, spec)
    _check_documented(fed_app, fed_spec)  # with federation's routes too


def test_federation_off_documents_no_federation_paths(spec):
    assert not [p for p in spec["paths"] if p.startswith(("/api/v1/federation", "/users/"))]


def _check_documented(app, spec):
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith(DOCUMENTED_PREFIXES):
            continue
        path = openapi_path(rule.rule)
        assert path in spec["paths"], f"{rule.rule} is missing from the OpenAPI document"
        documented = {m.upper() for m in spec["paths"][path] if m in HTTP_VERBS}
        assert rule.methods - {"HEAD", "OPTIONS"} <= documented, rule.rule


def test_every_lastfm_method_is_documented(spec):
    get = spec["paths"]["/2.0/"]["get"]
    [method_param] = [p for p in get["parameters"] if p["name"] == "method"]
    assert set(method_param["schema"]["enum"]) == {m.name for m in METHODS.values()}
    for method in METHODS.values():
        assert f"### `{method.name}`" in get["description"]
    request_schemas = spec["paths"]["/2.0/"]["post"]["requestBody"]["content"][
        "application/x-www-form-urlencoded"
    ]["schema"]["oneOf"]
    assert len(request_schemas) == len(METHODS)


def test_ui_routes_declare_bearer_auth(fed_spec):
    spec = fed_spec  # includes the federation API
    public = {
        ("/api/v1/auth/login", "post"),
        ("/api/v1/auth/register", "post"),
        ("/api/v1/federation/profiles/{username}", "get"),
    }
    for path, operations in spec["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for verb, operation in operations.items():
            if verb not in HTTP_VERBS:
                continue
            if (path, verb) in public:
                assert "security" not in operation
            else:
                assert operation.get("security") == [{"bearerAuth": []}], f"{verb} {path}"
                assert "401" in operation["responses"], f"{verb} {path}"


def test_docs_pages_are_served(client):
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/redoc").status_code == 200


def test_committed_spec_is_up_to_date():
    from scrobbler.openapi.export import build_spec

    committed = json.loads(COMMITTED_SPEC.read_text())
    assert committed == build_spec(), (
        "docs/openapi.json is stale. Regenerate it with: "
        "uv run python -m scrobbler.openapi.export docs/openapi.json"
    )
