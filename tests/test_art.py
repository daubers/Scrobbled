import json
import threading
import urllib.parse
from datetime import UTC, datetime, timedelta

import pytest
from werkzeug.serving import make_server

from scrobbler.extensions import db
from scrobbler.models import AlbumArt
from scrobbler.services import art

# Canned release-group matches, keyed by a substring of the Lucene search query we send.
_RELEASE_GROUPS = {
    "In Rainbows": "rg-found",  # has art
    "No Art Album": "rg-no-art",  # MB match, but Cover Art Archive has nothing
    "Boom Album": "rg-boom",  # MB match, but Cover Art Archive 500s
}


def _fake_musicbrainz_caa(environ, start_response):
    method, path = environ["REQUEST_METHOD"], environ["PATH_INFO"]

    if path == "/release-group/" and method == "GET":
        query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))["query"][0]
        if "Query Boom" in query:
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"boom"]
        match = next((v for k, v in _RELEASE_GROUPS.items() if k in query), None)
        groups = [{"id": match}] if match else []
        body = json.dumps({"release-groups": groups}).encode()
        start_response(
            "200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
        )
        return [body]

    if path == "/release-group/rg-found/front-500":
        body = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
        headers = [("Content-Type", "image/jpeg"), ("Content-Length", str(len(body)))]
        start_response("200 OK", headers)
        return [] if method == "HEAD" else [body]

    if path == "/release-group/rg-boom/front-500":
        start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
        return [b"boom"]

    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"not found"]


