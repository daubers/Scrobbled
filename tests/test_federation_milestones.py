"""Milestones end to end: publishing/milestones.py against a real database."""

from datetime import UTC, datetime, timedelta

import pytest

from scrobbler.extensions import db
from scrobbler.federation import sharing
from scrobbler.federation.models import (
    FederationMilestoneMark,
    FederationPendingCheck,
    FederationPost,
)
from scrobbler.federation.publishing import milestones
from scrobbler.models import Scrobble


def played(user, artist, track, n=1, start=None):
    start = start or datetime(2020, 1, 1, tzinfo=UTC)
    for i in range(n):
        db.session.add(
            Scrobble(
                user_id=user.id, artist=artist, track=track, played_at=start + timedelta(minutes=i)
            )
        )
    db.session.commit()


@pytest.fixture
def sharing_settings(fed_ctx, user):
    """Sharing switched on for a user with no scrobbles yet, so tests can add plays
    afterwards and treat every threshold crossed as genuinely new."""
    sharing.update(user.id, {"enabled": True})
    return sharing.settings_for(user.id)


def posts_for(user_id):
    return db.session.scalars(db.select(FederationPost).filter_by(user_id=user_id)).all()


def marks_for(user_id) -> dict[str, bool]:
    rows = db.session.scalars(db.select(FederationMilestoneMark).filter_by(user_id=user_id))
    return {row.key: row.posted for row in rows}


# --- check_and_post: scrobble-count -----------------------------------------------------


def test_nothing_reached_posts_nothing(fed_ctx, user, sharing_settings):
    played(user, "Radiohead", "Reckoner", n=2)
    assert milestones.check_and_post(user.id) == "none"
    assert posts_for(user.id) == []


def test_a_scrobble_count_threshold_is_posted(
    fed_ctx, user, sharing_settings, monkeypatch, metric_delta
):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    played(user, "Radiohead", "Reckoner", n=3)
    posted = metric_delta("scrobbler_federation_milestone_checks_total", result="posted")
    outcome = milestones.check_and_post(user.id)
    assert outcome == "posted"
    [post] = posts_for(user.id)
    assert post.key == "milestone:scrobbles:3"
    assert "3 plays scrobbled" in post.content_text
    assert marks_for(user.id) == {"scrobbles:3": True}
    assert posted.delta == 1


def test_checking_twice_only_posts_once(fed_ctx, user, sharing_settings, monkeypatch):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    played(user, "Radiohead", "Reckoner", n=3)
    first = milestones.check_and_post(user.id)
    second = milestones.check_and_post(user.id)
    assert (first, second) == ("posted", "none")
    assert len(posts_for(user.id)) == 1


def test_crossing_several_thresholds_at_once_posts_only_the_highest(
    fed_ctx, user, sharing_settings, monkeypatch
):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [2, 3, 5])
    played(user, "Radiohead", "Reckoner", n=5)
    outcome = milestones.check_and_post(user.id)
    assert outcome == "posted"
    [post] = posts_for(user.id)
    assert post.key == "milestone:scrobbles:5"
    # The lower thresholds are marked done, silently, so they're never posted later.
    assert marks_for(user.id) == {"scrobbles:2": False, "scrobbles:3": False, "scrobbles:5": True}


# --- check_and_post: top-10 entry -------------------------------------------------------


def test_a_top10_entry_is_posted(fed_ctx, user, sharing_settings, monkeypatch):
    monkeypatch.setattr(milestones, "TOP_N", 1)
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [])
    played(user, "Radiohead", "Reckoner", n=3)
    outcome = milestones.check_and_post(user.id)
    assert outcome == "posted"
    [post] = posts_for(user.id)
    assert post.key == "milestone:top10:radiohead"
    assert "Radiohead entered my all-time top 10 (#1)" in post.content_text
    assert marks_for(user.id) == {"top10:radiohead": True}


# --- check_and_post: artist plays --------------------------------------------------------


