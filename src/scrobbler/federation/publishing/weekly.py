"""Weekly summaries: for each sharing user with them on, once the Monday-09:00-local
posting gate has passed (see protocol/schedule.target_week), post about the Monday-
Sunday week that just ended.

Empty weeks aren't posted. Since the target week only ever moves forward — once its own
gate has passed, it never becomes the target again — an empty week is never reconsidered
either, without needing a separate "skipped" marker.
"""

import logging
from datetime import UTC, datetime

from scrobbler.extensions import db
from scrobbler.federation import metrics as fed_metrics
from scrobbler.federation import publishing, sharing
from scrobbler.federation.models import FederationSettings
from scrobbler.federation.protocol import content, schedule
from scrobbler.services import accounts, stats

KIND = "weekly"
TOP_ARTISTS = 3

log = logging.getLogger(__name__)


def build_content(count: int, top_artists: list, top_tracks: list) -> tuple[str, str]:
    headline = f"My week in music: {count} {content.plural(count, 'play')}"
    text_lines = [headline]
    html_parts = [content.paragraph(f"<strong>{content.escape(headline)}</strong>")]

    if top_artists:
        text_lines.append(
            "Top artists: "
            + " ".join(f"{i}. {a.name} ({a.playcount})" for i, a in enumerate(top_artists, 1))
        )
        html_parts.append(
            content.numbered_list(
                [
                    f"{content.escape(a.name)} — {a.playcount} "
                    f"{content.plural(a.playcount, 'play')}"
                    for a in top_artists
                ]
            )
        )

    if top_tracks:
        top = top_tracks[0]
        words = content.plural(top.playcount, "play")
        text_lines.append(f"Most played: {top.name} by {top.artist} ({top.playcount} {words})")
        html_parts.append(
            content.paragraph(
                f"Most played: <em>{content.escape(top.name)}</em> by "
                f"{content.escape(top.artist)} ({top.playcount} {words})"
            )
        )

    text_lines.append(content.hashtag_line())
    html_parts.append(content.paragraph(content.hashtag_line()))
    return "\n".join(text_lines), "".join(html_parts)


def check_and_post(settings: FederationSettings, now_utc: datetime) -> str:
    """For one user's settings, post their most recent eligible week if it's due and not
    already posted. Returns a result: disabled, already_posted, skipped_empty or posted."""
    result = _check_and_post(settings, now_utc)
    fed_metrics.weekly_total.labels(result=result).inc()
    return result


def _check_and_post(settings: FederationSettings, now_utc: datetime) -> str:
    if not settings.post_weekly_summary:
        return "disabled"
    user = accounts.get_user(settings.user_id)
    if user is None:  # the account was deleted; federation_settings should follow, but be safe
        return "disabled"

    tz = schedule.resolve_timezone(settings.timezone)
    start, end, key = schedule.target_week(now_utc, tz)
    if publishing.has_post(user.id, KIND, key):
        return "already_posted"  # the common case: checked before, nothing new to do

    count = stats.scrobble_count_between(user, start, end)
    if count == 0:
        return "skipped_empty"

    top_artists = stats.top_artists_between(user, start, end, limit=TOP_ARTISTS)
    top_tracks = stats.top_tracks_between(user, start, end, limit=1)
    text, html = build_content(count, top_artists, top_tracks)
    post = publishing.publish(user.id, user.username, KIND, key, text=text, html=html)
    return "posted" if post is not None else "already_posted"  # lost a race with another pass


def preview(user_id: int, now_utc: datetime | None = None) -> tuple[str, str] | None:
    """What a weekly summary would say right now, for the week still in progress. Never
    stored or posted: for the Sharing page's live preview. None if the account is gone."""
    user = accounts.get_user(user_id)
    if user is None:
        return None
    settings = sharing.settings_for(user_id)
    tz = schedule.resolve_timezone(settings.timezone)
    now_utc = now_utc or datetime.now(UTC)
    start = schedule.current_week_start(now_utc, tz)
    count = stats.scrobble_count_between(user, start, now_utc)
    top_artists = stats.top_artists_between(user, start, now_utc, limit=TOP_ARTISTS)
    top_tracks = stats.top_tracks_between(user, start, now_utc, limit=1)
    return build_content(count, top_artists, top_tracks)


def check_all(now_utc: datetime | None = None) -> None:
    """Worker task (periodic): check every sharing user with weekly summaries on. One
    user's failure is logged and doesn't stop the rest from being checked."""
    now_utc = now_utc or datetime.now(UTC)
    settings_rows = db.session.scalars(
        db.select(FederationSettings).filter_by(enabled=True, post_weekly_summary=True)
    ).all()
    for settings in settings_rows:
        try:
            check_and_post(settings, now_utc)
        except Exception:
            log.exception("weekly summary check failed for user %s", settings.user_id)
            db.session.rollback()
        else:
            db.session.commit()
