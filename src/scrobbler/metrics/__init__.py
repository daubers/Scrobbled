"""All Prometheus metrics for the service, defined in one place.

Labels are deliberately low-cardinality: Flask endpoint names (never raw paths),
Last.fm method names from the handler registry, and small fixed enums. Never add
user, api_key or track labels.
"""

import platform

from prometheus_client import Counter, Gauge, Histogram

from scrobbler import __version__

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

# HTTP (RED)
http_requests_total = Counter(
    "scrobbler_http_requests_total",
    "HTTP requests handled",
    ["endpoint", "http_method", "status"],
)
http_request_duration_seconds = Histogram(
    "scrobbler_http_request_duration_seconds",
    "HTTP request latency",
    ["endpoint", "http_method"],
    buckets=LATENCY_BUCKETS,
)
http_requests_in_progress = Gauge(
    "scrobbler_http_requests_in_progress",
    "HTTP requests currently being handled",
    ["endpoint"],
    multiprocess_mode="livesum",
)

# Last.fm API
lastfm_requests_total = Counter(
    "scrobbler_lastfm_requests_total",
    "Last.fm API calls",
    ["lastfm_method", "format", "outcome"],
)
lastfm_request_duration_seconds = Histogram(
    "scrobbler_lastfm_request_duration_seconds",
    "Last.fm API call latency",
    ["lastfm_method"],
    buckets=LATENCY_BUCKETS,
)
lastfm_errors_total = Counter(
    "scrobbler_lastfm_errors_total",
    "Last.fm API errors by error code",
    ["lastfm_method", "code"],
)

# Scrobbling
scrobbles_total = Counter(
    "scrobbler_scrobbles_total",
    "Scrobbles submitted, by result",
    ["result"],
)
scrobbles_ignored_total = Counter(
    "scrobbler_scrobbles_ignored_total",
    "Scrobbles ignored, by Last.fm ignored-message code",
    ["reason"],
)
scrobble_batch_size = Histogram(
    "scrobbler_scrobble_batch_size",
    "Tracks per track.scrobble call",
    buckets=(1, 2, 5, 10, 25, 50),
)
scrobble_lag_seconds = Histogram(
    "scrobbler_scrobble_lag_seconds",
    "Delay between a track being played and its scrobble arriving",
    buckets=(10, 60, 300, 900, 3600, 6 * 3600, 86400, 7 * 86400, 14 * 86400),
)
now_playing_updates_total = Counter(
    "scrobbler_now_playing_updates_total",
    "track.updateNowPlaying calls that were stored",
)

# Auth
auth_sessions_created_total = Counter(
    "scrobbler_auth_sessions_created_total",
    "Last.fm session keys issued",
    ["flow"],
)
auth_failures_total = Counter(
    "scrobbler_auth_failures_total",
    "Authentication failures",
    ["reason"],
)
ui_logins_total = Counter(
    "scrobbler_ui_logins_total",
    "Web UI login attempts",
    ["result"],
)
registrations_total = Counter(
    "scrobbler_registrations_total",
    "User registrations",
)

# History imports (counted by the import worker)
imports_total = Counter(
    "scrobbler_imports_total",
    "Import jobs by source and lifecycle event (created, completed, failed, cancelled)",
    ["source", "status"],
)
imported_scrobbles_total = Counter(
    "scrobbler_imported_scrobbles_total",
    "Rows read by imports, by outcome",
    ["source", "result"],
)
import_duration_seconds = Histogram(
    "scrobbler_import_duration_seconds",
    "Time to process an import job",
    ["source"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200),
)

# Background worker
worker_tasks_total = Counter(
    "scrobbler_worker_tasks_total",
    "Worker task runs that did work (queue tasks) or ran (periodic tasks), and failures",
    ["task", "outcome"],
)

# Album art (endpoint + background resolver)
art_requests_total = Counter(
    "scrobbler_art_requests_total",
    "Album art endpoint responses: served, queued (first sighting), pending (still "
    "resolving), unavailable (no art found), file_missing (re-queued)",
    ["result"],
)
art_resolutions_total = Counter(
    "scrobbler_art_resolutions_total",
    "Album art resolutions by outcome (found, not_found, error) and caller (worker, inline)",
    ["source", "result"],
)
art_upstream_seconds = Histogram(
    "scrobbler_art_upstream_seconds",
    "Latency of one album art upstream call, by service (musicbrainz, coverart, download)",
    ["service", "outcome"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40),
)
art_upstream_errors_total = Counter(
    "scrobbler_art_upstream_errors_total",
    "Failed album art upstream calls, by service and exception class",
    ["service", "error"],
)
art_worker_resolve_seconds = Histogram(
    "scrobbler_art_worker_resolve_seconds",
    "Time for the worker to resolve and download one album's art",
    buckets=(0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80),
)
art_image_bytes = Histogram(
    "scrobbler_art_image_bytes",
    "Size of downloaded album art images",
    buckets=(5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000),
)

# Album art: per-user uploads and corrections
art_uploads_total = Counter(
    "scrobbler_art_uploads_total",
    "Album art upload attempts: ok, empty, too_large or bad_type",
    ["result"],
)
art_upload_bytes = Histogram(
    "scrobbler_art_upload_bytes",
    "Size of accepted album art uploads",
    buckets=(5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 5_000_000),
)
art_override_actions_total = Counter(
    "scrobbler_art_override_actions_total",
    "Manual album art actions: correction_requested, retry_requested, override_deleted",
    ["action"],
)
art_correction_resolutions_total = Counter(
    "scrobbler_art_correction_resolutions_total",
    "Worker outcome of a manual correction, by input (mbid or search) and result",
    ["input", "result"],
)
art_correction_wait_seconds = Histogram(
    "scrobbler_art_correction_wait_seconds",
    "Time from a correction being requested to the worker finishing it",
    buckets=(1, 5, 15, 30, 60, 300, 900, 3600, 6 * 3600),
)
art_override_worker_seconds = Histogram(
    "scrobbler_art_override_worker_seconds",
    "Time for the worker to resolve and download one corrected album's art",
    buckets=(0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80),
)

# Database
db_query_duration_seconds = Histogram(
    "scrobbler_db_query_duration_seconds",
    "Database statement latency",
    ["operation"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
db_pool_connections = Gauge(
    "scrobbler_db_pool_connections",
    "Database connection pool state",
    ["state"],
    multiprocess_mode="livesum",
)

build_info = Gauge(
    "scrobbler_build_info",
    "Build information",
    ["version", "python_version"],
    multiprocess_mode="max",
)
build_info.labels(version=__version__, python_version=platform.python_version()).set(1)

# Gauges computed from the database by metrics.collectors.DatabaseStatsCollector.
COLLECTED = {
    "users": "scrobbler_users",
    "active_users": "scrobbler_active_users",
    "now_playing_active": "scrobbler_now_playing_active",
    "import_jobs": "scrobbler_import_jobs",
    "art_cache": "scrobbler_art_cache",
    "art_oldest_pending": "scrobbler_art_oldest_pending_seconds",
    "art_overrides": "scrobbler_art_overrides",
    "art_override_oldest_pending": "scrobbler_art_override_oldest_pending_seconds",
    "art_unresolved": "scrobbler_art_unresolved_albums",
}
