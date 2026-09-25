import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from conftest import signed
from scrobbler.extensions import db
from scrobbler.models import AuthToken, Session


def call(client, api_app, method, **params):
    data = signed({"method": method, "api_key": api_app.api_key, **params}, api_app.shared_secret)
    return ET.fromstring(client.post("/2.0/", data=data).data)


def error_code(root):
    return root.find("error").get("code")


def test_mobile_session(client, api_app, user, metric_delta):
    created = metric_delta("scrobbler_auth_sessions_created_total", flow="mobile")
    root = call(client, api_app, "auth.getMobileSession", username="ALICE", password="hunter22")
    assert root.find("session/name").text == "alice"
    key = root.find("session/key").text
    assert db.session.scalar(db.select(Session).filter_by(session_key=key)).user_id == user.id
    assert created.delta == 1


def test_mobile_session_bad_password(client, api_app, user, metric_delta):
    failures = metric_delta("scrobbler_auth_failures_total", reason="bad_password")
    root = call(client, api_app, "auth.getMobileSession", username="alice", password="nope")
    assert error_code(root) == "4"
    assert failures.delta == 1


def test_mobile_session_requires_signature(client, api_app, user):
    params = {
        "method": "auth.getMobileSession",
        "api_key": api_app.api_key,
        "username": "alice",
        "password": "hunter22",
    }
    assert error_code(ET.fromstring(client.post("/2.0/", data=params).data)) == "13"


def test_desktop_flow(client, api_app, user, auth, metric_delta):
    created = metric_delta("scrobbler_auth_sessions_created_total", flow="desktop")
    token = call(client, api_app, "auth.getToken").find("token").text

    # Not approved yet
    assert error_code(call(client, api_app, "auth.getSession", token=token)) == "14"

    # The client sends the user to /api/auth/, which hands over to the UI
    redirect = client.get(f"/api/auth/?api_key={api_app.api_key}&token={token}")
    assert redirect.status_code == 302
    location = urlsplit(redirect.headers["Location"])
    assert location.path == "/approve.html"
    assert parse_qs(location.query) == {"api_key": [api_app.api_key], "token": [token]}

    # The UI shows who is asking, then approves
    info = client.get(f"/api/v1/tokens/{token}", headers=auth).get_json()
    assert info == {"app_name": api_app.name, "approved": False}
    approved = client.post("/api/v1/tokens/approve", json={"token": token}, headers=auth)
    assert approved.status_code == 204

    root = call(client, api_app, "auth.getSession", token=token)
    assert root.find("session/name").text == "alice"
    assert created.delta == 1

    # Tokens are single use
    assert error_code(call(client, api_app, "auth.getSession", token=token)) == "4"


def test_token_is_bound_to_its_app(client, api_app, make_app, user, auth):
    token = call(client, api_app, "auth.getToken").find("token").text
    client.post("/api/v1/tokens/approve", json={"token": token}, headers=auth)
    other_app = make_app(owner=user)
    assert error_code(call(client, other_app, "auth.getSession", token=token)) == "4"


def test_expired_token(client, api_app, auth):
    token = call(client, api_app, "auth.getToken").find("token").text
    auth_token = db.session.scalar(db.select(AuthToken).filter_by(token=token))
    auth_token.created_at = datetime.now(UTC) - timedelta(hours=2)
    db.session.commit()

    assert error_code(call(client, api_app, "auth.getSession", token=token)) == "15"
    response = client.post("/api/v1/tokens/approve", json={"token": token}, headers=auth)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "token_expired"


def test_unknown_token_in_ui(client, auth):
    assert client.get("/api/v1/tokens/nope", headers=auth).status_code == 404
