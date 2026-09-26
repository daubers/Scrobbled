"""Now playing end to end: publishing/now_playing.py against a real database."""

from datetime import UTC, datetime, timedelta

import pytest

from scrobbler.extensions import db
from scrobbler.federation import sharing
from scrobbler.federation.models import FederationActivity, FederationNowPlaying, FederationPost
from scrobbler.federation.publishing import now_playing
from scrobbler.models import NowPlaying
from scrobbler.services.scrobbles import TrackInput, update_now_playing


def play(user, artist="Radiohead", track="Reckoner") -> datetime:
    """Sets the track as playing (started_at = now) and returns that now, so tests
    always measure elapsed/throttle time relative to when it actually started, not a
    fixed constant that would drift from real test-execution time."""
    update_now_playing(user, TrackInput(artist=artist, track=track))
    return datetime.now(UTC)


def stop_playing(user):
    """Expire (rather than delete) the row, matching how it naturally goes away."""
    db.session.get(NowPlaying, user.id).expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.session.commit()


def state_for(user_id):
    return db.session.get(FederationNowPlaying, user_id)


def updates_for(user_id):
    return db.session.scalars(
        db.select(FederationActivity).filter_by(user_id=user_id, activity_type="Update")
    ).all()


def posts_for(user_id):
    return db.session.scalars(
        db.select(FederationPost).filter_by(user_id=user_id, kind=now_playing.KIND)
    ).all()


@pytest.fixture
def profile_user(fed_ctx, user):
    sharing.update(user.id, {"enabled": True, "now_playing_mode": "profile"})
    return user


@pytest.fixture
def posting_user(fed_ctx, user):
    sharing.update(user.id, {"enabled": True, "now_playing_mode": "posts"})
    return user


def test_off_mode_does_nothing(fed_ctx, user):
    sharing.update(user.id, {"enabled": True})  # now_playing_mode defaults to off
    now = play(user)
    result = now_playing.check_and_post(user.id, now)
    assert result == {"profile": "off", "post": "off"}
    assert state_for(user.id) is None
    assert updates_for(user.id) == []


def test_profile_update_sent_on_a_genuine_change(fed_ctx, profile_user, metric_delta):
    now = play(profile_user)
    sent = metric_delta("scrobbler_federation_profile_updates_total", result="sent")
    result = now_playing.check_and_post(profile_user.id, now)
    assert result["profile"] == "sent"
    [activity] = updates_for(profile_user.id)
    assert activity.document["object"]["attachment"] == [
        {"type": "PropertyValue", "name": "Now playing", "value": "Reckoner by Radiohead"}
    ]
    assert sent.delta == 1


def test_profile_update_is_a_noop_when_nothing_changed(fed_ctx, profile_user):
    now = play(profile_user)
    now_playing.check_and_post(profile_user.id, now)
    result = now_playing.check_and_post(profile_user.id, now + timedelta(seconds=30))
    assert result["profile"] == "unchanged"
    assert len(updates_for(profile_user.id)) == 1  # not sent again


def test_profile_update_throttled_within_5_minutes(fed_ctx, profile_user, metric_delta):
    now = play(profile_user, track="Reckoner")
    now_playing.check_and_post(profile_user.id, now)
    play(profile_user, track="Karma Police")  # a genuine change
    throttled = metric_delta("scrobbler_federation_profile_updates_total", result="throttled")
    result = now_playing.check_and_post(profile_user.id, now + timedelta(minutes=2))
    assert result["profile"] == "throttled"
    assert len(updates_for(profile_user.id)) == 1  # still just the first
    assert throttled.delta == 1


def test_profile_update_sent_again_once_the_throttle_clears(fed_ctx, profile_user):
    now = play(profile_user, track="Reckoner")
    now_playing.check_and_post(profile_user.id, now)
    play(profile_user, track="Karma Police")
    result = now_playing.check_and_post(profile_user.id, now + timedelta(minutes=6))
    assert result["profile"] == "sent"
    assert len(updates_for(profile_user.id)) == 2


