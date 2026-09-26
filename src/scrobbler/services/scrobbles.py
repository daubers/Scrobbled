"""Scrobble validation/storage and now-playing state."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from scrobbler import events, metrics
from scrobbler.extensions import db
from scrobbler.models import ApiApp, NowPlaying, Scrobble, User

MAX_BATCH = 50
MAX_TEXT = 1024

# Last.fm ignoredMessage codes
NOT_IGNORED = 0
IGNORED_ARTIST = 1
IGNORED_TRACK = 2
IGNORED_TOO_OLD = 3
IGNORED_TOO_NEW = 4

IGNORED_MESSAGES = {
    NOT_IGNORED: "",
    IGNORED_ARTIST: "Artist was ignored",
    IGNORED_TRACK: "Track was ignored",
    IGNORED_TOO_OLD: "Timestamp was too old",
    IGNORED_TOO_NEW: "Timestamp was too new",
}

_INDEXED = re.compile(r"^(?P<name>[A-Za-z]+)\[(?P<index>\d+)\]$")


class BatchError(ValueError):
    pass


@dataclass
class TrackInput:
    artist: str
    track: str
    album: str | None = None
    album_artist: str | None = None
    duration: int | None = None
    track_number: int | None = None
    mbid: str | None = None
    timestamp: int | None = None


@dataclass
class ScrobbleResult:
    item: TrackInput
    status: str  # accepted, duplicate or ignored
    ignored_code: int = NOT_IGNORED

    @property
    def ignored_message(self) -> str:
        return IGNORED_MESSAGES[self.ignored_code]


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value[:MAX_TEXT] or None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def track_input(fields: dict[str, str]) -> TrackInput:
    return TrackInput(
        artist=_text(fields.get("artist")) or "",
        track=_text(fields.get("track")) or "",
        album=_text(fields.get("album")),
        album_artist=_text(fields.get("albumArtist")),
        duration=_int(fields.get("duration")),
        track_number=_int(fields.get("trackNumber")),
        mbid=_text(fields.get("mbid")),
        timestamp=_int(fields.get("timestamp")),
    )


def parse_batch(params: dict[str, str]) -> list[TrackInput]:
    """Parse `artist[0]`, `track[0]`, ... array params (or a single un-indexed track)."""
    grouped: dict[int, dict[str, str]] = {}
    for key, value in params.items():
        if match := _INDEXED.match(key):
            grouped.setdefault(int(match["index"]), {})[match["name"]] = value
    if not grouped and ("artist" in params or "track" in params):
        grouped[0] = params
    if not grouped:
        raise BatchError("No tracks supplied")
    if len(grouped) > MAX_BATCH:
        raise BatchError(f"At most {MAX_BATCH} scrobbles per request")
    items = [track_input(grouped[i]) for i in sorted(grouped)]
    if any(item.timestamp is None for item in items):
        raise BatchError("Every scrobble needs an integer timestamp")
    return items


def _ignored_code(item: TrackInput, now: datetime) -> int:
    if not item.artist:
        return IGNORED_ARTIST
    if not item.track:
        return IGNORED_TRACK
    played_at = datetime.fromtimestamp(item.timestamp, UTC)
    if now - played_at > timedelta(days=current_app.config["SCROBBLE_MAX_AGE_DAYS"]):
        return IGNORED_TOO_OLD
    if played_at - now > timedelta(seconds=current_app.config["SCROBBLE_MAX_FUTURE_SECONDS"]):
        return IGNORED_TOO_NEW
    return NOT_IGNORED


def submit_scrobbles(user: User, api_app: ApiApp | None, items: list[TrackInput]):
    now = datetime.now(UTC)
    results: list[ScrobbleResult] = []
    for item in items:
        code = _ignored_code(item, now)
        if code != NOT_IGNORED:
            results.append(ScrobbleResult(item, "ignored", code))
            continue
        played_at = datetime.fromtimestamp(item.timestamp, UTC)
        inserted = db.session.execute(
            insert(Scrobble)
            .values(
                user_id=user.id,
                artist=item.artist,
                track=item.track,
                album=item.album,
                album_artist=item.album_artist,
                track_number=item.track_number,
                duration=item.duration,
                mbid=item.mbid,
                played_at=played_at,
                submitted_at=now,
                api_app_id=api_app.id if api_app else None,
            )
            .on_conflict_do_nothing(constraint="uq_scrobbles_dedupe")
            .returning(Scrobble.id)
        ).scalar()
        results.append(ScrobbleResult(item, "accepted" if inserted else "duplicate"))
        if inserted:
            metrics.scrobble_lag_seconds.observe(max(0.0, (now - played_at).total_seconds()))

    cleared = _clear_now_playing(user, [r.item for r in results if r.status != "ignored"])
    db.session.commit()

    stored = [r.item for r in results if r.status == "accepted"]
    if stored:
        events.send(events.scrobbles_stored, user, scrobbles=stored, source="scrobble")
    if cleared:
        events.send(events.now_playing_changed, user, track=None)

    metrics.scrobble_batch_size.observe(len(items))
    for result in results:
        metrics.scrobbles_total.labels(result=result.status).inc()
        if result.status == "ignored":
            metrics.scrobbles_ignored_total.labels(reason=str(result.ignored_code)).inc()
    return results


def _clear_now_playing(user: User, scrobbled: list[TrackInput]) -> bool:
    """Remove now-playing if one of the scrobbled tracks is it. Returns True if removed."""
    now_playing = db.session.get(NowPlaying, user.id)
    if now_playing is None:
        return False
    for item in scrobbled:
        if (item.artist.lower(), item.track.lower()) == (
            now_playing.artist.lower(),
            now_playing.track.lower(),
        ):
            db.session.delete(now_playing)
            return True
    return False


# --- Now playing ---------------------------------------------------------------


def update_now_playing(user: User, item: TrackInput) -> int:
    """Store the user's current track. Returns an ignored code (0 when stored)."""
    if not item.artist:
        return IGNORED_ARTIST
    if not item.track:
        return IGNORED_TRACK
    now = datetime.now(UTC)
    seconds = item.duration or current_app.config["NOW_PLAYING_DEFAULT_SECONDS"]
    values = {
        "artist": item.artist,
        "track": item.track,
        "album": item.album,
        "album_artist": item.album_artist,
        "duration": item.duration,
        "started_at": now,
        "expires_at": now + timedelta(seconds=seconds),
    }
    db.session.execute(
        insert(NowPlaying)
        .values(user_id=user.id, **values)
        .on_conflict_do_update(index_elements=[NowPlaying.user_id], set_=values)
    )
    db.session.commit()
    metrics.now_playing_updates_total.inc()
    events.send(events.now_playing_changed, user, track=item)
    return NOT_IGNORED


def get_now_playing(user: User) -> NowPlaying | None:
    return db.session.scalar(
        db.select(NowPlaying).where(
            NowPlaying.user_id == user.id, NowPlaying.expires_at > func.now()
        )
    )
