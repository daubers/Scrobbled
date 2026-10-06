"""Now playing: a live field on the actor profile (actors.py handles that part), and
opt-in posts.

No event receiver or pending-checks queue like milestones has: the throttle windows (5
minutes for the profile push, 30 for posts) are far longer than a 30-second poll, so a
plain periodic scan - the same shape as weekly.check_all - is simplest and cheapest.
There's no baseline/import concern either: this only ever reflects what's playing right
now, never history.
"""

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from flask import current_app

from scrobbler.extensions import db
from scrobbler.federation import activities, actors, followers, publishing, sharing
from scrobbler.federation import metrics as fed_metrics
from scrobbler.federation.models import FederationNowPlaying, FederationPost, FederationSettings
from scrobbler.federation.protocol import content, vocab
from scrobbler.services import accounts, art
from scrobbler.services.scrobbles import get_now_playing

KIND = "now_playing"
PROFILE_THROTTLE = timedelta(minutes=5)
POST_THROTTLE = timedelta(minutes=30)
MIN_PLAYED = timedelta(seconds=30)

log = logging.getLogger(__name__)


def _key(playing) -> str:
    """A short, fixed-length identifier for "this track, starting at this instant" -
    hashed rather than the raw artist/track/timestamp, since federation_posts.key is only
    VARCHAR(64) and real artist and track names routinely blow past that on their own."""
    raw = f"{playing.artist}\x1f{playing.track}\x1f{playing.started_at.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _state_for(user_id: int) -> FederationNowPlaying:
    state = db.session.get(FederationNowPlaying, user_id)
    if state is None:
        state = FederationNowPlaying(user_id=user_id)
        db.session.add(state)
    return state


def _due(last: datetime | None, throttle: timedelta, now: datetime) -> bool:
    return last is None or now - last >= throttle


def _urls_for(username: str) -> vocab.ActorUrls:
    config = current_app.extensions["federation"]
    profile_page = f"{current_app.config['UI_BASE_URL']}/profile.html?u={username}"
    return vocab.actor_urls(config.base_url, username, profile_page)


def _push_profile(user, settings) -> None:
    urls = _urls_for(user.username)
    doc = actors.build_document(urls, user, settings)
    activity = activities.store(
        user.id, lambda url: vocab.update(url, urls.id, doc, to=[urls.followers])
    )
    inboxes = followers.accepted_inboxes(user.id)
    if inboxes:
        activities.queue(activity, inboxes)


def _build_content(playing) -> tuple[str, str]:
    text = "\n".join(
        [
            f"Now playing: {playing.track} by {playing.artist}",
            f"{content.hashtag_line()} #NowPlaying",
        ]
    )
    html = content.paragraph(
        f"Now playing: <em>{content.escape(playing.track)}</em> by {content.escape(playing.artist)}"
    ) + content.paragraph(f"{content.hashtag_line()} #NowPlaying")
    return text, html


def _attachment_for(playing) -> list[dict]:
    """Cover art for the post, if we have an album and can resolve it. Cover Art
    Archive's sized convenience endpoints (what art.lookup() resolves to) are always
    JPEG, whatever the original upload was."""
    if not playing.album:
        return []
    result = art.lookup(playing.artist, playing.album)
    if result.status != "found":
        return []
    return [
        {
            "type": "Image",
            "mediaType": "image/jpeg",
            "url": result.image_url,
            "name": f"Cover art for {playing.album}",
        }
    ]


def _post_now_playing(user, playing, key: str, state: FederationNowPlaying, now: datetime) -> str:
    text, html = _build_content(playing)
    post = publishing.publish(
        user.id,
        user.username,
        KIND,
        key,
        text=text,
        html=html,
        tags=("Scrobbler", "NowPlaying"),
        attachment=_attachment_for(playing),
    )
    if post is None:
        return "none"
    if state.post_key is not None:
        previous = db.session.scalar(
            db.select(FederationPost).filter_by(user_id=user.id, key=f"{KIND}:{state.post_key}")
        )
        if previous is not None and previous.deleted_at is None:
            publishing.delete_post(user.id, user.username, previous)
    state.post_key = key
    state.post_posted_at = now
    return "posted"


def check_and_post(user_id: int, now_utc: datetime | None = None) -> dict[str, str]:
    """For one user: push a throttled profile update and/or post, whichever their
    now_playing_mode calls for. Returns {"profile": ..., "post": ...}; "profile" is
    "unchanged", "throttled" or "sent", "post" is "off", "none" or "posted"."""
    settings = sharing.settings_for(user_id)
    if settings.now_playing_mode == "off":
        return {"profile": "off", "post": "off"}
    user = accounts.get_user(user_id)
    if user is None:  # the account was deleted; federation_settings should follow, but be safe
        return {"profile": "off", "post": "off"}

    now_utc = now_utc or datetime.now(UTC)
    playing = get_now_playing(user)
    current_key = _key(playing) if playing else None
    state = _state_for(user_id)

    profile_result = "unchanged"
    if current_key != state.profile_key:
        if _due(state.profile_updated_at, PROFILE_THROTTLE, now_utc):
            _push_profile(user, settings)
            state.profile_key = current_key
            state.profile_updated_at = now_utc
            profile_result = "sent"
        else:
            profile_result = "throttled"
        fed_metrics.profile_updates_total.labels(result=profile_result).inc()

    post_result = "off"
    if settings.now_playing_mode == "posts":
        post_result = "none"
        if (
            playing is not None
            and now_utc - playing.started_at >= MIN_PLAYED
            and current_key != state.post_key
            and _due(state.post_posted_at, POST_THROTTLE, now_utc)
        ):
            post_result = _post_now_playing(user, playing, current_key, state, now_utc)

    db.session.commit()
    return {"profile": profile_result, "post": post_result}


def check_all(now_utc: datetime | None = None) -> None:
    """Worker task (periodic, every 30 seconds): check every user with now playing on.
    One user's failure is logged and doesn't stop the rest from being checked."""
    now_utc = now_utc or datetime.now(UTC)
    user_ids = db.session.scalars(
        db.select(FederationSettings.user_id).filter(FederationSettings.now_playing_mode != "off")
    ).all()
    for user_id in user_ids:
        try:
            check_and_post(user_id, now_utc)
        except Exception:
            log.exception("now playing check failed for user %s", user_id)
            db.session.rollback()
