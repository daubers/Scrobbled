"""Album art: MusicBrainz release-group search + Cover Art Archive, both free and keyless.

Every (artist, album) lookup is cached in `AlbumArt` — a miss or an error is cached too, so
repeat requests for an album we can't find art for don't keep hammering the upstream APIs.
`lookup()` resolves *where* the art is (and never raises); `fetch_image()` streams the bytes
from there.

`lookup()` itself is still a live, synchronous resolution — fine for its one caller
(`federation/publishing/now_playing.py`, already running from a background worker task, not
a request). The web-facing `/api/v1/art/album` endpoint does NOT call `lookup()`: it only
ever reads via `get_cached()`/`read_file()`, both pure local reads with no network call, so
a page can never be slowed down by a MusicBrainz/Cover Art Archive round trip. A cache miss
there calls `want()` (a fast DB write, "please resolve this") instead, and a background
worker task (`resolve_one_pending()`, registered by `register_worker_tasks()`) does the
actual resolving plus downloading the image bytes to `ART_STORAGE_DIR`, completely
decoupled from any request.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import current_app
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from scrobbler import metrics
from scrobbler.extensions import db
from scrobbler.models import AlbumArt, Scrobble, UserAlbumArt

log = logging.getLogger(__name__)


@contextmanager
def _upstream_call(service: str):
    """Times one upstream call and counts its failures (the error is re-raised)."""
    start = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except Exception as err:
        outcome = "error"
        metrics.art_upstream_errors_total.labels(service, type(err).__name__).inc()
        raise
    finally:
        metrics.art_upstream_seconds.labels(service, outcome).observe(time.perf_counter() - start)


# Lucene special characters MusicBrainz's search syntax requires escaping.
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def _escape(value: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", value)


@dataclass
class ArtResult:
    status: str  # "found", "not_found" or "error"
    image_url: str | None = None


@dataclass
class ImageBytes:
    body: bytes
    content_type: str


class ArtFetchFailed(Exception):
    """The resolved image URL couldn't be fetched."""


def _user_agent() -> str:
    from scrobbler import __version__

    contact = current_app.config["MUSICBRAINZ_CONTACT"]
    return f"Scrobbler/{__version__} ( {contact} )"


_last_musicbrainz_call = 0.0


def _throttled_request(url: str, method: str = "GET") -> urllib.request.Request:
    global _last_musicbrainz_call
    interval = current_app.config["MUSICBRAINZ_REQUEST_INTERVAL"]
    wait = interval - (time.monotonic() - _last_musicbrainz_call)
    if wait > 0:
        time.sleep(wait)
    _last_musicbrainz_call = time.monotonic()
    return urllib.request.Request(url, method=method, headers={"User-Agent": _user_agent()})


def _stale(row: AlbumArt) -> bool:
    if row.status == "found":
        return False
    now = datetime.now(UTC)
    if row.status == "error":
        threshold = timedelta(seconds=current_app.config["ART_ERROR_RETRY_SECONDS"])
    else:
        threshold = timedelta(days=current_app.config["ART_NOT_FOUND_RETRY_DAYS"])
    return now - row.checked_at > threshold


def _find_release_group(artist: str, album: str) -> str | None:
    """A matching release-group's MBID, or None if MusicBrainz has no match."""
    api_url = current_app.config["MUSICBRAINZ_API_URL"]
    query = f'releasegroup:"{_escape(album)}" AND artist:"{_escape(artist)}"'
    params = urllib.parse.urlencode({"query": query, "fmt": "json", "limit": "5"})
    retries = current_app.config["MUSICBRAINZ_RETRIES"]
    for attempt in range(retries + 1):
        request = _throttled_request(f"{api_url}release-group/?{params}")
        try:
            with (
                _upstream_call("musicbrainz"),
                urllib.request.urlopen(request, timeout=10) as response,
            ):
                body = json.load(response)
        except urllib.error.HTTPError as err:
            if attempt == retries or err.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
        else:
            groups = body.get("release-groups") or []
            return groups[0]["id"] if groups else None
        time.sleep(min(10, 2**attempt))
    raise TimeoutError("MusicBrainz request exhausted its retries")


