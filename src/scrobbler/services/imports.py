"""Importing listening history: Last.fm export files and pulls from the Last.fm API.

Jobs are created by the API and processed by the import worker (`flask imports
worker`), because large histories take longer than a request should.

Supported inputs:
  * CSV from lastfm-to-csv (no header: artist, album, track, date) or any CSV/TSV
    with a header naming the columns (artist, album, track/name/title, and one of
    uts/timestamp/date/utc_time). Comma, semicolon and tab delimiters are detected.
  * JSON: an array of user.getRecentTracks pages (e.g. lastfm.ghan.nl exports),
    a single {"recenttracks": ...} response, or a flat list of track objects.
  * The Last.fm API: user.getRecentTracks for a Last.fm username, paged, rate
    limited and resumable.
"""

import csv
import gzip
import io
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from scrobbler import events, metrics
from scrobbler.extensions import db
from scrobbler.models import ImportJob, Scrobble, User
from scrobbler.services.scrobbles import MAX_TEXT, TrackInput

log = logging.getLogger(__name__)

SOURCES = ("csv", "json", "lastfm")
ACTIVE = ("pending", "running")
CHUNK = 1000
GZIP_MAGIC = b"\x1f\x8b"


class ImportFailed(ValueError):
    """An import can't be started or its input can't be read."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(UTC)


# --- Parsing -----------------------------------------------------------------------

_DATE_FORMATS = (
    "%d %b %Y %H:%M",  # lastfm-to-csv: "31 Jan 2021 14:05"
    "%d %b %Y, %H:%M",  # Last.fm API "#text": "31 Jan 2021, 14:05"
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M",
)


def parse_time(value: str | int | None) -> int | None:
    """Unix seconds from a Unix time (seconds or milliseconds) or a UTC date string."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return number // 1000 if number > 10**11 else number
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp())
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=UTC).timestamp())
        except ValueError:
            continue
    return None


def _clean(value) -> str | None:
    if isinstance(value, dict):  # {"#text": "...", "mbid": "..."} or {"name": "..."}
        value = value.get("#text", value.get("name"))
    text = ("" if value is None else str(value)).strip()
    return text[:MAX_TEXT] or None


def _mbid(value) -> str | None:
    mbid = _clean(value)
    return mbid if mbid and len(mbid) <= 36 else None


_HEADER_ALIASES = {
    "artist": {"artist", "artist_name", "artistname"},
    "album": {"album", "album_name", "albumname"},
    "track": {"track", "track_name", "trackname", "name", "title", "song"},
    "time": {"uts", "timestamp", "date", "utc_time", "time", "played_at", "datetime", "date#"},
    "album_artist": {"album_artist", "albumartist"},
    "mbid": {"track_mbid", "mbid"},
}


def _header_map(row: list[str]) -> dict[str, int] | None:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(row):
        key = cell.strip().lower().replace(" ", "_")
        for field, aliases in _HEADER_ALIASES.items():
            if key in aliases and field not in mapping:
                mapping[field] = index
    return mapping if {"artist", "track", "time"} <= mapping.keys() else None


