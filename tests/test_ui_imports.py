import io
from datetime import UTC, datetime, timedelta

import pytest

from scrobbler import worker


def csv_bytes(n=3) -> bytes:
    day = datetime.now(UTC) - timedelta(days=400)
    rows = [
        f"Artist,Album,Track {i},{(day + timedelta(minutes=i)).strftime('%d %b %Y %H:%M')}"
        for i in range(n)
    ]
    return ("\n".join(rows) + "\n").encode()


def upload(client, auth, data: bytes, name="export.csv", query=""):
    return client.post(
        f"/api/v1/imports/file{query}",
        data={"file": (io.BytesIO(data), name)},
        headers=auth,
        content_type="multipart/form-data",
    )


def test_options_hide_lastfm_without_an_api_key(client, auth, app):
    body = client.get("/api/v1/imports/options", headers=auth).get_json()
    assert body["sources"] == ["csv", "json"]
    assert body["max_upload_bytes"] == app.config["IMPORT_MAX_BYTES"]


def test_options_offer_lastfm_with_an_api_key(client, auth, app):
    app.config["LASTFM_API_KEY"] = "configured"
    try:
        body = client.get("/api/v1/imports/options", headers=auth).get_json()
    finally:
        app.config["LASTFM_API_KEY"] = ""
    assert body["sources"] == ["csv", "json", "lastfm"]


def test_upload_then_follow_progress(client, auth):
    response = upload(client, auth, csv_bytes(3))
    assert response.status_code == 202
    job = response.get_json()
    assert (job["source"], job["status"], job["filename"]) == ("csv", "pending", "export.csv")

    worker.run(once=True)
    done = client.get(f"/api/v1/imports/{job['id']}", headers=auth).get_json()
    assert (done["status"], done["imported"], done["total"]) == ("completed", 3, 3)
    assert client.get("/api/v1/me/summary", headers=auth).get_json()["scrobbles"] == 3
    assert [j["id"] for j in client.get("/api/v1/imports", headers=auth).get_json()] == [job["id"]]


def test_forced_format(client, auth):
    response = upload(
        client,
        auth,
        b'[{"artist": "A", "name": "T", "uts": 1600000000}]',
        name="data.txt",
        query="?format=json",
    )
    assert response.get_json()["source"] == "json"


def test_empty_upload_is_a_400(client, auth):
    response = upload(client, auth, b"")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "empty_file"


def test_too_large_upload_is_a_413(client, auth, app):
    saved = app.config["IMPORT_MAX_BYTES"]
    app.config["IMPORT_MAX_BYTES"] = 10
    try:
        response = upload(client, auth, csv_bytes(3))
    finally:
        app.config["IMPORT_MAX_BYTES"] = saved
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "file_too_large"


def test_lastfm_import_without_api_key(client, auth):
    response = client.post("/api/v1/imports/lastfm", json={"username": "rj"}, headers=auth)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "lastfm_not_configured"


def test_cancel(client, auth):
    job = upload(client, auth, csv_bytes(1)).get_json()
    cancelled = client.post(f"/api/v1/imports/{job['id']}/cancel", headers=auth).get_json()
    assert cancelled["status"] == "cancelled"


def test_other_users_imports_are_hidden(client, auth, make_user):
    from scrobbler.services.accounts import issue_ui_token

    job = upload(client, auth, csv_bytes(1)).get_json()
    other = {"Authorization": f"Bearer {issue_ui_token(make_user(username='mallory'))}"}
    assert client.get(f"/api/v1/imports/{job['id']}", headers=other).status_code == 404
    assert client.post(f"/api/v1/imports/{job['id']}/cancel", headers=other).status_code == 404
    assert client.get("/api/v1/imports", headers=other).get_json() == []


@pytest.mark.parametrize("path", ["/api/v1/imports", "/api/v1/imports/options"])
def test_imports_need_auth(client, path):
    assert client.get(path).status_code == 401