def _cover_art_url(release_group_mbid: str) -> str | None:
    """The release-group's front cover URL, or None if Cover Art Archive has none.

    Cover Art Archive redirects to archive.org for the actual image, which is
    occasionally slow — retried the same way as the MusicBrainz search, with a longer
    per-attempt timeout to give that redirect room.
    """
    coverart_url = current_app.config["COVERART_API_URL"]
    url = f"{coverart_url}release-group/{release_group_mbid}/front-500"
    retries = current_app.config["MUSICBRAINZ_RETRIES"]
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, method="HEAD")
        try:
            with _upstream_call("coverart"), urllib.request.urlopen(request, timeout=15):
                return url
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if attempt == retries or err.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(min(10, 2**attempt))
    raise TimeoutError("Cover Art Archive request exhausted its retries")


def get_cached(artist: str, album: str) -> AlbumArt | None:
    """Plain cache read by (artist, album). No network, no write."""
    return db.session.scalar(
        select(AlbumArt).where(
            func.lower(AlbumArt.artist) == artist.lower(),
            func.lower(AlbumArt.album) == album.lower(),
        )
    )


def _resolve(artist: str, album: str) -> tuple[ArtResult, str | None, str | None]:
    """Live MusicBrainz + Cover Art Archive resolution, no caching involved. Returns
    (result, release_group_mbid, error_code). Never raises."""
    release_group_mbid = None
    try:
        release_group_mbid = _find_release_group(artist, album)
        image_url = _cover_art_url(release_group_mbid) if release_group_mbid else None
        status = "found" if image_url else "not_found"
        return ArtResult(status, image_url), release_group_mbid, None
    except Exception as err:  # noqa: BLE001 - any upstream failure degrades to a cached "error"
        log.warning("Album art lookup failed for %r/%r: %s", artist, album, err)
        return ArtResult("error"), release_group_mbid, type(err).__name__


def _store(
    artist: str,
    album: str,
    result: ArtResult,
    release_group_mbid: str | None,
    error_code: str | None,
) -> None:
    values = {
        "artist": artist,
        "album": album,
        "status": result.status,
        "image_url": result.image_url,
        "release_group_mbid": release_group_mbid,
        "error_code": error_code,
        "checked_at": datetime.now(UTC),
    }
    stmt = insert(AlbumArt).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[func.lower(AlbumArt.artist), func.lower(AlbumArt.album)], set_=values
    )
    db.session.execute(stmt)
    db.session.commit()


def lookup(artist: str, album: str) -> ArtResult:
    """Cached (artist, album) art resolution, resolving live on a cache miss. Never
    raises. Used by ActivityPub posting, which already runs from a worker task - not
    used by the web-facing endpoint, which must never block on a live lookup (see
    get_cached()/want() below)."""
    existing = get_cached(artist, album)
    if existing is not None and not _stale(existing):
        return ArtResult(existing.status, existing.image_url)
    result, release_group_mbid, error_code = _resolve(artist, album)
    metrics.art_resolutions_total.labels("inline", result.status).inc()
    _store(artist, album, result, release_group_mbid, error_code)
    return result


def want(artist: str, album: str) -> None:
    """Record that we'd like art for (artist, album), for the background worker to
    resolve. A fast, network-free write; a no-op if a row already exists (any status)."""
    stmt = insert(AlbumArt).values(
        artist=artist, album=album, status="pending", checked_at=datetime.now(UTC)
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[func.lower(AlbumArt.artist), func.lower(AlbumArt.album)]
    )
    db.session.execute(stmt)
    db.session.commit()


