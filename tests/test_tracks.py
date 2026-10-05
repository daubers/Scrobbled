from datetime import UTC, datetime, timedelta

import pytest

from scrobbler.extensions import db
from scrobbler.models import Scrobble

NOW = datetime.now(UTC)


def add(
    user,
    artist,
    track,
    album=None,
    album_artist=None,
    track_number=None,
    duration=None,
    mbid=None,
    days_ago=0.0,
):
    db.session.add(
        Scrobble(
            user_id=user.id,
            artist=artist,
            track=track,
            album=album,
            album_artist=album_artist,
            track_number=track_number,
            duration=duration,
            mbid=mbid,
            played_at=NOW - timedelta(days=days_ago),
        )
    )


@pytest.fixture
def history(user, make_user):
    # Radiohead: 3 recent plays of Reckoner (mixed capitalisation) + 1 old Nude
    add(user, "Radiohead", "Reckoner", "In Rainbows", duration=290, mbid="abc", days_ago=0.1)
    add(user, "radiohead", "reckoner", "in rainbows", duration=290, mbid="abc", days_ago=0.2)
    add(user, "Radiohead", "Reckoner", None, duration=290, mbid=None, days_ago=0.3)
    add(user, "Radiohead", "Nude", "In Rainbows", days_ago=100)
    # Someone else's plays must never show up
    other = make_user(username="mallory")
    add(other, "Radiohead", "Reckoner", "In Rainbows", days_ago=0.1)
    db.session.commit()
    return user


def test_search_matches_track_name_case_insensitively(client, auth, history):
    body = client.get("/api/v1/me/tracks/search?q=reck", headers=auth).get_json()
    assert [(r["track"], r["artist"], r["playcount"]) for r in body] == [
        ("Reckoner", "Radiohead", 3)
    ]


def test_search_matches_artist_name(client, auth, history):
    body = client.get("/api/v1/me/tracks/search?q=radio", headers=auth).get_json()
    tracks = {r["track"] for r in body}
    assert tracks == {"Reckoner", "Nude"}


def test_search_excludes_other_users_and_respects_limit(client, auth, history):
    body = client.get("/api/v1/me/tracks/search?q=e&limit=1", headers=auth).get_json()
    assert len(body) == 1


def test_search_requires_a_query(client, auth):
    assert client.get("/api/v1/me/tracks/search", headers=auth).status_code == 422


def test_detail_aggregates_metadata_and_merges_capitalisation(client, auth, history):
    body = client.get(
        "/api/v1/me/tracks?artist=radiohead&track=RECKONER", headers=auth
    ).get_json()
    assert body["artist"] == "Radiohead"
    assert body["track"] == "Reckoner"
    metadata = body["metadata"]
    assert metadata["album"] == "In Rainbows"  # most common non-null value
    assert metadata["duration"] == 290
    assert metadata["mbid"] == "abc"  # most common non-null value
    assert metadata["playcount"] == 3
    assert metadata["first_played_at"] is not None
    assert metadata["last_played_at"] is not None
    assert [s["artist"] for s in body["items"]] == ["Radiohead", "radiohead", "Radiohead"]
    assert body["total"] == 3


def test_detail_unknown_track_returns_404(client, auth, history):
    response = client.get("/api/v1/me/tracks?artist=Nobody&track=Nothing", headers=auth)
    assert response.status_code == 404


def test_detail_requires_auth(client):
    assert client.get("/api/v1/me/tracks?artist=A&track=B").status_code == 401
    assert client.get("/api/v1/me/tracks/search?q=a").status_code == 401


def test_detail_pagination(client, auth, history):
    first = client.get(
        "/api/v1/me/tracks?artist=Radiohead&track=Reckoner&limit=2&page=1", headers=auth
    ).get_json()
    second = client.get(
        "/api/v1/me/tracks?artist=Radiohead&track=Reckoner&limit=2&page=2", headers=auth
    ).get_json()
    assert (first["total"], first["total_pages"]) == (3, 2)
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
