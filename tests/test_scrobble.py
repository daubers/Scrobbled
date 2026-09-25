import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pytest

from conftest import days_ago, signed
from scrobbler.extensions import db
from scrobbler.models import NowPlaying, Scrobble


@pytest.fixture
def lastfm(client, api_app, session_key):
    def call(method, fmt="xml", **params):
        data = signed(
            {"method": method, "api_key": api_app.api_key, "sk": session_key, **params},
            api_app.shared_secret,
        )
        response = client.post("/2.0/", data={**data, "format": fmt})
        return response.get_json() if fmt == "json" else ET.fromstring(response.data)

    return call


def batch(*tracks):
    params = {}
    for i, track in enumerate(tracks):
        params.update({f"{key}[{i}]": str(value) for key, value in track.items()})
    return params


def stored():
    return db.session.scalars(db.select(Scrobble).order_by(Scrobble.played_at)).all()


def test_single_scrobble(lastfm, user):
    ts = days_ago(0.01)
    root = lastfm(
        "track.scrobble",
        **batch(
            {"artist": "Radiohead", "track": "Reckoner", "album": "In Rainbows", "timestamp": ts}
        ),
    )
    scrobbles = root.find("scrobbles")
    assert (scrobbles.get("accepted"), scrobbles.get("ignored")) == ("1", "0")
    assert scrobbles.find("scrobble/track").text == "Reckoner"
    assert scrobbles.find("scrobble/ignoredMessage").get("code") == "0"

    [row] = stored()
    assert (row.user_id, row.artist, row.album) == (user.id, "Radiohead", "In Rainbows")
    assert int(row.played_at.timestamp()) == ts


def test_unindexed_single_scrobble(lastfm):
    root = lastfm("track.scrobble", artist="A", track="T", timestamp=str(days_ago(0.1)))
    assert root.find("scrobbles").get("accepted") == "1"


def test_batch_of_fifty(lastfm, metric_delta):
    sizes = metric_delta("scrobbler_scrobble_batch_size_count")
    start = days_ago(1)
    tracks = [{"artist": "A", "track": f"T{i}", "timestamp": start + i * 200} for i in range(50)]
    root = lastfm("track.scrobble", **batch(*tracks))
    assert root.find("scrobbles").get("accepted") == "50"
    assert len(stored()) == 50
    assert sizes.delta == 1


def test_more_than_fifty_is_rejected(lastfm):
    tracks = [{"artist": "A", "track": f"T{i}", "timestamp": days_ago(1) + i} for i in range(51)]
    assert lastfm("track.scrobble", **batch(*tracks)).find("error").get("code") == "6"


def test_missing_timestamp_is_an_error(lastfm):
    root = lastfm("track.scrobble", **batch({"artist": "A", "track": "T"}))
    assert root.find("error").get("code") == "6"


def test_ignore_rules(lastfm, metric_delta):
    ignored = {
        code: metric_delta("scrobbler_scrobbles_ignored_total", reason=code) for code in "1234"
    }
    accepted = metric_delta("scrobbler_scrobbles_total", result="accepted")
    now = int(time.time())
    root = lastfm(
        "track.scrobble",
        **batch(
            {"artist": "", "track": "T", "timestamp": now - 60},
            {"artist": "A", "track": " ", "timestamp": now - 60},
            {"artist": "A", "track": "Old", "timestamp": days_ago(15)},
            {"artist": "A", "track": "Future", "timestamp": now + 3600},
            {"artist": "A", "track": "Fine", "timestamp": now - 60},
        ),
    )
    scrobbles = root.find("scrobbles")
    assert (scrobbles.get("accepted"), scrobbles.get("ignored")) == ("1", "4")
    codes = [s.find("ignoredMessage").get("code") for s in scrobbles.findall("scrobble")]
    assert codes == ["1", "2", "3", "4", "0"]
    assert [row.track for row in stored()] == ["Fine"]
    assert {code: m.delta for code, m in ignored.items()} == {"1": 1, "2": 1, "3": 1, "4": 1}
    assert accepted.delta == 1


def test_duplicates_are_accepted_but_stored_once(lastfm, metric_delta):
    duplicates = metric_delta("scrobbler_scrobbles_total", result="duplicate")
    track = {"artist": "A", "track": "T", "timestamp": days_ago(0.5)}
    lastfm("track.scrobble", **batch(track))
    root = lastfm("track.scrobble", **batch(track))
    assert root.find("scrobbles").get("accepted") == "1"
    assert len(stored()) == 1
    assert duplicates.delta == 1


def test_json_scrobble_response(lastfm):
    body = lastfm(
        "track.scrobble",
        fmt="json",
        **batch({"artist": "A", "track": "T", "timestamp": days_ago(0.2)}),
    )
    assert body["scrobbles"]["@attr"] == {"accepted": "1", "ignored": "0"}
    assert body["scrobbles"]["scrobble"]["artist"] == {"corrected": "0", "#text": "A"}


def test_now_playing(lastfm, user, metric_delta):
    updates = metric_delta("scrobbler_now_playing_updates_total")
    root = lastfm("track.updateNowPlaying", artist="Radiohead", track="Reckoner", duration="290")
    assert root.find("nowplaying/track").text == "Reckoner"
    row = db.session.get(NowPlaying, user.id)
    assert row.track == "Reckoner"
    assert timedelta(seconds=289) < row.expires_at - row.started_at < timedelta(seconds=291)

    lastfm("track.updateNowPlaying", artist="Radiohead", track="Nude")
    db.session.expire_all()
    assert db.session.get(NowPlaying, user.id).track == "Nude"
    assert updates.delta == 2


def test_now_playing_requires_artist_and_track(lastfm):
    assert lastfm("track.updateNowPlaying", artist="A").find("error").get("code") == "6"


def test_scrobbling_the_current_track_clears_now_playing(lastfm, user):
    lastfm("track.updateNowPlaying", artist="Radiohead", track="Reckoner")
    lastfm(
        "track.scrobble",
        **batch({"artist": "radiohead", "track": "RECKONER", "timestamp": days_ago(0.001)}),
    )
    db.session.expire_all()
    assert db.session.get(NowPlaying, user.id) is None


def test_scrobbling_another_track_keeps_now_playing(lastfm, user):
    lastfm("track.updateNowPlaying", artist="Radiohead", track="Reckoner")
    lastfm(
        "track.scrobble",
        **batch({"artist": "Radiohead", "track": "Nude", "timestamp": days_ago(0.01)}),
    )
    db.session.expire_all()
    assert db.session.get(NowPlaying, user.id) is not None


def test_scrobble_lag_is_recorded(lastfm, metric_delta):
    lag = metric_delta("scrobbler_scrobble_lag_seconds_count")
    lastfm("track.scrobble", **batch({"artist": "A", "track": "T", "timestamp": days_ago(2)}))
    assert lag.delta == 1


def test_expired_now_playing_is_not_returned(app, lastfm, user):
    from scrobbler.services.scrobbles import get_now_playing

    lastfm("track.updateNowPlaying", artist="A", track="T", duration="60")
    assert get_now_playing(user) is not None
    row = db.session.get(NowPlaying, user.id)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.session.commit()
    assert get_now_playing(user) is None
