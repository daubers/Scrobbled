import hashlib
import io
import threading

import pytest
from werkzeug.serving import make_server

from scrobbler.extensions import db
from scrobbler.models import User
from scrobbler.services.accounts import issue_ui_token

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
PNG = b"\x89PNG\r\n\x1a\nxxxx"
URL = "/api/v1/me/avatar"


@pytest.fixture
def storage(app, tmp_path):
    saved = app.config["ART_STORAGE_DIR"]
    app.config["ART_STORAGE_DIR"] = str(tmp_path)
    yield tmp_path
    app.config["ART_STORAGE_DIR"] = saved


def put(client, auth, data=JPEG, name="me.jpg"):
    return client.put(
        URL,
        headers=auth,
        data={"file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


def test_endpoints_require_auth(client):
    assert client.get(URL).status_code == 401
    assert client.put(URL).status_code == 401
    assert client.delete(URL).status_code == 401


def test_no_avatar_to_begin_with(client, auth, user, storage):
    assert client.get(URL, headers=auth).status_code == 404
    assert client.delete(URL, headers=auth).status_code == 404
    me = client.get("/api/v1/auth/me", headers=auth).get_json()
    assert (me["has_avatar"], me["avatar_source"], me["avatar_updated_at"]) == (False, "none", None)


def test_upload_then_get_serves_the_image(client, auth, storage, metric_delta):
    ok = metric_delta("scrobbler_avatar_uploads_total", result="ok")
    size = metric_delta("scrobbler_avatar_upload_bytes_count")
    action = metric_delta("scrobbler_avatar_actions_total", action="uploaded")
    response = put(client, auth)
    assert response.status_code == 200
    body = response.get_json()
    assert (body["has_avatar"], body["avatar_source"]) == (True, "upload")
    image = client.get(URL, headers=auth)
    assert image.status_code == 200
    assert (image.data, image.mimetype) == (JPEG, "image/jpeg")
    assert image.headers["Cache-Control"] == "private, no-cache"
    assert (ok.delta, size.delta, action.delta) == (1, 1, 1)
    assert client.get("/api/v1/auth/me", headers=auth).get_json()["has_avatar"] is True


def test_uploading_again_replaces_it(client, auth, storage):
    put(client, auth)
    put(client, auth, data=PNG, name="me.png")
    image = client.get(URL, headers=auth)
    assert (image.data, image.mimetype) == (PNG, "image/png")
    assert [p.name for p in (storage / "avatars").iterdir()] == ["1"]  # no stray temp files


def test_supports_conditional_requests(client, auth, storage):
    put(client, auth)
    first = client.get(URL, headers=auth)
    again = client.get(URL, headers={**auth, "If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304


def test_delete_removes_the_image_and_file(client, auth, storage, metric_delta):
    put(client, auth)
    removed = metric_delta("scrobbler_avatar_actions_total", action="removed")
    assert client.delete(URL, headers=auth).status_code == 204
    assert client.get(URL, headers=auth).status_code == 404
    assert not (storage / "avatars" / "1").exists()
    assert client.get("/api/v1/auth/me", headers=auth).get_json()["has_avatar"] is False
    assert removed.delta == 1


def test_a_missing_file_reads_as_no_avatar(client, auth, storage):
    put(client, auth)
    (storage / "avatars" / "1").unlink()
    assert client.get(URL, headers=auth).status_code == 404


def test_avatars_are_per_user(client, auth, storage, make_user):
    put(client, auth)
    token = issue_ui_token(make_user(username="mallory"))
    other = {"Authorization": f"Bearer {token}"}
    assert client.get(URL, headers=other).status_code == 404
    assert db.session.scalar(db.select(User).where(User.username == "mallory")).has_avatar is False


@pytest.mark.parametrize(
    ("data", "status", "result"),
    [(b"", 400, "empty"), (b"GIF89a....", 415, "bad_type"), (JPEG + b"x" * 100, 413, "too_large")],
)
def test_upload_rejects_bad_files(client, auth, app, storage, metric_delta, data, status, result):
    saved = app.config["AVATAR_UPLOAD_MAX_BYTES"]
    app.config["AVATAR_UPLOAD_MAX_BYTES"] = 50
    try:
        counted = metric_delta("scrobbler_avatar_uploads_total", result=result)
        assert put(client, auth, data=data).status_code == status
        assert counted.delta == 1
        assert client.get(URL, headers=auth).status_code == 404
    finally:
        app.config["AVATAR_UPLOAD_MAX_BYTES"] = saved


def test_upload_requires_a_file(client, auth, storage):
    response = client.put(URL, headers=auth, data={}, content_type="multipart/form-data")
    assert response.status_code == 422


# --- Gravatar ------------------------------------------------------------------------------


def _digest(email):
    return hashlib.sha256(email.encode()).hexdigest()


GRAVATAR_JPEG = b"\xff\xd8\xff\xe0gravatar-jpeg"
_GRAVATARS = {
    _digest("alice@example.com"): ("200 OK", GRAVATAR_JPEG),
    _digest("html@example.com"): ("200 OK", b"<html>not an image</html>"),
    _digest("big@example.com"): ("200 OK", b"\xff\xd8\xff" + b"x" * (1024 * 1024)),
    _digest("boom@example.com"): ("500 Internal Server Error", b"boom"),
}
SEEN = []


def _fake_gravatar(environ, start_response):
    SEEN.append((environ["PATH_INFO"], environ["QUERY_STRING"]))
    status, body = _GRAVATARS.get(
        environ["PATH_INFO"].rsplit("/", 1)[-1], ("404 Not Found", b"not found")
    )
    start_response(status, [("Content-Length", str(len(body)))])
    return [body]


@pytest.fixture(scope="module")
def fake_gravatar():
    server = make_server("127.0.0.1", 0, _fake_gravatar, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/avatar/"
    server.shutdown()


@pytest.fixture
def gravatar(app, storage, fake_gravatar):
    keys = ("AVATAR_GRAVATAR_ENABLED", "GRAVATAR_URL")
    saved = {k: app.config[k] for k in keys}
    app.config.update(AVATAR_GRAVATAR_ENABLED=True, GRAVATAR_URL=fake_gravatar)
    SEEN.clear()
    yield
    app.config.update(saved)


def use_gravatar(client, auth):
    return client.post(f"{URL}/gravatar", headers=auth)


def set_email(user, email):
    user.email = email
    db.session.commit()


def test_gravatar_requires_auth(client):
    assert client.post(f"{URL}/gravatar").status_code == 401


def test_gravatar_is_off_by_default(client, auth, user, storage, metric_delta):
    me = client.get("/api/v1/auth/me", headers=auth).get_json()
    assert me["gravatar_enabled"] is False
    response = use_gravatar(client, auth)
    assert (response.status_code, response.get_json()["error"]["code"]) == (
        404,
        "gravatar_disabled",
    )


def test_gravatar_is_cached_and_served_like_an_upload(client, auth, user, gravatar, metric_delta):
    ok = metric_delta("scrobbler_avatar_gravatar_fetches_total", result="ok")
    action = metric_delta("scrobbler_avatar_actions_total", action="gravatar")
    assert client.get("/api/v1/auth/me", headers=auth).get_json()["gravatar_enabled"] is True
    response = use_gravatar(client, auth)
    assert response.status_code == 200
    assert response.get_json()["avatar_source"] == "gravatar"
    image = client.get(URL, headers=auth)
    assert (image.data, image.mimetype) == (GRAVATAR_JPEG, "image/jpeg")
    # Asked for the SHA-256 of the email at a fixed size, with a 404 rather than a stock picture.
    assert [(f"/avatar/{_digest('alice@example.com')}", "s=256&d=404")] == SEEN
    assert (ok.delta, action.delta) == (1, 1)


def test_gravatar_email_is_trimmed_and_lowercased(client, auth, user, gravatar):
    set_email(user, "  Alice@Example.COM ")
    assert use_gravatar(client, auth).status_code == 200


def test_gravatar_replaces_an_upload_and_back(client, auth, user, gravatar):
    put(client, auth, data=PNG, name="me.png")
    use_gravatar(client, auth)
    assert client.get(URL, headers=auth).data == GRAVATAR_JPEG
    put(client, auth)
    assert client.get(URL, headers=auth).data == JPEG
    assert client.get("/api/v1/auth/me", headers=auth).get_json()["avatar_source"] == "upload"


@pytest.mark.parametrize(
    ("email", "status", "code", "result"),
    [
        ("ghost@example.com", 404, "no_gravatar", "none"),
        ("boom@example.com", 502, "gravatar_unavailable", "error"),
        ("html@example.com", 502, "gravatar_unavailable", "bad_type"),
        ("big@example.com", 502, "gravatar_unavailable", "too_large"),
    ],
)
def test_gravatar_failures_keep_the_current_image(
    client, auth, user, gravatar, metric_delta, email, status, code, result
):
    put(client, auth)
    set_email(user, email)
    counted = metric_delta("scrobbler_avatar_gravatar_fetches_total", result=result)
    response = use_gravatar(client, auth)
    assert (response.status_code, response.get_json()["error"]["code"]) == (status, code)
    assert counted.delta == 1
    assert client.get(URL, headers=auth).data == JPEG


def test_gravatar_unreachable_is_a_502(client, auth, user, gravatar, app):
    app.config["GRAVATAR_URL"] = "http://127.0.0.1:1/avatar/"
    response = use_gravatar(client, auth)
    assert (response.status_code, response.get_json()["error"]["code"]) == (
        502,
        "gravatar_unavailable",
    )