def test_an_artist_plays_threshold_is_posted(fed_ctx, user, sharing_settings, monkeypatch):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [])
    monkeypatch.setattr(milestones, "TOP_N", 0)
    monkeypatch.setattr(milestones, "ARTIST_THRESHOLDS", [3])
    played(user, "Radiohead", "Reckoner", n=3)
    outcome = milestones.check_and_post(user.id)
    assert outcome == "posted"
    [post] = posts_for(user.id)
    assert post.key == "milestone:artist:radiohead:3"
    assert "3 plays of Radiohead" in post.content_text
    assert marks_for(user.id) == {"artist:radiohead:3": True}


def test_scrobble_count_takes_priority_over_top10_and_artist(
    fed_ctx, user, sharing_settings, monkeypatch
):
    """When more than one kind qualifies in the same check, only the highest-priority
    one (scrobble count) is posted; the others are left unmarked, to be picked up on a
    later check rather than silently lost."""
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    monkeypatch.setattr(milestones, "TOP_N", 1)
    monkeypatch.setattr(milestones, "ARTIST_THRESHOLDS", [3])
    played(user, "Radiohead", "Reckoner", n=3)
    outcome = milestones.check_and_post(user.id)
    assert outcome == "posted"
    [post] = posts_for(user.id)
    assert post.key == "milestone:scrobbles:3"
    assert marks_for(user.id) == {"scrobbles:3": True}

    # Picked up on the very next check, now that the scrobble-count one is out of the way.
    outcome = milestones.check_and_post(user.id)
    assert outcome == "posted"
    assert len(posts_for(user.id)) == 2


# --- disabled / missing users -----------------------------------------------------------


def test_milestones_can_be_switched_off(fed_ctx, user, sharing_settings, monkeypatch):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    played(user, "Radiohead", "Reckoner", n=3)
    sharing.update(user.id, {"post_milestones": False})
    assert milestones.check_and_post(user.id) == "disabled"
    assert posts_for(user.id) == []


def test_a_user_who_stopped_sharing_is_not_posted_for(fed_ctx, user, monkeypatch):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    sharing.update(user.id, {"enabled": True})
    played(user, "Radiohead", "Reckoner", n=3)
    sharing.update(user.id, {"enabled": False})
    assert milestones.check_and_post(user.id) == "disabled"
    assert posts_for(user.id) == []


def test_a_user_id_that_has_never_shared_is_handled_gracefully(fed_ctx):
    assert milestones.check_and_post(999_999) == "disabled"


# --- set_baseline: no retroactive posts -------------------------------------------------


def test_set_baseline_marks_existing_thresholds_without_posting(fed_ctx, user, monkeypatch):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    monkeypatch.setattr(milestones, "TOP_N", 1)
    monkeypatch.setattr(milestones, "ARTIST_THRESHOLDS", [3])
    played(user, "Radiohead", "Reckoner", n=3)

    milestones.set_baseline(user.id)

    assert posts_for(user.id) == []
    assert marks_for(user.id) == {
        "scrobbles:3": False,
        "top10:radiohead": False,
        "artist:radiohead:3": False,
    }


def test_enabling_sharing_baselines_existing_history(fed_ctx, user, monkeypatch):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    monkeypatch.setattr(milestones, "TOP_N", 0)  # isolate to the scrobble-count check
    played(user, "Radiohead", "Reckoner", n=3)

    sharing.update(user.id, {"enabled": True})  # history predates sharing

    assert marks_for(user.id) == {"scrobbles:3": False}
    assert milestones.check_and_post(user.id) == "none"
    assert posts_for(user.id) == []


def test_disabling_and_re_enabling_does_not_re_baseline(
    fed_ctx, user, sharing_settings, monkeypatch
):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    played(user, "Radiohead", "Reckoner", n=3)
    milestones.check_and_post(user.id)  # posts scrobbles:3
    [post] = posts_for(user.id)

    sharing.update(user.id, {"enabled": False})
    sharing.update(user.id, {"enabled": True})

    assert posts_for(user.id) == [post]  # unchanged, not re-posted or duplicated


def test_import_finished_baselines_when_sharing_is_on(fed_ctx, user, monkeypatch):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    monkeypatch.setattr(milestones, "TOP_N", 0)  # isolate to the scrobble-count check
    sharing.update(user.id, {"enabled": True})
    played(user, "Radiohead", "Reckoner", n=3)

    milestones.on_import_finished(user, job=None)

    assert marks_for(user.id) == {"scrobbles:3": False}


