import json
import pathlib
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
    "Boom Album": "rg-boom",  # MB match, but Cover Art Archive 500s on HEAD
    "Download Fail Album": "rg-dl-fail",  # HEAD succeeds, but the GET download 500s
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

    if path == "/release-group/rg-dl-fail/front-500":
        if method == "HEAD":
            start_response("200 OK", [("Content-Type", "image/jpeg")])
            return []
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
def art_config(app, fake_art_api, tmp_path):
    keys = (
        "MUSICBRAINZ_API_URL",
        "COVERART_API_URL",
        "MUSICBRAINZ_REQUEST_INTERVAL",
        "MUSICBRAINZ_RETRIES",
        "ART_STORAGE_DIR",
    )
    saved = {k: app.config[k] for k in keys}
    app.config.update(
        MUSICBRAINZ_API_URL=fake_art_api,
        COVERART_API_URL=fake_art_api,
        MUSICBRAINZ_REQUEST_INTERVAL=0,
        MUSICBRAINZ_RETRIES=0,
        ART_STORAGE_DIR=str(tmp_path),
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


# --- lookup() (still live-resolving: used by ActivityPub posting, out of scope here) -----------


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


# --- fetch_image() -------------------------------------------------------------------------


def test_fetch_image_streams_the_bytes(art_config):
    result = art.lookup("Radiohead", "In Rainbows")
    image = art.fetch_image(result.image_url)
    assert image.body == b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    assert image.content_type == "image/jpeg"


def test_fetch_image_raises_on_failure(art_config):
    with pytest.raises(art.ArtFetchFailed):
        art.fetch_image("http://127.0.0.1:1/nope.jpg")


# --- get_cached() / want() / requeue() / read_file() ----------------------------------------


def test_get_cached_returns_none_for_unseen_album(art_config):
    assert art.get_cached("Nobody", "Nothing") is None


def test_get_cached_is_case_insensitive(art_config):
    insert("Radiohead", "In Rainbows", "found", image_url="https://example.com/a.jpg")
    assert art.get_cached("radiohead", "in rainbows").image_url == "https://example.com/a.jpg"


def test_want_creates_a_pending_row(art_config):
    art.want("New Artist", "New Album")
    assert row("New Artist", "New Album").status == "pending"


def test_want_is_a_noop_if_a_row_already_exists(art_config):
    insert("Radiohead", "In Rainbows", "found", image_url="https://example.com/a.jpg")
    art.want("Radiohead", "In Rainbows")
    assert row("Radiohead", "In Rainbows").status == "found"  # untouched, not reset to pending


def test_read_file_returns_none_when_not_downloaded(art_config):
    assert art.read_file(999_999) is None


def test_read_file_returns_the_bytes(app, art_config):
    path = pathlib.Path(app.config["ART_STORAGE_DIR"]) / "123.jpg"
    path.write_bytes(b"hello")
    assert art.read_file(123) == b"hello"


def test_requeue_marks_a_row_pending(art_config):
    insert("Someone", "Old Album", "found", image_url="https://example.com/a.jpg")
    art.requeue(row("Someone", "Old Album"))
    assert row("Someone", "Old Album").status == "pending"


# --- resolve_one_pending() (the worker task) ------------------------------------------------


def test_resolve_one_pending_resolves_and_downloads(art_config):
    art.want("Radiohead", "In Rainbows")
    assert art.resolve_one_pending() is True
    cached = row("Radiohead", "In Rainbows")
    assert cached.status == "found"
    assert art.read_file(cached.id) == b"\xff\xd8\xff\xe0fake-jpeg-bytes"


def test_resolve_one_pending_handles_not_found(art_config):
    art.want("Nobody", "Nothing")
    assert art.resolve_one_pending() is True
    assert row("Nobody", "Nothing").status == "not_found"


def test_resolve_one_pending_handles_upstream_error(art_config):
    art.want("Someone", "Boom Album")
    assert art.resolve_one_pending() is True
    cached = row("Someone", "Boom Album")
    assert cached.status == "error"
    assert cached.error_code


def test_resolve_one_pending_download_failure_is_cached_as_error(art_config):
    art.want("Someone", "Download Fail Album")
    assert art.resolve_one_pending() is True
    cached = row("Someone", "Download Fail Album")
    assert cached.status == "error"  # not left "found" with no file on disk
    assert art.read_file(cached.id) is None


def test_resolve_one_pending_returns_false_when_nothing_to_do(art_config):
    assert art.resolve_one_pending() is False


def test_resolve_one_pending_picks_up_stale_not_found(art_config):
    insert("Radiohead", "In Rainbows", "not_found", days_ago=31)
    assert art.resolve_one_pending() is True
    assert row("Radiohead", "In Rainbows").status == "found"


def test_resolve_one_pending_picks_up_stale_error(art_config):
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
    assert art.resolve_one_pending() is True
    assert row("Radiohead", "In Rainbows").status == "found"


def test_resolve_one_pending_ignores_fresh_not_found(art_config):
    insert("Radiohead", "In Rainbows", "not_found", days_ago=1)
    assert art.resolve_one_pending() is False


# --- GET /api/v1/art/album -------------------------------------------------------------------


def test_album_art_requires_auth(client):
    assert client.get("/api/v1/art/album?artist=A&album=B").status_code == 401


def test_album_art_requires_artist_and_album(client, auth):
    assert client.get("/api/v1/art/album?artist=A", headers=auth).status_code == 422
    assert client.get("/api/v1/art/album?album=B", headers=auth).status_code == 422


def test_album_art_404s_and_queues_an_unseen_album(client, auth, art_config):
    response = client.get("/api/v1/art/album?artist=New+Artist&album=New+Album", headers=auth)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "no_album_art"
    assert row("New Artist", "New Album").status == "pending"


def test_album_art_never_calls_the_network(client, auth, app, art_config):
    # Point at a port nothing listens on: the endpoint must still respond (fast, 404)
    # without ever trying to reach it — proof it's a cache-only read.
    app.config.update(
        MUSICBRAINZ_API_URL="http://127.0.0.1:1/", COVERART_API_URL="http://127.0.0.1:1/"
    )
    response = client.get("/api/v1/art/album?artist=Another&album=Album", headers=auth)
    assert response.status_code == 404


def test_album_art_404s_while_pending(client, auth, art_config):
    art.want("Radiohead", "In Rainbows")
    response = client.get("/api/v1/art/album?artist=Radiohead&album=In+Rainbows", headers=auth)
    assert response.status_code == 404


def test_album_art_returns_the_image_once_resolved(client, auth, art_config):
    art.want("Radiohead", "In Rainbows")
    art.resolve_one_pending()
    response = client.get("/api/v1/art/album?artist=Radiohead&album=In+Rainbows", headers=auth)
    assert response.status_code == 200
    assert response.data == b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    assert response.content_type == "image/jpeg"
    assert response.headers["Cache-Control"] == "private, max-age=604800"


def test_album_art_404s_when_resolved_not_found(client, auth, art_config):
    art.want("Nobody", "Nothing")
    art.resolve_one_pending()
    response = client.get("/api/v1/art/album?artist=Nobody&album=Nothing", headers=auth)
    assert response.status_code == 404


def test_album_art_requeues_a_found_row_with_a_missing_file(client, auth, art_config):
    insert("Legacy", "Album", "found", image_url="https://example.com/old.jpg")
    response = client.get("/api/v1/art/album?artist=Legacy&album=Album", headers=auth)
    assert response.status_code == 404
    assert row("Legacy", "Album").status == "pending"  # requeued for the worker to redo


# --- metrics ------------------------------------------------------------------------------


def test_worker_counts_resolutions_and_times_upstreams(art_config, metric_delta):
    found = metric_delta("scrobbler_art_resolutions_total", source="worker", result="found")
    resolve = metric_delta("scrobbler_art_worker_resolve_seconds_count")
    image = metric_delta("scrobbler_art_image_bytes_count")
    musicbrainz = metric_delta(
        "scrobbler_art_upstream_seconds_count", service="musicbrainz", outcome="ok"
    )
    download = metric_delta(
        "scrobbler_art_upstream_seconds_count", service="download", outcome="ok"
    )
    art.want("Radiohead", "In Rainbows")
    art.resolve_one_pending()
    assert (found.delta, resolve.delta, image.delta) == (1, 1, 1)
    assert musicbrainz.delta == 1
    assert download.delta == 1


def test_worker_counts_download_failure_as_one_error(art_config, metric_delta):
    errors = metric_delta("scrobbler_art_resolutions_total", source="worker", result="error")
    found = metric_delta("scrobbler_art_resolutions_total", source="worker", result="found")
    failed = metric_delta(
        "scrobbler_art_upstream_errors_total", service="download", error="HTTPError"
    )
    art.want("Someone", "Download Fail Album")
    art.resolve_one_pending()
    assert (errors.delta, found.delta) == (1, 0)
    assert failed.delta == 1


def test_endpoint_counts_queued_pending_and_unavailable(client, auth, art_config, metric_delta):
    queued = metric_delta("scrobbler_art_requests_total", result="queued")
    pending = metric_delta("scrobbler_art_requests_total", result="pending")
    unavailable = metric_delta("scrobbler_art_requests_total", result="unavailable")
    url = "/api/v1/art/album?artist=Nobody&album=Nothing"
    client.get(url, headers=auth)
    client.get(url, headers=auth)
    art.resolve_one_pending()
    client.get(url, headers=auth)
    assert (queued.delta, pending.delta, unavailable.delta) == (1, 1, 1)


def test_endpoint_counts_served(client, auth, art_config, metric_delta):
    served = metric_delta("scrobbler_art_requests_total", result="served")
    url = "/api/v1/art/album?artist=Radiohead&album=In+Rainbows"
    client.get(url, headers=auth)
    art.resolve_one_pending()
    assert client.get(url, headers=auth).status_code == 200
    assert served.delta == 1
