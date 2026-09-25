from conftest import signed


def test_app_crud(client, auth):
    created = client.post("/api/v1/apps", json={"name": "Player"}, headers=auth)
    assert created.status_code == 201
    app_ = created.get_json()
    assert len(app_["api_key"]) == len(app_["shared_secret"]) == 32

    listed = client.get("/api/v1/apps", headers=auth).get_json()
    assert [a["api_key"] for a in listed] == [app_["api_key"]]

    assert client.delete(f"/api/v1/apps/{app_['api_key']}", headers=auth).status_code == 204
    assert client.get("/api/v1/apps", headers=auth).get_json() == []
    assert client.delete(f"/api/v1/apps/{app_['api_key']}", headers=auth).status_code == 404


def test_cannot_touch_other_users_apps(client, auth, make_user, make_app):
    other = make_app(owner=make_user(username="mallory"))
    assert client.delete(f"/api/v1/apps/{other.api_key}", headers=auth).status_code == 404
    assert client.get("/api/v1/apps", headers=auth).get_json() == []


def test_revoked_session_key_stops_working(client, auth, api_app, session_key):
    sessions = client.get("/api/v1/sessions", headers=auth).get_json()
    assert [s["app_name"] for s in sessions] == [api_app.name]

    assert client.delete(f"/api/v1/sessions/{sessions[0]['id']}", headers=auth).status_code == 204
    params = signed(
        {
            "method": "track.updateNowPlaying",
            "api_key": api_app.api_key,
            "sk": session_key,
            "artist": "A",
            "track": "T",
        },
        api_app.shared_secret,
    )
    response = client.post("/2.0/", data={**params, "format": "json"})
    assert response.get_json()["error"] == 9
