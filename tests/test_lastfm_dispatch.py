import xml.etree.ElementTree as ET

import pytest

from conftest import signed
from scrobbler.lastfm.registry import METHODS, Param, lastfm_method
from scrobbler.lastfm.responses import El


@pytest.fixture(autouse=True)
def _test_methods():
    @lastfm_method("test.echo", summary="Echo", params=(Param("word", "A word", required=True),))
    def echo(call):
        return El.of("echo", El.text_el("word", call.get("word"), lang="en"))

    @lastfm_method("test.signed", summary="Signed", signed=True)
    def signed_method(call):
        return El("signed")

    @lastfm_method("test.whoami", summary="Session", signed=True, session=True)
    def whoami(call):
        return El.of("user", El.text_el("name", call.user.username))

    yield
    for name in ("test.echo", "test.signed", "test.whoami"):
        METHODS.pop(name)


def lfm(response):
    root = ET.fromstring(response.data)
    assert root.tag == "lfm"
    return root


def test_unknown_method_is_error_3(client, api_app):
    response = client.get("/2.0/", query_string={"method": "nope.nope", "api_key": api_app.api_key})
    root = lfm(response)
    assert root.get("status") == "failed"
    assert root.find("error").get("code") == "3"
    assert response.status_code == 400


def test_missing_api_key_is_error_6(client):
    root = lfm(client.get("/2.0/?method=test.echo&word=hi"))
    assert root.find("error").get("code") == "6"


def test_unknown_api_key_is_error_10(client, metric_delta):
    failures = metric_delta("scrobbler_auth_failures_total", reason="bad_api_key")
    root = lfm(client.get("/2.0/?method=test.echo&word=hi&api_key=nope"))
    assert root.find("error").get("code") == "10"
    assert failures.delta == 1


def test_missing_required_param_is_error_6(client, api_app):
    root = lfm(client.get(f"/2.0/?method=test.echo&api_key={api_app.api_key}"))
    assert root.find("error").get("code") == "6"
    assert "word" in root.find("error").text


def test_xml_response(client, api_app):
    response = client.get(f"/2.0/?method=TEST.ECHO&word=hi&api_key={api_app.api_key}")
    root = lfm(response)
    assert response.status_code == 200
    assert root.get("status") == "ok"
    word = root.find("echo/word")
    assert (word.text, word.get("lang")) == ("hi", "en")


def test_json_response_uses_lastfm_conventions(client, api_app):
    response = client.post(
        "/2.0/",
        data={"method": "test.echo", "word": "hi", "api_key": api_app.api_key, "format": "json"},
    )
    assert response.get_json() == {"echo": {"word": {"lang": "en", "#text": "hi"}}}


def test_json_error_shape(client):
    response = client.get("/2.0?method=nope&format=json")
    assert response.get_json() == {
        "error": 3,
        "message": "Invalid Method - No method with that name in this package",
    }


def test_signed_method_rejects_bad_signature(client, api_app, metric_delta):
    errors = metric_delta("scrobbler_lastfm_errors_total", lastfm_method="test.signed", code="13")
    params = {"method": "test.signed", "api_key": api_app.api_key, "api_sig": "0" * 32}
    root = lfm(client.post("/2.0/", data=params))
    assert root.find("error").get("code") == "13"
    assert errors.delta == 1


def test_signed_method_accepts_good_signature(client, api_app):
    params = signed({"method": "test.signed", "api_key": api_app.api_key}, api_app.shared_secret)
    assert lfm(client.post("/2.0/", data={**params, "format": "xml"})).get("status") == "ok"


def test_session_method_resolves_user(client, api_app, session_key, user):
    params = signed(
        {"method": "test.whoami", "api_key": api_app.api_key, "sk": session_key},
        api_app.shared_secret,
    )
    assert lfm(client.post("/2.0/", data=params)).find("user/name").text == user.username


def test_session_key_from_another_app_is_rejected(client, make_app, make_session, user):
    app_a, app_b = make_app(owner=user), make_app(owner=user)
    sk = make_session(user, app_a).session_key
    params = signed(
        {"method": "test.whoami", "api_key": app_b.api_key, "sk": sk}, app_b.shared_secret
    )
    assert lfm(client.post("/2.0/", data=params)).find("error").get("code") == "9"


def test_unknown_methods_share_one_metric_label(client, metric_delta):
    unknown = metric_delta(
        "scrobbler_lastfm_requests_total", lastfm_method="unknown", format="xml", outcome="error"
    )
    for name in ("a.b", "c.d", "e.f"):
        client.get(f"/2.0/?method={name}")
    assert unknown.delta == 3
