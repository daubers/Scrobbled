"""Weekly summaries end to end: publishing/weekly.py against a real database."""

from datetime import UTC, datetime

import pytest

from scrobbler.extensions import db
from scrobbler.federation import publishing, sharing
from scrobbler.federation.models import FederationActivity, FederationPost
from scrobbler.federation.publishing import weekly
from scrobbler.models import Scrobble

# 2026-01-05 and 2026-01-12 are Mondays (ISO weeks 2026-W02 and 2026-W03); confirmed in
# test_federation_schedule.py. The gate for W02 (Monday 2026-01-12 09:00 UTC) is when a
# summary of that week becomes postable.
GATE = datetime(2026, 1, 12, 9, 0, tzinfo=UTC)


def played(user, artist, track, when, album=None):
    db.session.add(
        Scrobble(user_id=user.id, artist=artist, track=track, album=album, played_at=when)
    )


def a_scrobble_in_the_target_week(user, hour=12):
    played(user, "Radiohead", "Reckoner", datetime(2026, 1, 6, hour, tzinfo=UTC))


@pytest.fixture
def sharing_settings(fed_ctx, user):
    _, _ = sharing.update(user.id, {"enabled": True})
    return sharing.settings_for(user.id)


def posts_for(user_id):
    return db.session.scalars(db.select(FederationPost).filter_by(user_id=user_id)).all()


def test_an_empty_week_is_not_posted(fed_ctx, user, sharing_settings):
    outcome = weekly.check_and_post(sharing_settings, GATE)
    assert outcome == "skipped_empty"
    assert posts_for(user.id) == []


def test_a_week_with_plays_is_posted(fed_ctx, user, sharing_settings, metric_delta):
    a_scrobble_in_the_target_week(user)
    a_scrobble_in_the_target_week(user, hour=13)
    posted = metric_delta("scrobbler_federation_weekly_total", result="posted")
    outcome = weekly.check_and_post(sharing_settings, GATE)
    assert outcome == "posted"
    [post] = posts_for(user.id)
    assert (post.kind, post.key) == ("weekly", "weekly:2026-W02")
    assert "2 plays" in post.content_text
    activity = db.session.get(FederationActivity, post.activity_id)
    assert activity.activity_type == "Create"
    assert posted.delta == 1


def test_checking_twice_only_posts_once(fed_ctx, user, sharing_settings):
    a_scrobble_in_the_target_week(user)
    first = weekly.check_and_post(sharing_settings, GATE)
    second = weekly.check_and_post(sharing_settings, GATE)
    assert (first, second) == ("posted", "already_posted")
    assert len(posts_for(user.id)) == 1


def test_before_the_gate_nothing_is_posted_yet(fed_ctx, user, sharing_settings):
    a_scrobble_in_the_target_week(user)
    just_before_gate = GATE.replace(minute=59, hour=8)
    outcome = weekly.check_and_post(sharing_settings, just_before_gate)
    # Before the gate, the target is the *previous* week (empty), so this is skipped —
    # not an error, and the play in the now-current week isn't lost, just not due yet.
    assert outcome == "skipped_empty"
    assert posts_for(user.id) == []


def test_weekly_summaries_can_be_switched_off(fed_ctx, user, sharing_settings):
    a_scrobble_in_the_target_week(user)
    sharing.update(user.id, {"post_weekly_summary": False})
    outcome = weekly.check_and_post(sharing.settings_for(user.id), GATE)
    assert outcome == "disabled"
    assert posts_for(user.id) == []


def test_a_user_who_stopped_sharing_is_not_posted_for(fed_ctx, user):
    sharing.update(user.id, {"enabled": True})
    a_scrobble_in_the_target_week(user)
    settings = sharing.settings_for(user.id)  # a stale copy: still says "sharing"
    sharing.update(user.id, {"enabled": False})
    # check_and_post's own has_post()/enabled check can't catch this: the settings
    # object it was handed is stale. publish() re-reads settings itself and refuses —
    # defence in depth against exactly this race between a worker pass starting and a
    # user disabling sharing mid-pass.
    outcome = weekly.check_and_post(settings, GATE)
    assert outcome == "already_posted"  # no post resulted, whatever the exact label
    assert posts_for(user.id) == []


def test_a_worker_down_for_weeks_posts_only_the_latest_week(fed_ctx, user, sharing_settings):
    # Plays spread across three different weeks
    played(user, "A", "Old", datetime(2025, 12, 22, 12, tzinfo=UTC))  # weeks before 2026-W02
    played(user, "B", "Older", datetime(2025, 12, 29, 12, tzinfo=UTC))
    a_scrobble_in_the_target_week(user)  # 2026-W02: the only week that should be posted

    much_later = datetime(2026, 3, 1, 12, tzinfo=UTC)
    weekly.check_and_post(sharing_settings, much_later)
    # Nothing posted: the target week by March is nowhere near W02 or the empty ones
    assert posts_for(user.id) == []

    weekly.check_and_post(sharing_settings, GATE)
    [post] = posts_for(user.id)
    assert post.key == "weekly:2026-W02"


def test_check_all_covers_every_sharing_user_and_isolates_failures(fed_ctx, make_user):
    alice = make_user(username="alice_weekly")
    bob = make_user(username="bob_weekly")
    sharing.update(alice.id, {"enabled": True})
    sharing.update(bob.id, {"enabled": True})
    # Simulate a since-invalid stored value (e.g. a renamed IANA zone), bypassing
    # update()'s own validation, to prove resolve_timezone()'s fallback keeps the task
    # going for this user instead of raising and skipping everyone after them.
    bob_settings = sharing.settings_for(bob.id)
    bob_settings.timezone = "not-a-real-zone"
    db.session.commit()
    a_scrobble_in_the_target_week(alice)
    a_scrobble_in_the_target_week(bob)

    weekly.check_all(GATE)

    assert len(posts_for(alice.id)) == 1
    assert len(posts_for(bob.id)) == 1  # a bad stored timezone doesn't stop the post either


def test_check_all_skips_users_without_weekly_summaries_or_not_sharing(fed_ctx, make_user):
    off = make_user(username="off")
    sharing.update(off.id, {"enabled": True, "post_weekly_summary": False})
    never_shared = make_user(username="never_shared")
    a_scrobble_in_the_target_week(off)
    a_scrobble_in_the_target_week(never_shared)

    weekly.check_all(GATE)

    assert posts_for(off.id) == []
    assert posts_for(never_shared.id) == []


def test_the_task_is_registered_when_federation_is_on(fed_app):
    from scrobbler import worker

    assert "federation.weekly" in worker.registered(fed_app)


def test_the_task_is_not_registered_when_federation_is_off(app):
    from scrobbler import worker

    assert "federation.weekly" not in worker.registered(app)


def test_deleting_a_weekly_post_works_like_any_other_post(fed_ctx, user, sharing_settings):
    a_scrobble_in_the_target_week(user)
    weekly.check_and_post(sharing_settings, GATE)
    [post] = posts_for(user.id)
    activity = publishing.delete_post(user.id, "alice", post)
    db.session.commit()
    assert activity.activity_type == "Delete"
    assert db.session.get(FederationPost, post.id).deleted_at is not None
