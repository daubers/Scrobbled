"""Listening statistics, shared by the Last.fm user.* methods and /api/v1/me."""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Select, func, literal_column, select

from scrobbler.extensions import db
from scrobbler.models import Scrobble, User

# Last.fm period names -> days (None = all time)
PERIODS: dict[str, int | None] = {
    "overall": None,
    "7day": 7,
    "1month": 30,
    "3month": 90,
    "6month": 180,
    "12month": 365,
}
BUCKETS = ("day", "week", "month")


@dataclass
class Page:
    items: list
    page: int
    per_page: int
    total: int

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(self.total / self.per_page)) if self.per_page else 1


@dataclass
class TopItem:
    rank: int
    name: str
    playcount: int
    artist: str | None = None  # for albums and tracks
    duration: int | None = None  # for tracks


def since(period: str, now: datetime | None = None) -> datetime | None:
    days = PERIODS[period]
    return None if days is None else (now or datetime.now(UTC)) - timedelta(days=days)


def _between(user: User, start: datetime | None, end: datetime | None):
    """user_id plus an optional window: start inclusive, end exclusive."""
    conditions = [Scrobble.user_id == user.id]
    if start is not None:
        conditions.append(Scrobble.played_at >= start)
    if end is not None:
        conditions.append(Scrobble.played_at < end)
    return conditions


def _user_scrobbles(user: User, period: str = "overall"):
    return _between(user, since(period), None)


def _display(column):
    """Most common spelling within a case-insensitive group. Ties go to the capitalised
    spelling: the C collation sorts uppercase first, regardless of the server locale."""
    return func.mode().within_group(column.collate("C"))


def _paginate(query: Select, page: int, per_page: int) -> tuple[list, int]:
    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.session.execute(query.limit(per_page).offset((page - 1) * per_page)).all()
    return rows, total


def recent(
    user: User,
    page: int = 1,
    per_page: int = 50,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Page:
    conditions = [Scrobble.user_id == user.id]
    if start:
        conditions.append(Scrobble.played_at >= start)
    if end:
        conditions.append(Scrobble.played_at <= end)
    query = select(Scrobble).where(*conditions).order_by(Scrobble.played_at.desc())
    rows, total = _paginate(query, page, per_page)
    return Page([row[0] for row in rows], page, per_page, total)


def top_artists(user: User, period: str = "overall", page: int = 1, per_page: int = 50) -> Page:
    key = func.lower(Scrobble.artist)
    plays = func.count().label("plays")
    query = (
        select(_display(Scrobble.artist), plays)
        .where(*_user_scrobbles(user, period))
        .group_by(key)
        .order_by(plays.desc(), key)
    )
    rows, total = _paginate(query, page, per_page)
    offset = (page - 1) * per_page
    items = [TopItem(offset + i + 1, name, count) for i, (name, count) in enumerate(rows)]
    return Page(items, page, per_page, total)


def top_albums(user: User, period: str = "overall", page: int = 1, per_page: int = 50) -> Page:
    album_artist = func.coalesce(Scrobble.album_artist, Scrobble.artist)
    keys = (func.lower(Scrobble.album), func.lower(album_artist))
    plays = func.count().label("plays")
    query = (
        select(_display(Scrobble.album), _display(album_artist), plays)
        .where(*_user_scrobbles(user, period), Scrobble.album.is_not(None))
        .group_by(*keys)
        .order_by(plays.desc(), *keys)
    )
    rows, total = _paginate(query, page, per_page)
    offset = (page - 1) * per_page
    items = [
        TopItem(offset + i + 1, name, count, artist=artist)
        for i, (name, artist, count) in enumerate(rows)
    ]
    return Page(items, page, per_page, total)


def top_tracks(user: User, period: str = "overall", page: int = 1, per_page: int = 50) -> Page:
    keys = (func.lower(Scrobble.track), func.lower(Scrobble.artist))
    plays = func.count().label("plays")
    query = (
        select(
            _display(Scrobble.track),
            _display(Scrobble.artist),
            func.max(Scrobble.duration),
            plays,
        )
        .where(*_user_scrobbles(user, period))
        .group_by(*keys)
        .order_by(plays.desc(), *keys)
    )
    rows, total = _paginate(query, page, per_page)
    offset = (page - 1) * per_page
    items = [
        TopItem(offset + i + 1, name, count, artist=artist, duration=duration)
        for i, (name, artist, duration, count) in enumerate(rows)
    ]
    return Page(items, page, per_page, total)


def listen_counts(user: User, period: str = "1month", bucket: str = "day") -> list[dict]:
    """Scrobbles per bucket (UTC). Empty buckets are included for day buckets."""
    if bucket not in BUCKETS:  # interpolated into SQL below, so allow-list it
        raise ValueError(f"bucket must be one of {BUCKETS}")
    bucket_col = func.date_trunc(literal_column(f"'{bucket}'"), Scrobble.played_at, "UTC")
    query = (
        select(bucket_col.label("bucket"), func.count())
        .where(*_user_scrobbles(user, period))
        .group_by("bucket")
        .order_by("bucket")
    )
    counts = {row[0].date(): row[1] for row in db.session.execute(query)}
    if bucket == "day" and (start := since(period)) is not None:
        day, today = start.date(), datetime.now(UTC).date()
        filled: dict[date, int] = {}
        while day <= today:
            filled[day] = counts.get(day, 0)
            day += timedelta(days=1)
        counts = filled
    return [{"start": day, "count": count} for day, count in counts.items()]


def scrobble_count_between(user: User, start: datetime, end: datetime) -> int:
    """Total plays in [start, end)."""
    return db.session.scalar(select(func.count()).where(*_between(user, start, end))) or 0


def top_artists_between(
    user: User, start: datetime, end: datetime, limit: int = 10
) -> list[TopItem]:
    """Top artists in [start, end), unpaginated (callers like weekly summaries want a
    handful, not a full listing)."""
    key = func.lower(Scrobble.artist)
    plays = func.count().label("plays")
    query = (
        select(_display(Scrobble.artist), plays)
        .where(*_between(user, start, end))
        .group_by(key)
        .order_by(plays.desc(), key)
        .limit(limit)
    )
    rows = db.session.execute(query).all()
    return [TopItem(i + 1, name, count) for i, (name, count) in enumerate(rows)]


def top_tracks_between(
    user: User, start: datetime, end: datetime, limit: int = 10
) -> list[TopItem]:
    """Top tracks in [start, end), unpaginated."""
    keys = (func.lower(Scrobble.track), func.lower(Scrobble.artist))
    plays = func.count().label("plays")
    query = (
        select(
            _display(Scrobble.track),
            _display(Scrobble.artist),
            func.max(Scrobble.duration),
            plays,
        )
        .where(*_between(user, start, end))
        .group_by(*keys)
        .order_by(plays.desc(), *keys)
        .limit(limit)
    )
    rows = db.session.execute(query).all()
    return [
        TopItem(i + 1, name, count, artist=artist, duration=duration)
        for i, (name, artist, duration, count) in enumerate(rows)
    ]


def summary(user: User) -> dict:
    row = db.session.execute(
        select(
            func.count(),
            func.count(func.distinct(func.lower(Scrobble.artist))),
            func.count(
                func.distinct(func.lower(Scrobble.artist) + "\x1f" + func.lower(Scrobble.track))
            ),
            func.min(Scrobble.played_at),
        ).where(Scrobble.user_id == user.id)
    ).one()
    return {
        "scrobbles": row[0],
        "artists": row[1],
        "tracks": row[2],
        "first_scrobble_at": row[3],
    }