def parse_csv(text: str) -> Iterator[TrackInput | None]:
    """Yields a TrackInput per row, or None for rows that can't be used."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    first = next(reader, None)
    if first is None:
        return
    columns = _header_map(first)
    rows: Iterable[list[str]] = reader
    if columns is None:
        # lastfm-to-csv has no header: artist, album, track, date
        columns = {"artist": 0, "album": 1, "track": 2, "time": 3}
        rows = _prepend(first, reader)

    def cell(row: list[str], field: str) -> str | None:
        index = columns.get(field)
        return row[index] if index is not None and index < len(row) else None

    for row in rows:
        if not any(c.strip() for c in row):
            continue
        timestamp = parse_time(cell(row, "time"))
        if timestamp is None:
            yield None
            continue
        yield TrackInput(
            artist=_clean(cell(row, "artist")) or "",
            track=_clean(cell(row, "track")) or "",
            album=_clean(cell(row, "album")),
            album_artist=_clean(cell(row, "album_artist")),
            mbid=_mbid(cell(row, "mbid")),
            timestamp=timestamp,
        )


def _prepend(first, rest):
    yield first
    yield from rest


def _track_from_json(track: dict) -> TrackInput | None:
    """A track object as found in user.getRecentTracks (compact or extended)."""
    if not isinstance(track, dict):
        return None
    if (track.get("@attr") or {}).get("nowplaying") == "true":
        return None  # no timestamp: it's still playing
    date = track.get("date")
    timestamp = parse_time(
        date.get("uts") or date.get("#text")
        if isinstance(date, dict)
        else date or track.get("uts") or track.get("timestamp")
    )
    if timestamp is None:
        return None
    return TrackInput(
        artist=_clean(track.get("artist")) or "",
        track=_clean(track.get("name") or track.get("track") or track.get("title")) or "",
        album=_clean(track.get("album")),
        mbid=_mbid(track.get("mbid")),
        timestamp=timestamp,
    )


def _json_tracks(data) -> Iterator[dict]:
    if isinstance(data, dict):
        if "recenttracks" in data:
            data = data["recenttracks"]
        if "track" in data:
            tracks = data["track"]
            yield from tracks if isinstance(tracks, list) else [tracks]
            return
        raise ImportFailed("unrecognised_json", "The JSON doesn't look like a Last.fm export.")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and (
                "recenttracks" in item or isinstance(item.get("track"), list | dict)
            ):
                yield from _json_tracks(item)  # a page of tracks
            else:
                yield item  # a flat list of tracks
        return
    raise ImportFailed("unrecognised_json", "The JSON doesn't look like a Last.fm export.")


def parse_json(text: str) -> Iterator[TrackInput | None]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ImportFailed("invalid_json", f"The file isn't valid JSON: {err.msg}") from err
    for track in _json_tracks(data):
        yield _track_from_json(track)


def detect_format(filename: str | None, raw: bytes) -> str:
    name = (filename or "").lower().removesuffix(".gz")
    if name.endswith(".json"):
        return "json"
    if name.endswith((".csv", ".tsv", ".txt")):
        return "csv"
    if raw[:2] == GZIP_MAGIC:
        with gzip.open(io.BytesIO(raw)) as unzipped:
            raw = unzipped.read(64)
    return "json" if raw.lstrip()[:1] in (b"[", b"{") else "csv"


def _decode(raw: bytes) -> str:
    if raw[:2] == GZIP_MAGIC:  # a gzipped export
        raw = gzip.decompress(raw)
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


# --- Jobs ----------------------------------------------------------------------------


def lastfm_pull_available() -> bool:
    return bool(current_app.config.get("LASTFM_API_KEY"))


def create_file_import(
    user: User, filename: str | None, raw: bytes, fmt: str = "auto"
) -> ImportJob:
    if not raw.strip():
        raise ImportFailed("empty_file", "The file is empty.")
    source = detect_format(filename, raw) if fmt == "auto" else fmt
    if source not in ("csv", "json"):
        raise ImportFailed("unsupported_format", "Upload a CSV or JSON export.")
    job = ImportJob(
        user_id=user.id,
        source=source,
        filename=(filename or "upload")[:255],
        payload=gzip.compress(raw, compresslevel=6),
    )
    db.session.add(job)
    db.session.commit()
    metrics.imports_total.labels(source=source, status="created").inc()
    return job


def create_lastfm_import(user: User, lastfm_username: str) -> ImportJob:
    if not lastfm_pull_available():
        raise ImportFailed(
            "lastfm_not_configured", "Importing from Last.fm isn't set up on this server."
        )
    username = lastfm_username.strip()
    if not username:
        raise ImportFailed("missing_username", "Enter your Last.fm username.")
    job = ImportJob(
        user_id=user.id,
        source="lastfm",
        lastfm_username=username[:64],
        next_page=1,
        pull_until=_now(),
    )
    db.session.add(job)
    db.session.commit()
    metrics.imports_total.labels(source="lastfm", status="created").inc()
    return job


def list_jobs(user: User, limit: int = 20) -> list[ImportJob]:
    return list(
        db.session.scalars(
            db.select(ImportJob)
            .filter_by(user_id=user.id)
            .order_by(ImportJob.created_at.desc())
            .limit(limit)
        )
    )


def get_job(user: User, job_id: int) -> ImportJob | None:
    return db.session.scalar(db.select(ImportJob).filter_by(id=job_id, user_id=user.id))


def cancel_job(user: User, job_id: int) -> ImportJob | None:
    job = get_job(user, job_id)
    if job is None:
        return None
    if job.status in ACTIVE:
        job.status = "cancelled"
        job.finished_at = _now()
        job.payload = None
        db.session.commit()
        metrics.imports_total.labels(source=job.source, status="cancelled").inc()
        events.send(events.import_finished, user, job=job)
    return job


# --- Storing -------------------------------------------------------------------------


def _usable(item: TrackInput | None, now: datetime) -> bool:
    """Imports keep old plays (that's the point), but not empty or future ones."""
    if item is None or not item.artist or not item.track:
        return False
    limit = current_app.config["SCROBBLE_MAX_FUTURE_SECONDS"]
    return datetime.fromtimestamp(item.timestamp, UTC) - now <= timedelta(seconds=limit)


def store_chunk(job: ImportJob, items: list[TrackInput | None]) -> list[TrackInput]:
    """Insert one chunk and update the job's counters (the caller commits).

    Returns the plays that were newly stored (not duplicates or skipped rows).
    """
    now = _now()
    rows, seen = [], {}
    skipped = repeated = 0
    for item in items:
        if not _usable(item, now):
            skipped += 1
            continue
        played_at = datetime.fromtimestamp(item.timestamp, UTC)
        key = (played_at, item.artist, item.track)
        if key in seen:  # the same play twice in one chunk: ON CONFLICT can't handle that
            repeated += 1
            continue
        seen[key] = item
        rows.append(
            {
                "user_id": job.user_id,
                "artist": item.artist,
                "track": item.track,
                "album": item.album,
                "album_artist": item.album_artist,
                "mbid": item.mbid,
                "played_at": played_at,
                "submitted_at": now,
            }
        )
    stored: list[TrackInput] = []
    if rows:
        stored = [
            seen[tuple(row)]
            for row in db.session.execute(
                insert(Scrobble)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_scrobbles_dedupe")
                .returning(Scrobble.played_at, Scrobble.artist, Scrobble.track)
            )
        ]
    inserted = len(stored)
    duplicates = len(rows) - inserted + repeated
    job.imported += inserted
    job.duplicates += duplicates
    job.skipped += skipped
    job.processed += len(items)
    job.heartbeat_at = now
    counter = metrics.imported_scrobbles_total
    counter.labels(source=job.source, result="imported").inc(inserted)
    counter.labels(source=job.source, result="duplicate").inc(duplicates)
    counter.labels(source=job.source, result="skipped").inc(skipped)
    return stored


def _store_and_commit(job: ImportJob, items: list[TrackInput | None]) -> None:
    stored = store_chunk(job, items)
    db.session.commit()
    if stored:
        user = db.session.get(User, job.user_id)
        events.send(events.scrobbles_stored, user, scrobbles=stored, source="import")


def _cancelled(job: ImportJob) -> bool:
    status = db.session.scalar(db.select(ImportJob.status).filter_by(id=job.id))
    return status == "cancelled"


def _chunks(items: Iterable, size: int = CHUNK) -> Iterator[list]:
    chunk = []
    for item in items:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _process_file(job: ImportJob) -> None:
    text = _decode(gzip.decompress(job.payload))
    parse = parse_json if job.source == "json" else parse_csv
    items = list(parse(text))
    job.total = len(items)
    db.session.commit()
    for chunk in _chunks(items):
        if _cancelled(job):
            return
        _store_and_commit(job, chunk)


# --- Last.fm API pull ------------------------------------------------------------------


class LastfmClient:
    def __init__(self, api_url: str, api_key: str, interval: float, retries: int = 4):
        self.api_url, self.api_key = api_url, api_key
        self.interval, self.retries = interval, retries
        self._last_call = 0.0

    def recent_tracks(self, username: str, page: int, until: datetime) -> dict:
        params = {
            "method": "user.getRecentTracks",
            "user": username,
            "api_key": self.api_key,
            "format": "json",
            "limit": "200",
            "page": str(page),
            "to": str(int(until.timestamp())),
        }
        url = f"{self.api_url}?{urllib.parse.urlencode(params)}"
        for attempt in range(self.retries + 1):
            wait = self.interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    body = json.load(response)
            except urllib.error.HTTPError as err:
                body = _json_or_none(err)
                code = (body or {}).get("error")
                if code == 6:  # "User not found"
                    raise ImportFailed(
                        "lastfm_user_not_found", "No Last.fm user with that name."
                    ) from err
                if code in (10, 26):
                    raise ImportFailed(
                        "lastfm_key_rejected", "Last.fm rejected this server's API key."
                    ) from err
                if attempt == self.retries or (err.code < 500 and code != 29):
                    raise ImportFailed(
                        "lastfm_error", f"Last.fm returned an error ({err.code})."
                    ) from err
            except (urllib.error.URLError, TimeoutError) as err:
                if attempt == self.retries:
                    raise ImportFailed("lastfm_unreachable", "Couldn't reach Last.fm.") from err
            else:
                if "error" in body:
                    raise ImportFailed(
                        "lastfm_error", body.get("message", "Last.fm returned an error.")
                    )
                return body
            time.sleep(min(30, 2**attempt))  # back off before retrying
        raise ImportFailed("lastfm_unreachable", "Couldn't reach Last.fm.")


def _json_or_none(err: urllib.error.HTTPError) -> dict | None:
    try:
        return json.load(err)
    except (json.JSONDecodeError, ValueError):
        return None


def _process_lastfm(job: ImportJob) -> None:
    config = current_app.config
    client = LastfmClient(
        config["LASTFM_API_URL"], config["LASTFM_API_KEY"], config["LASTFM_REQUEST_INTERVAL"]
    )
    page = job.next_page or 1
    while True:
        if _cancelled(job):
            return
        body = client.recent_tracks(job.lastfm_username, page, job.pull_until)
        recent = body.get("recenttracks", {})
        attrs = recent.get("@attr", {})
        total_pages = int(attrs.get("totalPages") or 0)
        job.total = int(attrs.get("total") or 0)
        tracks = list(_json_tracks(body))
        job.next_page = page + 1
        _store_and_commit(job, [_track_from_json(t) for t in tracks])
        if page >= total_pages or not tracks:
            return
        page += 1


# --- Worker ----------------------------------------------------------------------------


def claim_next_job() -> ImportJob | None:
    """Atomically take the oldest pending job (safe with several workers)."""
    job = db.session.scalar(
        db.select(ImportJob)
        .filter_by(status="pending")
        .order_by(ImportJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        db.session.rollback()
        return None
    job.status = "running"
    job.started_at = job.started_at or _now()
    job.heartbeat_at = _now()
    db.session.commit()
    return job


def requeue_stalled(max_age: timedelta = timedelta(minutes=5)) -> int:
    """Put back jobs whose worker died mid-way (no heartbeat for a while)."""
    result = db.session.execute(
        update(ImportJob)
        .where(ImportJob.status == "running", ImportJob.heartbeat_at < _now() - max_age)
        .values(status="pending")
    )
    db.session.commit()
    return result.rowcount


def process_job(job: ImportJob) -> None:
    started = time.perf_counter()
    try:
        if job.source == "lastfm":
            _process_lastfm(job)
        else:
            _process_file(job)
    except ImportFailed as err:
        db.session.rollback()
        _finish(job, "failed", err.message)
    except Exception:
        log.exception("import job %s failed", job.id)
        db.session.rollback()
        _finish(
            job, "failed", "The import failed unexpectedly. Try again, or check the server logs."
        )
    else:
        if not _cancelled(job):
            _finish(job, "completed")
    metrics.import_duration_seconds.labels(source=job.source).observe(time.perf_counter() - started)


def _finish(job: ImportJob, status: str, error: str | None = None) -> None:
    job = db.session.get(ImportJob, job.id)  # reload after a rollback
    job.status, job.error, job.finished_at = status, error, _now()
    job.payload = None
    db.session.commit()
    metrics.imports_total.labels(source=job.source, status=status).inc()
    events.send(events.import_finished, db.session.get(User, job.user_id), job=job)


def work_once() -> bool:
    """Worker task: process the oldest waiting import, if any."""
    job = claim_next_job()
    if job is None:
        return False
    log.info("import job %s (%s) for user %s started", job.id, job.source, job.user_id)
    process_job(job)
    log.info("import job %s finished", job.id)
    return True


def register_worker_tasks() -> None:
    from scrobbler import worker

    worker.register_task("imports", work_once)
    worker.register_periodic("imports.requeue_stalled", 60, requeue_stalled)