def requeue(row: AlbumArt) -> None:
    """Asks the worker to (re)resolve a row - used when a 'found' row's file has gone
    missing (e.g. one resolved before local file storage existed)."""
    row.status = "pending"
    db.session.commit()


def _file_path(row_id: int) -> Path:
    return Path(current_app.config["ART_STORAGE_DIR"]) / f"{row_id}.jpg"


def read_file(row_id: int) -> bytes | None:
    """The downloaded image bytes for a resolved row, or None if not downloaded."""
    try:
        return _file_path(row_id).read_bytes()
    except FileNotFoundError:
        return None


def fetch_image(image_url: str) -> ImageBytes:
    """Streams the bytes from image_url. Raises ArtFetchFailed on any problem."""
    try:
        with _upstream_call("download"), urllib.request.urlopen(image_url, timeout=10) as response:
            return ImageBytes(response.read(), response.headers.get("Content-Type", "image/jpeg"))
    except (urllib.error.URLError, TimeoutError) as err:
        raise ArtFetchFailed(str(err)) from err


# --- Per-user overrides --------------------------------------------------------------
#
# A user's own art for an album (an upload, or a manual correction the worker resolves)
# beats the global cache for that user only. Like the rest of the web path, nothing here
# touches the network: a correction is just a queued row.

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


class UnsupportedImage(Exception):
    """The bytes aren't a JPEG, PNG or WebP image."""


