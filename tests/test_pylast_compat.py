"""End-to-end compatibility check with pylast, a widely used Last.fm client.

pylast always talks HTTPS, so the app is served over TLS with a throwaway
self-signed certificate that only pylast's SSL context is told to trust.
"""

import shutil
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pylast
import pytest
from werkzeug.serving import make_server

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory):
    directory = tmp_path_factory.mktemp("tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


@pytest.fixture(scope="module")
def https_server(app, tls_files):
    cert, key = tls_files
    server = make_server("127.0.0.1", 0, app, threaded=True, ssl_context=(str(cert), str(key)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    pylast.SSL_CONTEXT.load_verify_locations(cafile=str(cert))
    yield f"127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def network(https_server, api_app):
    return pylast._Network(
        name="Scrobbler",
        homepage=f"https://{https_server}",
        ws_server=(https_server, "/2.0/"),
        api_key=api_app.api_key,
        api_secret=api_app.shared_secret,
        session_key="",
        username="",
        password_hash="",
        domain_names={0: https_server},
        urls={
            "album": "",
            "artist": "",
            "event": "",
            "country": "",
            "playlist": "",
            "tag": "",
            "track": "",
            "group": "",
            "user": "user/%(name)s",
        },
    )


def test_pylast_end_to_end(network, client, auth, user):
    # Desktop auth: pylast fetches a token and builds the approval URL...
    generator = pylast.SessionKeyGenerator(network)
    auth_url = generator.get_web_auth_url()
    assert urlsplit(auth_url).path == "/api/auth/"
    token = parse_qs(urlsplit(auth_url).query)["token"][0]

    # ...the user approves it in the web UI...
    approved = client.post("/api/v1/tokens/approve", json={"token": token}, headers=auth)
    assert approved.status_code == 204

    # ...and pylast exchanges it for a session key.
    network.session_key = generator.get_web_auth_session_key(auth_url)
    assert len(network.session_key) == 32

    network.update_now_playing(
        artist="Radiohead", title="Reckoner", album="In Rainbows", duration=290
    )
    now = int(time.time())
    network.scrobble(artist="Radiohead", title="Nude", timestamp=now - 600, album="In Rainbows")
    network.scrobble_many(
        [
            {"artist": "Björk", "title": "Jóga", "timestamp": now - 1200},
            {"artist": "Portishead", "title": "Roads", "timestamp": now - 1800, "album": "Dummy"},
        ]
    )

    lastfm_user = network.get_user("alice")
    assert lastfm_user.get_now_playing().title == "Reckoner"
    recent = lastfm_user.get_recent_tracks(limit=10, now_playing=False)
    assert [(p.track.artist.name, p.track.title) for p in recent] == [
        ("Radiohead", "Nude"),
        ("Björk", "Jóga"),
        ("Portishead", "Roads"),
    ]
    assert recent[0].album == "In Rainbows"
    assert int(recent[0].timestamp) == now - 600

    top = lastfm_user.get_top_artists(period=pylast.PERIOD_7DAYS)
    assert {item.item.name for item in top} == {"Radiohead", "Björk", "Portishead"}
    assert lastfm_user.get_playcount() == 3


def test_pylast_sees_lastfm_errors(network):
    network.session_key = "0" * 32
    with pytest.raises(pylast.WSError) as error:
        network.scrobble(artist="A", title="T", timestamp=int(time.time()))
    assert error.value.get_id() == "9"
