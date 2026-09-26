"""Milestones: total scrobbles, plays of one artist, and entries into a user's all-time
top 10 — each posted once, the first time it's reached.

Checked only for a user's own live scrobbles (never for imports, which would otherwise
flood years of history through as if it just happened): the `scrobbles_stored` receiver
below queues a FederationPendingCheck row, drained by the `federation.milestones` worker
task. Enabling sharing, and finishing an import, instead call `set_baseline` — every
threshold already passed is marked done without posting, so a new or returning sharer
never gets a burst of "congratulations" for plays from years ago.
"""

import logging

from scrobbler.extensions import db
from scrobbler.federation import publishing, sharing
from scrobbler.federation.models import FederationMilestoneMark, FederationPendingCheck
from scrobbler.federation.protocol import content
from scrobbler.services import accounts, stats

log = logging.getLogger(__name__)

KIND = "milestone"

SCROBBLE_THRESHOLDS = [1000, 5000, 10000, *range(25000, 500_000, 25000)]
ARTIST_THRESHOLDS = [100, 500, 1000]
TOP_N = 10
# A top-10 entry and an artist-plays threshold are only ever checked against a user's
# current top ARTIST_SCAN artists: crossing 100+ plays for one artist keeps it well
# within a generous slice, and scanning a user's entire artist list on every scrobble
# would be unbounded. A crossing that (rarely) falls outside this slice is simply
# missed rather than triggering a full unpaginated scan.
ARTIST_SCAN = 20


def _existing_marks(user_id: int) -> set[str]:
    rows = db.session.scalars(db.select(FederationMilestoneMark.key).filter_by(user_id=user_id))
    return set(rows)


def _mark(user_id: int, key: str, *, posted: bool) -> None:
    db.session.add(FederationMilestoneMark(user_id=user_id, key=key, posted=posted))


def _scrobbles_content(threshold: int) -> tuple[str, str]:
    words = content.plural(threshold, "play")
    headline = f"{threshold:,} {words} scrobbled"
    text = "\n".join([headline, content.hashtag_line()])
    html = content.paragraph(f"<strong>{content.escape(headline)}</strong>") + content.paragraph(
        content.hashtag_line()
    )
    return text, html


def _artist_content(artist: str, threshold: int) -> tuple[str, str]:
    words = content.plural(threshold, "play")
    headline = f"{threshold:,} {words} of {artist}"
    text = "\n".join([headline, content.hashtag_line()])
    html = content.paragraph(
        f"<strong>{threshold:,} {words} of {content.escape(artist)}</strong>"
    ) + content.paragraph(content.hashtag_line())
    return text, html


def _top10_content(artist: str, rank: int) -> tuple[str, str]:
    headline = f"{artist} entered my all-time top 10 (#{rank})"
    text = "\n".join([headline, content.hashtag_line()])
    html = content.paragraph(f"<strong>{content.escape(headline)}</strong>") + content.paragraph(
        content.hashtag_line()
    )
    return text, html


def _scrobble_milestone(user, existing: set[str]) -> tuple[str, str, str] | None:
    """(key, text, html) for the highest not-yet-marked scrobble-count threshold this
    user has reached, marking every lower one reached at the same time silently. None if
    none has been reached."""
    count = stats.summary(user)["scrobbles"]
    reached = [t for t in SCROBBLE_THRESHOLDS if t <= count and f"scrobbles:{t}" not in existing]
    if not reached:
        return None
    for threshold in reached[:-1]:
        _mark(user.id, f"scrobbles:{threshold}", posted=False)
    top = reached[-1]
    text, html = _scrobbles_content(top)
    return f"scrobbles:{top}", text, html


def _artist_milestone(user, existing: set[str]) -> tuple[str, str, str] | None:
    """(key, text, html) for the highest not-yet-marked plays-of-one-artist threshold
    reached by any artist in the user's current top ARTIST_SCAN. Only one post per
    check, even if more than one artist or threshold qualifies at once."""
    top_artists = stats.top_artists(user, per_page=ARTIST_SCAN).items
    best: tuple[str, str, int, int] | None = None  # (key, artist, threshold, playcount)
    for artist in top_artists:
        name = artist.name.lower()
        reached = [
            t
            for t in ARTIST_THRESHOLDS
            if t <= artist.playcount and f"artist:{name}:{t}" not in existing
        ]
        for threshold in reached[:-1]:
            _mark(user.id, f"artist:{name}:{threshold}", posted=False)
        if reached and (best is None or reached[-1] > best[2]):
            best = (f"artist:{name}:{reached[-1]}", artist.name, reached[-1], artist.playcount)
    if best is None:
        return None
    key, artist_name, threshold, _ = best
    text, html = _artist_content(artist_name, threshold)
    return key, text, html