def sniff_image_type(data: bytes) -> str:
    """The image's content type from its magic bytes. Raises UnsupportedImage."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise UnsupportedImage


def _override_path(row_id: int) -> Path:
    return Path(current_app.config["ART_STORAGE_DIR"]) / "user" / str(row_id)


def _override_key(user_id: int, artist: str, album: str):
    return and_(
        UserAlbumArt.user_id == user_id,
        func.lower(UserAlbumArt.artist) == artist.lower(),
        func.lower(UserAlbumArt.album) == album.lower(),
    )


def get_override(user_id: int, artist: str, album: str) -> UserAlbumArt | None:
    return db.session.scalar(select(UserAlbumArt).where(_override_key(user_id, artist, album)))


def read_override_file(row: UserAlbumArt) -> bytes | None:
    try:
        return _override_path(row.id).read_bytes()
    except FileNotFoundError:
        return None


def _upsert_override(user_id: int, artist: str, album: str, **values) -> int:
    now = datetime.now(UTC)
    stmt = insert(UserAlbumArt).values(
        user_id=user_id, artist=artist, album=album, created_at=now, updated_at=now, **values
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            UserAlbumArt.user_id,
            func.lower(UserAlbumArt.artist),
            func.lower(UserAlbumArt.album),
        ],
        set_={**values, "updated_at": now},
    ).returning(UserAlbumArt.id)
    row_id = db.session.execute(stmt).scalar_one()
    db.session.commit()
    return row_id


def save_upload(user_id: int, artist: str, album: str, data: bytes, content_type: str) -> None:
    """Store an uploaded image as the user's art. The caller has already validated it."""
    path_id = _upsert_override(
        user_id,
        artist,
        album,
        kind="upload",
        status="found",
        content_type=content_type,
        search_artist=None,
        search_album=None,
        release_group_mbid=None,
        error_code=None,
    )
    path = _override_path(path_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def request_correction(
    user_id: int,
    artist: str,
    album: str,
    search_artist: str | None,
    search_album: str | None,
    release_group_mbid: str | None,
) -> None:
    """Queue a corrected lookup (different search text, or a known MusicBrainz
    release-group) for the worker. A fast, network-free write."""
    _upsert_override(
        user_id,
        artist,
        album,
        kind="correction",
        status="pending",
        content_type=None,
        search_artist=search_artist,
        search_album=search_album,
        release_group_mbid=release_group_mbid,
        error_code=None,
    )
    metrics.art_override_actions_total.labels("correction_requested").inc()


def delete_override(user_id: int, artist: str, album: str) -> bool:
    """Drops the user's override (and its image). False if there wasn't one."""
    row = get_override(user_id, artist, album)
    if row is None:
        return False
    row_id = row.id
    db.session.execute(delete(UserAlbumArt).where(UserAlbumArt.id == row_id))
    db.session.commit()
    _override_path(row_id).unlink(missing_ok=True)
    metrics.art_override_actions_total.labels("override_deleted").inc()
    return True


def retry(artist: str, album: str) -> bool:
    """Ask the worker to look an album up again from scratch. False if there's nothing
    to retry (it's already found, or already waiting)."""
    row = get_cached(artist, album)
    if row is None:
        want(artist, album)
    elif row.status in ("not_found", "error"):
        requeue(row)
    else:
        return False
    metrics.art_override_actions_total.labels("retry_requested").inc()
    return True


def list_failures(user_id: int, page: int, limit: int) -> tuple[list[dict], int]:
    """One page of the user's scrobbled albums that have no art (the lookup failed) or
    whose manual correction hasn't worked, most-scrobbled first. Returns (items, total)."""
    albums = (
        select(
            func.min(Scrobble.artist).label("artist"),
            func.min(Scrobble.album).label("album"),
            func.count().label("scrobbles"),
        )
        .where(Scrobble.user_id == user_id, Scrobble.album.is_not(None), Scrobble.album != "")
        .group_by(func.lower(Scrobble.artist), func.lower(Scrobble.album))
        .subquery()
    )
    query = (
        select(albums.c.artist, albums.c.album, albums.c.scrobbles, AlbumArt, UserAlbumArt)
        .select_from(albums)
        .outerjoin(
            AlbumArt,
            and_(
                func.lower(AlbumArt.artist) == func.lower(albums.c.artist),
                func.lower(AlbumArt.album) == func.lower(albums.c.album),
            ),
        )
        .outerjoin(
            UserAlbumArt,
            and_(
                UserAlbumArt.user_id == user_id,
                func.lower(UserAlbumArt.artist) == func.lower(albums.c.artist),
                func.lower(UserAlbumArt.album) == func.lower(albums.c.album),
            ),
        )
        .where(
            or_(
                UserAlbumArt.status.in_(("pending", "error")),
                and_(
                    UserAlbumArt.id.is_(None),
                    AlbumArt.status.in_(("not_found", "error")),
                ),
            )
        )
    )
    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.session.execute(
        query.order_by(albums.c.scrobbles.desc(), albums.c.artist, albums.c.album)
        .limit(limit)
        .offset((page - 1) * limit)
    ).all()
    items = [
        {
            "artist": artist,
            "album": album,
            "scrobbles": scrobbles,
            "status": cached.status if cached else "pending",
            "error_code": cached.error_code if cached else None,
            "checked_at": cached.checked_at if cached else None,
            "override": (
                {
                    "kind": override.kind,
                    "status": override.status,
                    "error_code": override.error_code,
                    "search_artist": override.search_artist,
                    "search_album": override.search_album,
                    "release_group_mbid": override.release_group_mbid,
                }
                if override
                else None
            ),
        }
        for artist, album, scrobbles, cached, override in rows
    ]
    return items, total


# --- Worker --------------------------------------------------------------------------


def _claim_pending() -> AlbumArt | None:
    """Atomically takes one row due for resolution (new, or a stale not_found/error)."""
    not_found_cutoff = datetime.now(UTC) - timedelta(
        days=current_app.config["ART_NOT_FOUND_RETRY_DAYS"]
    )
    error_cutoff = datetime.now(UTC) - timedelta(
        seconds=current_app.config["ART_ERROR_RETRY_SECONDS"]
    )
    row = db.session.scalar(
        select(AlbumArt)
        .where(
            or_(
                AlbumArt.status == "pending",
                (AlbumArt.status == "not_found") & (AlbumArt.checked_at < not_found_cutoff),
                (AlbumArt.status == "error") & (AlbumArt.checked_at < error_cutoff),
            )
        )
        .order_by(AlbumArt.checked_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        db.session.rollback()
    return row


def _claim_correction() -> UserAlbumArt | None:
    row = db.session.scalar(
        select(UserAlbumArt)
        .where(UserAlbumArt.status == "pending")
        .order_by(UserAlbumArt.updated_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        db.session.rollback()
    return row


def _resolve_correction(row: UserAlbumArt) -> tuple[ArtResult, str | None]:
    """Live resolution of a user's correction: a known release-group goes straight to
    Cover Art Archive, otherwise the (corrected) text is searched. Never raises."""
    if not row.release_group_mbid:
        result, _, error_code = _resolve(
            row.search_artist or row.artist, row.search_album or row.album
        )
        return result, error_code
    try:
        image_url = _cover_art_url(row.release_group_mbid)
    except Exception as err:  # noqa: BLE001 - any upstream failure is recorded on the row
        log.warning("Corrected art lookup failed for %r/%r: %s", row.artist, row.album, err)
        return ArtResult("error"), type(err).__name__
    return ArtResult("found" if image_url else "not_found", image_url), None


def resolve_one_correction() -> bool:
    """Resolve one user's pending manual correction, saving the image on success.
    Returns True if it processed one."""
    row = _claim_correction()
    if row is None:
        return False
    start = time.perf_counter()
    source = "mbid" if row.release_group_mbid else "search"
    result, error_code = _resolve_correction(row)
    content_type = None
    if result.status == "found":
        try:
            image = fetch_image(result.image_url)
            content_type = image.content_type.split(";")[0].strip()
            if content_type not in IMAGE_TYPES:
                content_type = "image/jpeg"
            path = _override_path(row.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image.body)
        except ArtFetchFailed as err:
            log.warning("Corrected art download failed for %r/%r: %s", row.artist, row.album, err)
            result, error_code = ArtResult("error"), "ArtFetchFailed"
    elif result.status == "not_found":
        error_code = "NotFound"
    metrics.art_correction_resolutions_total.labels(source, result.status).inc()
    metrics.art_resolutions_total.labels("override", result.status).inc()
    now = datetime.now(UTC)
    metrics.art_correction_wait_seconds.observe((now - row.updated_at).total_seconds())
    row.status = "found" if result.status == "found" else "error"
    row.content_type = content_type
    row.error_code = error_code
    row.updated_at = now
    db.session.commit()
    metrics.art_override_worker_seconds.observe(time.perf_counter() - start)
    return True


def resolve_one_pending() -> bool:
    """Worker task: resolve one row that's pending or due for a recheck, downloading
    and saving its image bytes when found. A user's manual correction goes first.
    Returns True if it processed a row (call again immediately), False if there was
    nothing to do."""
    if resolve_one_correction():
        return True
    row = _claim_pending()
    if row is None:
        return False

    start = time.perf_counter()
    result, release_group_mbid, error_code = _resolve(row.artist, row.album)
    if result.status == "found":
        try:
            image = fetch_image(result.image_url)
            path = _file_path(row.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image.body)
            metrics.art_image_bytes.observe(len(image.body))
        except ArtFetchFailed as err:
            log.warning("Album art download failed for %r/%r: %s", row.artist, row.album, err)
            result, error_code = ArtResult("error"), "ArtFetchFailed"

    metrics.art_resolutions_total.labels("worker", result.status).inc()
    row.status = result.status
    row.image_url = result.image_url
    row.release_group_mbid = release_group_mbid
    row.error_code = error_code
    row.checked_at = datetime.now(UTC)
    db.session.commit()
    metrics.art_worker_resolve_seconds.observe(time.perf_counter() - start)
    log.info("album art for %r/%r: %s", row.artist, row.album, result.status)
    return True


def register_worker_tasks(app) -> None:
    from scrobbler import worker

    worker.register_task(app, "art", resolve_one_pending)
