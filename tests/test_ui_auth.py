from datetime import UTC, datetime, timedelta

from scrobbler.extensions import db
from scrobbler.models import UiToken


def register(client, username="bob", password="correct horse", email=None):
    return client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": email or f"{username}@example.com",
            "password": password,
        },
    )


def test_register_returns_token_and_user(client, metric_delta):
    registrations = metric_delta("scrobbler_registrations_total")
    response = register(client)
    assert response.status_code == 201
    body = response.get_json()
    assert body["user"]["username"] == "bob"
    assert "password" not in body["user"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.get_json()["username"] == "bob"
    assert registrations.delta == 1


def test_usernames_are_unique_case_insensitively(client):
    register(client, "Bob")
    response = register(client, "bOB")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "username_taken"


def test_register_validation_errors_use_error_shape(client):
    response = register(client, password="short")
    assert response.status_code == 422
    error = response.get_json()["error"]
    assert error["code"] == "unprocessable_entity"
    assert "password" in error["details"]["json"]


def test_invalid_username_is_rejected(client):
    response = register(client, username="no spaces", email="nospaces@example.com")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_username"


def test_login(client, user, metric_delta):
    ok = metric_delta("scrobbler_ui_logins_total", result="success")
    failed = metric_delta("scrobbler_ui_logins_total", result="failure")

    response = client.post("/api/v1/auth/login", json={"username": "ALICE", "password": "hunter22"})
    assert response.status_code == 200
    assert response.get_json()["user"]["username"] == "alice"

    bad = client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"})
    assert bad.status_code == 401
    assert bad.get_json()["error"]["code"] == "invalid_credentials"
    nobody = client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x"})
    assert nobody.status_code == 401
    assert (ok.delta, failed.delta) == (1, 2)


def test_protected_routes_need_a_token(client):
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        response = client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.get_json()["error"]["code"] == "unauthorized"


def test_logout_revokes_the_token(client, auth):
    assert client.post("/api/v1/auth/logout", headers=auth).status_code == 204
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401


def test_expired_tokens_are_rejected(client, auth):
    for token in db.session.scalars(db.select(UiToken)):
        token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.session.commit()
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401


def test_cors_allows_only_the_configured_ui_origin(client, app):
    allowed = app.config["CORS_ORIGINS"][0]
    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
    }
    ok = client.options("/api/v1/auth/me", headers={"Origin": allowed, **preflight})
    assert ok.headers["Access-Control-Allow-Origin"] == allowed
    assert "authorization" in ok.headers["Access-Control-Allow-Headers"].lower()

    evil = client.options(
        "/api/v1/auth/me", headers={"Origin": "https://evil.example", **preflight}
    )
    assert "Access-Control-Allow-Origin" not in evil.headers