def _top10_milestone(user, existing: set[str]) -> tuple[str, str, str] | None:
    """(key, text, html) for the highest-ranked, not-yet-marked new entry into the
    user's current all-time top 10. Only one post per check.

    Nothing is posted until the user actually has TOP_N distinct artists: with fewer
    than that, every first play of a new artist would trivially "enter the top 10",
    which isn't a real milestone.
    """
    page = stats.top_artists(user, per_page=TOP_N)
    if page.total < TOP_N:
        return None
    for artist in page.items:  # already ranked best first
        key = f"top10:{artist.name.lower()}"
        if key not in existing:
            text, html = _top10_content(artist.name, artist.rank)
            return key, text, html
    return None


def check_and_post(user_id: int) -> str:
    """For one user, post at most one milestone: the first of scrobble-count, top-10
    entry, or artist-plays (in that priority) that has a new, unmarked threshold to
    report. Returns a result: disabled, none or posted."""
    settings = sharing.settings_for(user_id)
    if not (settings.enabled and settings.post_milestones):
        return "disabled"
    user = accounts.get_user(user_id)
    if user is None:  # the account was deleted; federation_settings should follow, but be safe
        return "disabled"

    existing = _existing_marks(user_id)
    found = (
        _scrobble_milestone(user, existing)
        or _top10_milestone(user, existing)
        or _artist_milestone(user, existing)
    )
    if found is None:
        return "none"
    key, text, html = found
    post = publishing.publish(user.id, user.username, KIND, key, text=text, html=html)
    if post is not None:
        _mark(user.id, key, posted=True)
    return "posted" if post is not None else "none"  # lost a race with another pass


def set_baseline(user_id: int) -> None:
    """Mark every threshold this user has already passed, without posting: called when
    a user enables sharing, and after an import finishes, so history already on file
    never produces a flood of retroactive "milestone" posts."""
    user = accounts.get_user(user_id)
    if user is None:
        return
    existing = _existing_marks(user_id)
    count = stats.summary(user)["scrobbles"]
    for threshold in SCROBBLE_THRESHOLDS:
        if threshold <= count and f"scrobbles:{threshold}" not in existing:
            _mark(user_id, f"scrobbles:{threshold}", posted=False)
    top_artists = stats.top_artists(user, per_page=max(TOP_N, ARTIST_SCAN))
    has_top10 = top_artists.total >= TOP_N
    for artist in top_artists.items:
        name = artist.name.lower()
        if has_top10 and artist.rank <= TOP_N and f"top10:{name}" not in existing:
            _mark(user_id, f"top10:{name}", posted=False)
        for threshold in ARTIST_THRESHOLDS:
            if threshold <= artist.playcount and f"artist:{name}:{threshold}" not in existing:
                _mark(user_id, f"artist:{name}:{threshold}", posted=False)
    db.session.commit()


def on_scrobbles_stored(sender, *, scrobbles, source, **kwargs) -> None:
    """Queue a milestone check for the user's own live scrobbles. Imports are never
    live listening, so they're excluded here and instead handled by set_baseline via
    on_import_finished."""
    if source != "scrobble" or not scrobbles:
        return
    settings = sharing.settings_for(sender.id)
    if not (settings.enabled and settings.post_milestones):
        return
    if db.session.get(FederationPendingCheck, sender.id) is None:
        db.session.add(FederationPendingCheck(user_id=sender.id))
        db.session.commit()


def on_import_finished(sender, *, job, **kwargs) -> None:
    """An import just finished: mark whatever it added as baseline, silently, rather
    than queuing a live check that would post about years-old history."""
    settings = sharing.settings_for(sender.id)
    if not settings.enabled:  # skip the computation for users who've never touched federation
        return
    set_baseline(sender.id)


def work_once() -> bool:
    """Claim one pending user (oldest first) and check them. On failure, the row is
    requeued at the back rather than retried immediately, so one persistently-failing
    user's checks (a pure DB read — unlike delivery, there's no external server to fail
    against) can't head-of-line-block everyone behind them."""
    row = db.session.execute(
        db.select(FederationPendingCheck)
        .order_by(FederationPendingCheck.since)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if row is None:
        return False
    user_id = row.user_id
    try:
        check_and_post(user_id)
    except Exception:
        log.exception("milestone check failed for user %s", user_id)
        db.session.rollback()
        db.session.execute(
            db.update(FederationPendingCheck)
            .where(FederationPendingCheck.user_id == user_id)
            .values(since=db.func.now())
        )
        db.session.commit()
        return True
    db.session.execute(
        db.delete(FederationPendingCheck).where(FederationPendingCheck.user_id == user_id)
    )
    db.session.commit()
    return True