@pytest.fixture(scope="module")
def fake_art_api():
    server = make_server("127.0.0.1", 0, _fake_musicbrainz_caa, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture
def art_config(app, fake_art_api):
    keys = (
        "MUSICBRAINZ_API_URL",
        "COVERART_API_URL",
        "MUSICBRAINZ_REQUEST_INTERVAL",
        "MUSICBRAINZ_RETRIES",
    )
    saved = {k: app.config[k] for k in keys}
    app.config.update(
        MUSICBRAINZ_API_URL=fake_art_api,
        COVERART_API_URL=fake_art_api,
        MUSICBRAINZ_REQUEST_INTERVAL=0,
        MUSICBRAINZ_RETRIES=0,
    )
    yield
    app.config.update(saved)


def row(artist, album):
    return db.session.scalar(
        db.select(AlbumArt).where(
            db.func.lower(AlbumArt.artist) == artist.lower(),
            db.func.lower(AlbumArt.album) == album.lower(),
        )
    )


def insert(artist, album, status, days_ago=0.0, image_url=None):
    db.session.add(
        AlbumArt(
            artist=artist,
            album=album,
            status=status,
            image_url=image_url,
            checked_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    db.session.commit()


# --- lookup() ------------------------------------------------------------------------------


def test_lookup_finds_art(art_config):
    result = art.lookup("Radiohead", "In Rainbows")
    assert result.status == "found"
    assert result.image_url.endswith("/release-group/rg-found/front-500")
    assert row("Radiohead", "In Rainbows").status == "found"


def test_lookup_no_musicbrainz_match_is_not_found(art_config):
    result = art.lookup("Nobody", "Nothing At All")
    assert result == art.ArtResult("not_found")


def test_lookup_musicbrainz_match_but_no_cover_art_is_not_found(art_config):
    result = art.lookup("Someone", "No Art Album")
    assert result == art.ArtResult("not_found")


def test_lookup_upstream_error_is_cached_not_raised(app, art_config):
    result = art.lookup("Someone", "Boom Album")
    assert result.status == "error"
    assert row("Someone", "Boom Album").error_code


def test_lookup_search_error_is_cached_not_raised(app, art_config):
    result = art.lookup("Someone", "Query Boom")
    assert result.status == "error"


def test_lookup_is_case_insensitive_and_cached(app, art_config):
    art.lookup("Radiohead", "In Rainbows")
    # Point at a port nothing listens on: if the different-cased call weren't a cache hit on
    # the same row, it would fail to reach the network and come back as "error".
    app.config.update(
        MUSICBRAINZ_API_URL="http://127.0.0.1:1/", COVERART_API_URL="http://127.0.0.1:1/"
    )
    result = art.lookup("radiohead", "in rainbows")
    assert result.status == "found"


def test_lookup_fresh_cache_hit_skips_the_network(app, art_config, fake_art_api):
    insert("Fresh Artist", "Fresh Album", "not_found", days_ago=0)
    # If this weren't a cache hit, the (nonexistent) search term would still return "not_found"
    # from the live fake server too — so instead prove no network call happened by pointing at
    # a port nothing listens on and confirming the result is still served from the cache.
    app.config.update(
        MUSICBRAINZ_API_URL="http://127.0.0.1:1/", COVERART_API_URL="http://127.0.0.1:1/"
    )
    result = art.lookup("Fresh Artist", "Fresh Album")
    assert result.status == "not_found"


def test_stale_not_found_row_is_rechecked(app, art_config):
    insert("Radiohead", "In Rainbows", "not_found", days_ago=31)
    result = art.lookup("Radiohead", "In Rainbows")
    assert result.status == "found"  # the live fake server actually has art for this album


def test_fresh_not_found_row_is_not_rechecked(app, art_config):
    insert("Radiohead", "In Rainbows", "not_found", days_ago=1)
    result = art.lookup("Radiohead", "In Rainbows")
    assert result.status == "not_found"  # cache hit, even though the live server has art


def test_stale_error_row_is_rechecked(app, art_config):
    db.session.add(
        AlbumArt(
            artist="Radiohead",
            album="In Rainbows",
            status="error",
            error_code="TimeoutError",
            checked_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    db.session.commit()
    result = art.lookup("Radiohead", "In Rainbows")
    assert result.status == "found"


def test_found_row_is_never_rechecked(app, art_config):
    insert(
        "Someone", "No Art Album", "found", days_ago=9999, image_url="https://example.com/old.jpg"
    )
    result = art.lookup("Someone", "No Art Album")
    assert result.image_url == "https://example.com/old.jpg"


# --- fetch_image() ---------------------------------------------------------------------------


def test_fetch_image_streams_the_bytes(art_config):
    result = art.lookup("Radiohead", "In Rainbows")
    image = art.fetch_image(result.image_url)
    assert image.body == b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    assert image.content_type == "image/jpeg"


def test_fetch_image_raises_on_failure(art_config):
    with pytest.raises(art.ArtFetchFailed):
        art.fetch_image("http://127.0.0.1:1/nope.jpg")


# --- GET /api/v1/art/album ---------------------------------------------------------------------


def test_album_art_requires_auth(client):
    assert client.get("/api/v1/art/album?artist=A&album=B").status_code == 401


def test_album_art_returns_the_image(client, auth, art_config):
    response = client.get("/api/v1/art/album?artist=Radiohead&album=In+Rainbows", headers=auth)
    assert response.status_code == 200
    assert response.data == b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    assert response.content_type == "image/jpeg"
    assert response.headers["Cache-Control"] == "private, max-age=604800"


def test_album_art_404s_when_not_found(client, auth, art_config):
    response = client.get("/api/v1/art/album?artist=Nobody&album=Nothing", headers=auth)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "no_album_art"


def test_album_art_502s_on_upstream_error(client, auth, art_config):
    response = client.get("/api/v1/art/album?artist=Someone&album=Boom+Album", headers=auth)
    assert response.status_code == 502
    assert response.get_json()["error"]["code"] == "art_upstream_unavailable"


def test_album_art_requires_artist_and_album(client, auth):
    assert client.get("/api/v1/art/album?artist=A", headers=auth).status_code == 422
    assert client.get("/api/v1/art/album?album=B", headers=auth).status_code == 422