def test_import_finished_skips_users_who_have_never_shared(fed_ctx, user, monkeypatch):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    played(user, "Radiohead", "Reckoner", n=3)

    milestones.on_import_finished(user, job=None)  # settings.enabled is False

    assert marks_for(user.id) == {}


# --- the scrobbles_stored receiver: live plays only --------------------------------------


def pending_for(user_id) -> FederationPendingCheck | None:
    return db.session.get(FederationPendingCheck, user_id)


def test_a_live_scrobble_queues_a_pending_check(fed_ctx, user, sharing_settings):
    milestones.on_scrobbles_stored(user, scrobbles=["placeholder"], source="scrobble")
    assert pending_for(user.id) is not None


def test_an_import_does_not_queue_a_pending_check(fed_ctx, user, sharing_settings):
    milestones.on_scrobbles_stored(user, scrobbles=["placeholder"], source="import")
    assert pending_for(user.id) is None


def test_an_empty_batch_does_not_queue_a_pending_check(fed_ctx, user, sharing_settings):
    milestones.on_scrobbles_stored(user, scrobbles=[], source="scrobble")
    assert pending_for(user.id) is None


def test_a_scrobble_is_a_noop_when_milestones_are_off(fed_ctx, user, sharing_settings):
    sharing.update(user.id, {"post_milestones": False})
    milestones.on_scrobbles_stored(user, scrobbles=["placeholder"], source="scrobble")
    assert pending_for(user.id) is None


def test_a_scrobble_is_a_noop_when_not_sharing(fed_ctx, user):
    milestones.on_scrobbles_stored(user, scrobbles=["placeholder"], source="scrobble")
    assert pending_for(user.id) is None


def test_repeated_scrobbles_collapse_into_one_pending_row(fed_ctx, user, sharing_settings):
    milestones.on_scrobbles_stored(user, scrobbles=["a"], source="scrobble")
    milestones.on_scrobbles_stored(user, scrobbles=["b"], source="scrobble")  # no error, no 2nd row
    assert (
        db.session.scalar(
            db.select(db.func.count())
            .select_from(FederationPendingCheck)
            .filter_by(user_id=user.id)
        )
        == 1
    )


# --- work_once: draining the queue --------------------------------------------------------


def test_work_once_returns_false_when_nothing_is_pending(fed_ctx):
    assert milestones.work_once() is False


def test_work_once_checks_and_clears_a_pending_user(fed_ctx, user, sharing_settings, monkeypatch):
    monkeypatch.setattr(milestones, "SCROBBLE_THRESHOLDS", [3])
    played(user, "Radiohead", "Reckoner", n=3)
    db.session.add(FederationPendingCheck(user_id=user.id))
    db.session.commit()

    assert milestones.work_once() is True

    assert pending_for(user.id) is None
    assert len(posts_for(user.id)) == 1


def test_work_once_processes_the_oldest_pending_user_first(fed_ctx, make_user, sharing_settings):
    later = make_user(username="later")
    sharing.update(later.id, {"enabled": True})
    earlier = sharing_settings  # the sharing_settings fixture's own user

    now = datetime.now(UTC)
    db.session.add(FederationPendingCheck(user_id=later.id, since=now))
    db.session.add(
        FederationPendingCheck(user_id=earlier.user_id, since=now - timedelta(minutes=5))
    )
    db.session.commit()

    milestones.work_once()

    assert pending_for(earlier.user_id) is None
    assert pending_for(later.id) is not None


def test_work_once_requeues_on_failure(fed_ctx, user, sharing_settings, monkeypatch):
    def boom(user_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(milestones, "check_and_post", boom)
    db.session.add(FederationPendingCheck(user_id=user.id, since=datetime(2020, 1, 1, tzinfo=UTC)))
    db.session.commit()

    assert milestones.work_once() is True

    row = pending_for(user.id)
    assert row is not None  # requeued, not dropped
    assert row.since > datetime(2020, 1, 1, tzinfo=UTC)  # pushed to the back of the queue


def test_the_task_is_registered_when_federation_is_on(fed_app):
    from scrobbler import worker

    assert "federation.milestones" in worker.registered(fed_app)


def test_the_task_is_not_registered_when_federation_is_off(app):
    from scrobbler import worker

    assert "federation.milestones" not in worker.registered(app)