def test_profile_cleared_when_playback_stops(fed_ctx, profile_user):
    now = play(profile_user)
    now_playing.check_and_post(profile_user.id, now)
    stop_playing(profile_user)
    result = now_playing.check_and_post(profile_user.id, now + timedelta(minutes=6))
    assert result["profile"] == "sent"
    [_, cleared] = updates_for(profile_user.id)
    assert cleared.document["object"]["attachment"] == []


def test_profile_mode_never_posts(fed_ctx, profile_user):
    now = play(profile_user)
    result = now_playing.check_and_post(profile_user.id, now + timedelta(seconds=31))
    assert result["post"] == "off"
    assert posts_for(profile_user.id) == []


def test_post_needs_at_least_30_seconds_played(fed_ctx, posting_user):
    now = play(posting_user)
    result = now_playing.check_and_post(posting_user.id, now + timedelta(seconds=10))
    assert result["post"] == "none"
    assert posts_for(posting_user.id) == []


def test_post_sent_after_30_seconds(fed_ctx, posting_user):
    now = play(posting_user)
    result = now_playing.check_and_post(posting_user.id, now + timedelta(seconds=31))
    assert result["post"] == "posted"
    [post] = posts_for(posting_user.id)
    assert "Now playing: Reckoner by Radiohead" in post.content_text
    activity = db.session.get(FederationActivity, post.activity_id)
    assert activity.activity_type == "Create"
    assert activity.document["object"]["tag"] == [
        {"type": "Hashtag", "name": "#Scrobbler"},
        {"type": "Hashtag", "name": "#NowPlaying"},
    ]


def test_post_throttled_within_30_minutes(fed_ctx, posting_user):
    now = play(posting_user, track="Reckoner")
    now_playing.check_and_post(posting_user.id, now + timedelta(seconds=31))
    play(posting_user, track="Karma Police")
    result = now_playing.check_and_post(posting_user.id, now + timedelta(minutes=10))
    assert result["post"] == "none"
    assert len(posts_for(posting_user.id)) == 1


def test_previous_post_deleted_when_a_new_one_goes_out(fed_ctx, posting_user):
    now = play(posting_user, track="Reckoner")
    now_playing.check_and_post(posting_user.id, now + timedelta(seconds=31))
    [first] = posts_for(posting_user.id)
    assert first.deleted_at is None

    play(posting_user, track="Karma Police")
    now_playing.check_and_post(posting_user.id, now + timedelta(minutes=31))

    assert db.session.get(FederationPost, first.id).deleted_at is not None
    live = [p for p in posts_for(posting_user.id) if p.deleted_at is None]
    assert len(live) == 1
    assert "Karma Police" in live[0].content_text


def test_stopping_does_not_post_or_delete(fed_ctx, posting_user):
    now = play(posting_user)
    now_playing.check_and_post(posting_user.id, now + timedelta(seconds=31))
    stop_playing(posting_user)
    result = now_playing.check_and_post(posting_user.id, now + timedelta(minutes=35))
    assert result["post"] == "none"
    live = [p for p in posts_for(posting_user.id) if p.deleted_at is None]
    assert len(live) == 1  # the one post from before stopping is left alone


def test_check_all_covers_every_user_with_now_playing_on(fed_ctx, make_user):
    alice = make_user(username="alice_np")
    bob = make_user(username="bob_np")
    sharing.update(alice.id, {"enabled": True, "now_playing_mode": "profile"})
    sharing.update(bob.id, {"enabled": True})  # stays off
    play(alice)
    now = play(bob)

    now_playing.check_all(now)

    assert len(updates_for(alice.id)) == 1
    assert updates_for(bob.id) == []


def test_check_all_isolates_one_users_failure(fed_ctx, make_user, monkeypatch):
    alice = make_user(username="alice_np2")
    sharing.update(alice.id, {"enabled": True, "now_playing_mode": "profile"})
    now = play(alice)

    def boom(user_id, now_utc=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(now_playing, "check_and_post", boom)
    now_playing.check_all(now)  # doesn't raise
    assert state_for(alice.id) is None  # never got past the monkeypatched call


def test_the_task_is_registered_when_federation_is_on(fed_app):
    from scrobbler import worker

    assert "federation.now_playing" in worker.registered(fed_app)


def test_the_task_is_not_registered_when_federation_is_off(app):
    from scrobbler import worker

    assert "federation.now_playing" not in worker.registered(app)
