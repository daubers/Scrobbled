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
