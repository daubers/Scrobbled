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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import current_app
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from scrobbler.extensions import db
from scrobbler.models import AlbumArt

log = logging.getLogger(__name__)

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
            with urllib.request.urlopen(request, timeout=10) as response:
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
            with urllib.request.urlopen(request, timeout=15):
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
        with urllib.request.urlopen(image_url, timeout=10) as response:
            return ImageBytes(response.read(), response.headers.get("Content-Type", "image/jpeg"))
    except (urllib.error.URLError, TimeoutError) as err:
        raise ArtFetchFailed(str(err)) from err


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


def resolve_one_pending() -> bool:
    """Worker task: resolve one row that's pending or due for a recheck, downloading
    and saving its image bytes when found. Returns True if it processed a row (call
    again immediately), False if there was nothing to do."""
    row = _claim_pending()
    if row is None:
        return False

    result, release_group_mbid, error_code = _resolve(row.artist, row.album)
    if result.status == "found":
        try:
            image = fetch_image(result.image_url)
            path = _file_path(row.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image.body)
        except ArtFetchFailed as err:
            log.warning("Album art download failed for %r/%r: %s", row.artist, row.album, err)
            result, error_code = ArtResult("error"), "ArtFetchFailed"

    row.status = result.status
    row.image_url = result.image_url
    row.release_group_mbid = release_group_mbid
    row.error_code = error_code
    row.checked_at = datetime.now(UTC)
    db.session.commit()
    return True


def register_worker_tasks(app) -> None:
    from scrobbler import worker

    worker.register_task(app, "art", resolve_one_pending)
