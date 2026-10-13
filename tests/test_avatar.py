import io

import pytest

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
