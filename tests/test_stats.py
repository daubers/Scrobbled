import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pytest

from scrobbler.extensions import db
from scrobbler.models import Scrobble
from scrobbler.services import scrobbles
from scrobbler.services.scrobbles import TrackInput

NOW = datetime.now(UTC)


def add(user, artist, track, album=None, days_ago=0.0, album_artist=None, duration=None):
    db.session.add(
        Scrobble(
            user_id=user.id,
            artist=artist,
            track=track,
            album=album,
            album_artist=album_artist,
            duration=duration,
            played_at=NOW - timedelta(days=days_ago),
        )
    )


@pytest.fixture
def history(user, make_user):
    # Radiohead: 3 recent plays (with mixed capitalisation) + 1 old; Bjork: 2 recent
    add(user, "Radiohead", "Reckoner", "In Rainbows", days_ago=0.1, duration=290)
    add(user, "radiohead", "reckoner", "in rainbows", days_ago=0.2)
    add(user, "Radiohead", "Nude", "In Rainbows", days_ago=0.3)
    add(user, "Radiohead", "Creep", "Pablo Honey", days_ago=100)
    add(user, "Björk", "Joga", "Homogenic", days_ago=1)
    add(user, "Björk", "Hyperballad", None, days_ago=2)
    # Someone else's plays must never show up
    other = make_user(username="mallory")
    add(other, "Nickelback", "Photograph", "All the Right Reasons", days_ago=0.1)
    db.session.commit()
    return user


def lastfm_get(client, api_app, method, fmt="xml", **params):
    response = client.get(
        "/2.0/",
        query_string={"method": method, "api_key": api_app.api_key, "format": fmt, **params},
    )
    return response.get_json() if fmt == "json" else ET.fromstring(response.data)


def test_top_artists_merges_capitalisation_and_respects_period(client, api_app, history):
    root = lastfm_get(client, api_app, "user.getTopArtists", user="alice", period="7day")
    top = root.find("topartists")
    assert top.get("total") == "2"
    assert [(a.find("name").text, a.find("playcount").text, a.get("rank")) for a in top] == [
        ("Radiohead", "3", "1"),
        ("Björk", "2", "2"),
    ]
    overall = lastfm_get(client, api_app, "user.getTopArtists", user="alice")
    assert overall.find("topartists/artist/playcount").text == "4"


def test_top_albums_skip_scrobbles_without_album(client, api_app, history):
    root = lastfm_get(client, api_app, "user.getTopAlbums", user="alice", period="overall")
    albums = [(a.find("name").text, a.find("artist/name").text) for a in root.find("topalbums")]
    assert albums[0] == ("In Rainbows", "Radiohead")
    assert len(albums) == 3


def test_top_tracks_pagination(client, api_app, history):
    first = lastfm_get(client, api_app, "user.getTopTracks", user="alice", limit="2", page="1")
    second = lastfm_get(client, api_app, "user.getTopTracks", user="alice", limit="2", page="2")
    top = first.find("toptracks")
    assert (top.get("total"), top.get("totalPages")) == ("5", "3")
    assert top.find("track/name").text == "Reckoner"
    assert top.find("track/duration").text == "290"
    assert [t.get("rank") for t in second.find("toptracks")] == ["3", "4"]


def test_recent_tracks_include_now_playing_first(client, api_app, history):
    scrobbles.update_now_playing(history, TrackInput(artist="Portishead", track="Roads"))
    root = lastfm_get(client, api_app, "user.getRecentTracks", user="alice", limit="3")
    tracks = root.find("recenttracks").findall("track")
    assert tracks[0].get("nowplaying") == "true"
    assert tracks[0].find("date") is None
    assert [t.find("name").text for t in tracks[1:]] == ["Reckoner", "reckoner", "Nude"]
    assert tracks[1].find("date").get("uts").isdigit()
    assert root.find("recenttracks").get("total") == "6"


def test_recent_tracks_from_to(client, api_app, history):
    frm = int((NOW - timedelta(days=1.5)).timestamp())
    to = int((NOW - timedelta(days=0.25)).timestamp())
    root = lastfm_get(
        client, api_app, "user.getRecentTracks", user="alice", **{"from": frm, "to": to}
    )
    assert [t.find("name").text for t in root.find("recenttracks")] == ["Nude", "Joga"]


def test_recent_tracks_json_track_is_always_a_list(client, api_app, user):
    add(user, "A", "Only", days_ago=0.1)
    db.session.commit()
    body = lastfm_get(client, api_app, "user.getRecentTracks", fmt="json", user="alice")
    assert isinstance(body["recenttracks"]["track"], list)
    assert body["recenttracks"]["@attr"]["total"] == "1"
    assert body["recenttracks"]["track"][0]["artist"] == {"mbid": "", "#text": "A"}


def test_user_info(client, api_app, history):
    root = lastfm_get(client, api_app, "user.getInfo", user="Alice")
    assert root.find("user/name").text == "alice"
    assert root.find("user/playcount").text == "6"
    assert root.find("user/artist_count").text == "2"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"user": "nobody"}, "6"),
        ({"user": "alice", "period": "fortnight"}, "6"),
        ({"user": "alice", "limit": "0"}, "6"),
        ({"user": "alice", "page": "x"}, "6"),
        ({}, "6"),
    ],
)
def test_user_method_parameter_errors(client, api_app, user, params, expected):
    root = lastfm_get(client, api_app, "user.getTopArtists", **params)
    assert root.find("error").get("code") == expected


# --- /api/v1/me ------------------------------------------------------------------


def test_me_recent_and_top(client, auth, history):
    recent = client.get("/api/v1/me/recent?limit=2", headers=auth).get_json()
    assert (recent["total"], recent["total_pages"]) == (6, 3)
    assert [s["track"] for s in recent["items"]] == ["Reckoner", "reckoner"]

    top = client.get("/api/v1/me/top/albums?period=overall", headers=auth).get_json()
    assert top["items"][0] == {
        "rank": 1,
        "name": "In Rainbows",
        "artist": "Radiohead",
        "playcount": 3,
    }

    bad = client.get("/api/v1/me/top/artists?period=forever", headers=auth)
    assert bad.status_code == 422


def test_me_now_playing(client, auth, user):
    assert client.get("/api/v1/me/now-playing", headers=auth).get_json() == {"now_playing": None}
    scrobbles.update_now_playing(user, TrackInput(artist="Portishead", track="Roads", duration=300))
    body = client.get("/api/v1/me/now-playing", headers=auth).get_json()
    assert body["now_playing"]["track"] == "Roads"


def test_me_counts_fill_empty_days(client, auth, history):
    counts = client.get("/api/v1/me/counts?period=7day", headers=auth).get_json()
    assert len(counts) == 8  # 7 days back plus today
    assert sum(c["count"] for c in counts) == 5
    assert counts[-1]["start"] == NOW.date().isoformat()


def test_me_summary(client, auth, history):
    body = client.get("/api/v1/me/summary", headers=auth).get_json()
    assert (body["scrobbles"], body["artists"], body["tracks"]) == (6, 2, 5)


def test_me_requires_auth(client):
    for path in ("recent", "top/artists", "counts", "summary", "now-playing"):
        assert client.get(f"/api/v1/me/{path}").status_code == 401
