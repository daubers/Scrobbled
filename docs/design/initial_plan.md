# Plan: Last.fm-compatible scrobbling server (Flask API + separate static UI, PostgreSQL)

## Context
`ScrobblingService` is an empty uv project (Python 3.13, no dependencies, no code). The goal is a self-hosted scrobbling server that implements the **Last.fm / Audioscrobbler 2.0 API**, so existing players and scrobblers (Navidrome, Pano Scrobbler, foobar2000, pylast, etc.) can point at it by changing only the API root URL. The first version includes:
- users with API-key auth,
- now-playing,
- stats,
- a web UI.

**Architecture requirement:** Flask is an API layer only and serves no HTML. The UI is a separate, self-hosted static frontend (plain HTML/CSS/JS) that calls Flask over HTTP.

```
players/scrobblers ──► Flask  /2.0/     (Last.fm-compatible API)
static UI (frontend/) ─► Flask  /api/v1/  (JSON REST API for the UI)
                         Flask ──► PostgreSQL
```

## Stack & dependencies
- **Backend** (`pyproject.toml`, `uv add`):
  - Runtime: `flask`, `flask-sqlalchemy` (SQLAlchemy 2.0 style), `flask-migrate` (Alembic), `flask-cors`, `flask-smorest` (OpenAPI 3 + marshmallow schemas + docs UI), `prometheus-client`, `psycopg[binary]`, `python-dotenv`, `gunicorn`.
  - Dev: `pytest`, `pylast`, `openapi-spec-validator`.
- **Frontend** (`frontend/`): plain HTML, CSS and vanilla JS ES modules. There is no build step and no npm, so any static server (nginx, Caddy, `python -m http.server`) can host it. `frontend/config.js` holds `API_BASE_URL`.
- **`docker-compose.yml`** has three core services:
  - `db`: Postgres 16, creating the `scrobbler` and `scrobbler_test` databases.
  - `api`: gunicorn.
  - `ui`: nginx serving `frontend/` read-only.
- A `monitoring` compose profile adds three more:
  - `prometheus`
  - `grafana`
  - `postgres-exporter`
- **Config** comes from env vars: `DATABASE_URL`, `TEST_DATABASE_URL`, `SECRET_KEY`, `CORS_ORIGINS` (the UI origin), `UI_BASE_URL`. A `.env.example` documents them.

## Layout
```
src/scrobbler/
  __init__.py          # create_app(): extensions, CORS, blueprints, JSON error handlers
  config.py            # includes OPENAPI_* settings
  extensions.py        # db, migrate, smorest Api
  openapi/lastfm.py    # hand-written OpenAPI path + component definitions for /2.0/
  schemas/             # marshmallow schemas (request + response) for /api/v1
  metrics/
    __init__.py        # metric definitions (single place, all names prefixed scrobbler_)
    http.py            # before/after-request hooks: RED metrics per route
    db.py              # SQLAlchemy event hooks: query timing, pool gauges
    collectors.py      # cached DB-backed gauges (users, active users, now-playing)
    server.py          # separate metrics HTTP server (multiprocess-aware)
gunicorn.conf.py       # PROMETHEUS_MULTIPROC_DIR setup, child_exit mark_process_dead, start metrics server
deploy/
  nginx.conf
  prometheus/prometheus.yml, prometheus/alerts.yml
  grafana/provisioning/datasources/prometheus.yml
  grafana/provisioning/dashboards/dashboards.yml
  grafana/dashboards/{api-overview,lastfm-api,usage,database}.json
  models.py
  services/
    accounts.py        # register, password check, API apps, sessions, UI tokens
    scrobbles.py       # validate + store scrobbles, now-playing upsert
    stats.py           # top artists/albums/tracks, recent, counts by period
  lastfm/              # Last.fm blueprint at /2.0/
    __init__.py        # dispatcher: method=... -> handler registry
    signature.py       # api_sig verification
    responses.py       # dict -> XML (default) or JSON (format=json)
    errors.py          # LastFMError + codes
    methods/auth.py    # auth.getToken, auth.getSession, auth.getMobileSession
    methods/track.py   # track.scrobble, track.updateNowPlaying
    methods/user.py    # user.getInfo, getRecentTracks, getTopArtists/Albums/Tracks
  api/                 # JSON REST blueprint at /api/v1/ used by the UI
    auth.py            # register, login, logout, me
    apps.py            # API applications CRUD
    sessions.py        # list/revoke client sessions
    tokens.py          # approve desktop auth token
    stats.py           # now-playing, recent, top*, counts
    decorators.py      # @require_ui_token -> g.user
frontend/
  index.html           # dashboard
  login.html, register.html, history.html, apps.html, sessions.html, approve.html
  config.js            # API_BASE_URL
  js/api.js            # fetch wrapper: bearer token, JSON, error handling
  js/<page>.js         # one module per page
  css/style.css
migrations/  tests/  docker-compose.yml  .env.example  README.md
```

## Data model (`models.py`)
- **User**: id, username (unique, case-insensitive), email, password_hash (werkzeug `generate_password_hash`), created_at.
- **ApiApp**: api_key (32-char hex), shared_secret (32-char hex), name, owner user_id, created_at. These are what scrobbling clients use.
- **UiToken**: token_hash, user_id, created_at, last_used_at, expires_at (30 days). This is the bearer token for the web UI. Only the sha256 of the token is stored.
- **AuthToken**: token, api_app_id, user_id (null until the user approves it), created_at. Tokens expire after 60 minutes. Used for the Last.fm desktop auth flow.
- **Session**: session_key (32-char hex), user_id, api_app_id, created_at. Sessions don't expire but can be revoked.
- **Scrobble**: id, user_id, artist, track, album, album_artist, track_number, duration, mbid, played_at (timestamptz), submitted_at, api_app_id.
  - Index on `(user_id, played_at DESC)`.
  - Unique on `(user_id, played_at, artist, track)`; inserts use `ON CONFLICT DO NOTHING`.
- **NowPlaying**: user_id (PK), artist, track, album, album_artist, duration, started_at, expires_at (start + duration, or 10 min by default).

Stats group by `lower(artist)` etc. so capitalization differences don't split counts. Normalized artist/album tables can come later.

## Last.fm API (`/2.0/`)
- **Endpoint:** `GET|POST /2.0/`. Params come from the query string or a form body. The registry declares for each handler whether it needs a signature and whether it needs a session key.
- **Signature:**
  - Take every param except `format`, `callback` and `api_sig`, and sort them by name.
  - Concatenate each name followed by its value, append the shared secret, and take the md5 hash.
  - Compare the result to `api_sig` with `hmac.compare_digest`.
- **Responses:** XML `<lfm status="ok">` by default, JSON when `format=json`. Errors use the Last.fm shapes. Codes: 2, 3, 4, 6, 9, 10, 13, 14, 15, 29.
- **auth.getMobileSession:** signed; takes username and password, and returns `<session><name/><key/><subscriber>0</subscriber></session>`.
- **Desktop flow:** `auth.getToken`, then the user approves, then `auth.getSession`.
  - Clients send users to `<root>/api/auth/?api_key=…&token=…`. Flask answers that path with a **302 redirect to `UI_BASE_URL/approve.html?...`**. That keeps Flask HTML-free while existing clients still work.
  - The UI page calls `POST /api/v1/tokens/approve`.
- **track.updateNowPlaying:** signed, needs a session key. Upserts NowPlaying and echoes the corrected fields.
- **track.scrobble:** signed, needs a session key. Takes a batch of up to 50 tracks as `artist[i]`, `track[i]`, `timestamp[i]`, `album[i]`, `albumArtist[i]`, `duration[i]`, `trackNumber[i]`, `mbid[i]`.
  - Ignore codes: 1 for a missing artist, 2 for a missing track, 3 for a timestamp over 14 days old, 4 for a timestamp more than 5 minutes in the future.
  - Duplicates count as accepted, for idempotent retries.
  - The response carries accepted/ignored counts and one `<scrobble>` per track.
  - Scrobbling a track clears a matching now-playing entry.
- **user.getRecentTracks:** includes the now-playing track with `nowplaying="true"`. Also `user.getTopArtists`, `user.getTopAlbums`, `user.getTopTracks` and `user.getInfo`. They support `period` (overall/7day/1month/3month/6month/12month), `limit` and `page`, and take an api_key with no signature.

## UI REST API (`/api/v1/`, JSON only)
- **Auth:** `POST /auth/register`, `POST /auth/login` (returns `{token, user}`), `POST /auth/logout`, `GET /auth/me`.
  - Every other route uses `Authorization: Bearer <token>`. A bearer token rather than cookies avoids cross-origin cookie/CSRF complexity.
- **Apps:** `GET/POST /apps`, `DELETE /apps/<api_key>`. POST returns the key and secret.
- **Sessions:** `GET /sessions`, `DELETE /sessions/<id>`.
- **Tokens:** `GET /tokens/<token>` (app name, for the confirm screen), `POST /tokens/approve`.
- **Stats:**
  - `GET /me/now-playing`
  - `GET /me/recent?limit&page`
  - `GET /me/top/{artists|albums|tracks}?period&limit&page`
  - `GET /me/counts?bucket=day&period=`
  - `GET /me/summary` (total scrobbles, distinct artists)
- Errors use one JSON shape, `{"error": {"code": "...", "message": "..."}}`, with proper HTTP statuses.
- CORS allows only the `CORS_ORIGINS` origins, with the `Authorization` header allowed.
- The Last.fm `user.*` methods and these endpoints both call `services/stats.py`, so the logic isn't duplicated.

## OpenAPI document & docs
- **Library:** `flask-smorest` owns the spec (OpenAPI 3.1).
  - The `/api/v1` blueprints are `smorest.Blueprint`s. Every route uses `@blp.arguments(Schema)` / `@blp.response(code, Schema)` / `@blp.alt_response` for errors, with summaries and descriptions from docstrings. The spec is generated from the code, so it can't drift.
  - The bearer token is declared as an `http`/`bearer` security scheme. Routes that need it are marked, so Swagger UI's "Authorize" button works.
  - Tags: Auth, Apps, Sessions, Tokens, Stats, Last.fm.
- **Documenting `/2.0/`:** it is a single `method=` dispatcher, which OpenAPI can't express as one operation per method on the same path.
  - `openapi/lastfm.py` registers it with `api.spec.path()` as `GET /2.0/` and `POST /2.0/` operations. `method` is an enum parameter, and the common params are listed: `api_key`, `api_sig`, `sk`, `format`.
  - Each method's request and response is in the description plus `oneOf` response examples in both XML and JSON.
  - Each method's params also appear as a named schema component (e.g. `LastfmTrackScrobbleRequest`) so they're browsable.
  - The per-method details come from the same handler registry the dispatcher uses (each handler declares name, params, auth/signature needs and an example). The docs therefore list exactly the methods that exist.
- **Exposed endpoints (on the API server):**
  - `GET /api/openapi.json`: the spec.
  - `GET /api/docs`: Swagger UI, interactive with "try it out".
  - `GET /api/redoc`: ReDoc, for reading.
  - These are API-tooling pages generated by the library, not part of the app UI. The Swagger/ReDoc assets load from a CDN by default. The URLs can be pointed at self-hosted copies with `OPENAPI_SWAGGER_UI_URL` / `OPENAPI_REDOC_URL`, and `OPENAPI_DOCS_ENABLED` can turn the docs off in production.
- **CLI:** `flask openapi write docs/openapi.json` (built into flask-smorest) exports the spec, which is committed to the repo for clients and codegen.
- The README and the UI's "apps" page link to `/api/docs`.

## Metrics (Prometheus, `prometheus-client`)
- **Exposure:**
  - Metrics are served on a **separate port** (`METRICS_PORT`, default 9100) at `/metrics`, not on the public API port. Only Prometheus on the internal network reaches it, and it stays out of the OpenAPI spec.
  - Gunicorn runs with `PROMETHEUS_MULTIPROC_DIR`, so counters and histograms aggregate across workers. The Flask dev server uses the same code in single-process mode.
- **Label cardinality is kept bounded:**
  - `endpoint` is the Flask endpoint name (e.g. `api_v1.apps.list`), never the raw path. Unmatched requests get `unmatched`.
  - `lastfm_method` comes only from the registry; anything else gets `unknown`.
  - No user, api_key or track labels.

| Metric | Type | Labels | Source |
|---|---|---|---|
| `scrobbler_http_requests_total` | counter | endpoint, http_method, status | request hooks |
| `scrobbler_http_request_duration_seconds` | histogram | endpoint, http_method | request hooks |
| `scrobbler_http_requests_in_progress` | gauge | endpoint | request hooks |
| `scrobbler_lastfm_requests_total` | counter | lastfm_method, format, outcome (ok/error) | dispatcher |
| `scrobbler_lastfm_request_duration_seconds` | histogram | lastfm_method | dispatcher |
| `scrobbler_lastfm_errors_total` | counter | lastfm_method, code | LastFMError handler |
| `scrobbler_scrobbles_total` | counter | result (accepted/duplicate/ignored) | scrobble service |
| `scrobbler_scrobbles_ignored_total` | counter | reason (1–4) | scrobble service |
| `scrobbler_scrobble_batch_size` | histogram (1,2,5,10,25,50) | — | track.scrobble |
| `scrobbler_scrobble_lag_seconds` | histogram | — | submitted_at − played_at (spots offline-cache flushes) |
| `scrobbler_now_playing_updates_total` | counter | — | now-playing service |
| `scrobbler_auth_sessions_created_total` | counter | flow (mobile/desktop) | Last.fm auth |
| `scrobbler_auth_failures_total` | counter | reason (bad_password/bad_signature/bad_api_key/bad_session/token_expired/token_unauthorized) | Last.fm auth + sig check |
| `scrobbler_ui_logins_total` | counter | result (success/failure) | /api/v1/auth |
| `scrobbler_registrations_total` | counter | — | /api/v1/auth |
| `scrobbler_db_query_duration_seconds` | histogram | operation (select/insert/update/delete) | SQLAlchemy events |
| `scrobbler_db_pool_connections` | gauge | state (checked_out/idle/overflow) | pool events |
| `scrobbler_users` | gauge | — | cached collector (60s TTL) |
| `scrobbler_active_users` | gauge | window (1h/24h/7d) | cached collector |
| `scrobbler_now_playing_active` | gauge | — | cached collector |
| `scrobbler_build_info` | gauge (=1) | version, python_version | startup |

- The DB-backed gauges are computed by a custom collector at most once per 60s and cached, so frequent scrapes don't load Postgres. In multiprocess mode, only the metrics-server process registers the collector.
- `postgres-exporter` supplies database-side metrics: connections, transactions, cache hit ratio, DB size, locks and deadlocks.

## Grafana dashboards (provisioned from files, `deploy/grafana/`)
- A Prometheus datasource and a dashboard provider are provisioned. The dashboards go in a "Scrobbler" folder, with `$interval` and `$endpoint`/`$lastfm_method` template variables where they're useful.
- **API Overview (RED):**
  - request rate by endpoint,
  - error ratio (4xx and 5xx separately),
  - p50/p95/p99 latency by endpoint (`histogram_quantile`),
  - in-flight requests,
  - a "slowest endpoints" table (top-k p95),
  - scrape `up` status and build info.
- **Last.fm API:**
  - calls/sec by method,
  - error rate by Last.fm error code (stacked),
  - signature/auth failures by reason,
  - scrobbles accepted vs duplicate vs ignored,
  - ignore reasons,
  - batch size heatmap,
  - scrobble lag distribution,
  - now-playing update rate,
  - sessions created by flow.
- **Usage:**
  - scrobbles per minute and per day,
  - total and active users (1h/24h/7d),
  - listeners now playing,
  - registrations and logins (success vs failure).
- **Database:**
  - app-side query latency percentiles by operation,
  - pool usage vs size,
  - Postgres connections, TPS, cache hit ratio, DB size growth, locks and deadlocks (from postgres-exporter).
- **Alert rules** (`deploy/prometheus/alerts.yml`, shown in Grafana through the datasource):
  - `ScrobblerDown` (`up == 0` for 2m)
  - `HighErrorRate` (5xx ratio > 5% for 5m)
  - `HighLatency` (p95 > 1s for 10m)
  - `AuthFailureSpike` (auth failures > 10/s)
  - `ScrobbleIgnoreRateHigh` (ignored > 20% of submitted)
  - `DBPoolExhausted`
  - Alertmanager routing is out of scope. The rules are defined and visible, but no notifications go out.

## Source control, CI/CD, packages & notifications
### Git
- `git init` on branch `main`, with a `.gitignore` covering `.venv`, `.idea`, `.env`, `__pycache__`, `.pytest_cache` and `prometheus_multiproc/`.
- Remote `origin` = `ssh://git@git.daubney.dev:222/matt/Scrobbler.git`.
- Using the Gitea API with `GITEA_TOKEN` from the environment:
  - Check whether `matt/Scrobbler` exists with `GET /api/v1/repos/matt/Scrobbler`.
  - Create it with `POST /api/v1/user/repos` (private) only if it's missing.
  - Confirm that Actions are enabled on the repo.
- **Commits:** one commit per build-order step once that step's tests pass (see the Build order section), with descriptive messages.
- **Push:** push to `origin/main` once the CI step lands, so the workflows run. After that, push after each commit.

### Gitea Actions (`.gitea/workflows/`), runner label `ubuntu-latest`
- **`ci.yml`** runs on push and PR to any branch:
  - **`lint`:** `uv sync`, `ruff check`, `ruff format --check`. Adds `ruff` as a dev dependency.
  - **`test`:** a `postgres:16` service container. Runs `flask db upgrade`, then `pytest`, which includes the OpenAPI drift test, the metrics tests and the dashboard tests.
  - **`validate-monitoring`:** `promtool check config` and `promtool check rules` via the `prom/prometheus` image.
  - **`notify`:** `needs: [lint, test, validate-monitoring]`, `if: always()`. Posts the overall result.
- **`publish.yml`** runs on push to `main` and on `v*` tags, after the CI jobs pass (it repeats them as needed-jobs):
  - **`images`:** `docker login pkgs.daubney.dev` with `PKGS_USER`/`PKGS_PASSWORD`, then build and push two images to the ProGet **Docker feed**:
    - `pkgs.daubney.dev/<docker-feed>/scrobbler-api` (multi-stage uv build to a slim runtime running gunicorn),
    - `pkgs.daubney.dev/<docker-feed>/scrobbler-ui` (nginx + `frontend/`).
    - Both are tagged `sha-<short>` and `main`. On tags they're also tagged `<version>` and `latest`.
  - **`python-package`:** `uv build`, then `uv publish` to the ProGet **PyPI feed** (`https://pkgs.daubney.dev/pypi/<pypi-feed>/legacy`, auth `api` / `PKGS_API_KEY`). Only on `v*` tags, because PyPI versions are immutable. The project version comes from the tag.
  - **`assets`:** upload the monitoring bundle to a ProGet **asset feed** with `X-ApiKey: PKGS_API_KEY`. The bundle holds `docs/openapi.json`, `deploy/grafana/dashboards/*.json`, the Prometheus config and alerts, and `docker-compose.yml`. Destination: `https://pkgs.daubney.dev/endpoints/<asset-feed>/content/scrobbler/<version-or-sha>/`.
  - **`notify`:** `if: always()`. Posts the result, including the published image tags and version.
- **Dockerfiles:** `docker/api.Dockerfile` and `docker/ui.Dockerfile`, which `docker-compose.yml` also uses for local builds. Compose can take `SCROBBLER_IMAGE_TAG` to pull the published images instead of building them.

### Notifications (ntfy on notify.daubney.dev, topic `ScrobblingService`, every run)
- A reusable composite action `.gitea/actions/notify/action.yml` takes `status`, `title` and `message` inputs.
- It sends `curl -u "$NTFY_USER:$NTFY_PASSWORD" https://notify.daubney.dev/ScrobblingService` with these headers:
  - `Title`: e.g. "CI ✅ main @ abc1234" or "Publish ❌ v0.2.0".
  - `Priority`: `default` on success, `high` on failure.
  - `Tags`: `white_check_mark` / `x` / `package`.
  - `Click`: the Gitea run URL (`${{ gitea.server_url }}/${{ gitea.repository }}/actions/runs/${{ gitea.run_number }}`).
- The body carries the commit message, author, branch and job results.
- A failed notification never fails the workflow (`continue-on-error`).

### Secrets & variables (set via Gitea API with `GITEA_TOKEN`, never committed)
- Set with `PUT /api/v1/repos/matt/Scrobbler/actions/secrets/{name}` and `.../actions/variables/{name}`.
- **Secrets:**
  - `PKGS_USER`, `PKGS_PASSWORD`, `PKGS_API_KEY`: values read from the local environment (from `.zshrc`) at set time. Never echoed.
  - `NTFY_USER` = `matt` and `NTFY_PASSWORD`: the password you gave me. It goes only into the Gitea secret. It won't be in the repo, the plan file or memory.
- **Variables:** `PKGS_HOST=pkgs.daubney.dev`, `PKGS_DOCKER_FEED`, `PKGS_PYPI_FEED`, `PKGS_ASSET_FEED`, `NTFY_URL=https://notify.daubney.dev`, `NTFY_TOPIC=ScrobblingService`.

### ProGet feeds
- During implementation, list the existing feeds with the ProGet API (`X-ApiKey: $PKGS_API_KEY`) and pick the existing Docker, PyPI and asset feeds that fit.
- If a suitable feed doesn't exist, **I'll ask you before creating one**, because that changes your shared package server.

## Frontend (`frontend/`)
- `js/api.js`:
  - Reads `API_BASE_URL` from `config.js`.
  - Stores the UI token in `localStorage`.
  - Adds the bearer header to requests.
  - Redirects to `login.html` on a 401.
- Pages:
  - **login / register**
  - **index (dashboard):** now playing (polled every 30s), totals, recent 10, top artists/tracks with a period switcher, and a simple daily listens bar chart drawn with inline SVG, no chart library.
  - **history:** paginated.
  - **apps:** create/delete, show key and secret, and setup instructions showing the API root `<api>/2.0/`.
  - **sessions:** revoke.
  - **approve:** the desktop-flow token confirmation. Sends the user to login first if needed, then back.
- Text from the server is always inserted with `textContent` or escaping, never raw `innerHTML`, so track names can't inject HTML.

## Tests (`tests/`, pytest against `TEST_DATABASE_URL`)
- `conftest.py`: an app fixture (tables created once, truncated per test), a Flask test client, and user/app/session/UI-token fixtures plus a `signed(params, secret)` helper.
- `test_signature.py`: known-vector check; bad or missing signature returns error 13.
- `test_lastfm_auth.py`: mobile session success and bad password (4), token → approve (via `/api/v1`) → getSession, expired and unapproved tokens (15 and 14), and the `/api/auth/` redirect to the UI.
- `test_scrobble.py`: single scrobble, batch of 50, ignore rules, duplicate idempotency, and now-playing set, expiry and clearing.
- `test_lastfm_user.py`: top and recent over periods, XML vs JSON shapes.
- `test_ui_api.py`:
  - register/login/logout,
  - 401 without a token,
  - app CRUD,
  - session revoke (the revoked key then gets error 9 on `/2.0/`),
  - stats endpoints,
  - CORS headers.
- `test_openapi.py`:
  - `/api/openapi.json` passes `openapi-spec-validator`.
  - Every registered Flask rule under `/api/v1` and `/2.0/` has a matching path in the spec.
  - Every Last.fm handler in the registry appears in the `method` enum.
  - `/api/docs` and `/api/redoc` return 200.
  - The committed `docs/openapi.json` matches the generated spec, so a stale export fails the tests.
- `test_metrics.py`: uses a fresh `CollectorRegistry` per test.
  - Requests increment the HTTP metrics with the right `endpoint`/`status` labels.
  - A scrobble batch with mixed valid and invalid tracks increments accepted, ignored and reason counts correctly.
  - A bad signature increments `auth_failures_total{reason="bad_signature"}` and `lastfm_errors_total{code="13"}`.
  - An unknown method or path maps to `unknown`/`unmatched`, which checks that cardinality stays bounded.
  - The metrics server output parses.
- `test_dashboards.py`:
  - Every dashboard JSON loads and has a unique uid.
  - Every `scrobbler_*` metric referenced in panel queries or alert rules is actually defined in `metrics/__init__.py` (histograms may use their `_bucket`/`_sum`/`_count` names). This stops dashboards drifting from the code.
- `test_pylast_compat.py`: runs the app in a threaded werkzeug server and points `pylast` at it. It gets a session, calls now-playing and scrobble, then reads recent tracks. Exactly which password param pylast sends for getMobileSession will be checked here, and both forms will be accepted if needed.
- Frontend: no JS test framework in v1. It is verified manually in the browser (below).

## Build order (each step ends with passing tests + a commit)
0. `git init`, `.gitignore`, remote and repo setup. Then `ci.yml` plus the notify action and repo secrets/variables. Push, and confirm the first CI run goes green and the ntfy message arrives, so every later commit is checked by CI.
1. Dependencies, docker-compose, config, app factory, models, first migration.
2. Last.fm core: signature, responses, errors, dispatcher.
3. flask-smorest `Api` setup plus `/api/openapi.json`, `/api/docs` and `/api/redoc`. Then `services/accounts.py` and the UI auth API (`/api/v1/auth`, bearer decorator + security scheme, CORS), with schemas written alongside each route from here on.
4. Last.fm auth methods, the `/api/auth/` redirect, and the token approval API.
5. Scrobble and now-playing, with the service layer.
6. Stats service, then Last.fm `user.*` methods and the `/api/v1/me/*` endpoints.
7. Frontend pages.
8. Last.fm OpenAPI definitions (`openapi/lastfm.py`) from the handler registry. Export `docs/openapi.json` and add `test_openapi.py`.
9. Metrics:
   - Add the HTTP hooks and metrics server early, in step 1, so every later step can add its domain counters as it goes.
   - Finish with the DB hooks, cached collectors, gunicorn multiprocess config and `test_metrics.py`.
10. Monitoring stack: Prometheus config and alerts, postgres-exporter, Grafana provisioning, the 4 dashboards, `test_dashboards.py`.
11. pylast compatibility test, nginx config, README (running the API and UI, configuring clients, API docs link, monitoring).
12. Dockerfiles and `publish.yml`: confirm the ProGet feeds, push to main and check the images and assets land. Then tag `v0.1.0` and check the wheel appears in the PyPI feed.

## Verification
1. `docker compose up -d db`, then `uv sync`, then `uv run flask --app scrobbler db upgrade`.
2. `uv run pytest`: all tests pass, including the pylast end-to-end test.
3. Run `uv run flask --app scrobbler run` (port 5000) and `python -m http.server 8080 -d frontend`, with `CORS_ORIGINS=http://localhost:8080`. Then in the browser:
   - register and create an API app,
   - scrobble a few tracks with a small pylast script,
   - confirm the dashboard shows now-playing, recent tracks, top lists and the chart.
4. Test the desktop auth flow: call `auth.getToken` via curl, open `http://localhost:5000/api/auth/?api_key=…&token=…`, confirm it redirects to the UI approve page and approval works, then call `auth.getSession` via curl.
5. `curl 'http://localhost:5000/2.0/?method=user.getRecentTracks&user=<name>&api_key=<key>&format=json'` returns Last.fm-shaped JSON.
6. Open `http://localhost:5000/api/docs`:
   - Every tag and path appears.
   - "Authorize" with a token from `/auth/login`, then run `GET /api/v1/me/recent` from Swagger UI.
   - Run `GET /2.0/?method=user.getTopArtists` from the Last.fm section.
   - `/api/redoc` renders.
7. `docker compose --profile monitoring up` brings up db, api, ui, prometheus, grafana and postgres-exporter. Then:
   - `curl localhost:9100/metrics` shows the `scrobbler_*` series.
   - Prometheus targets page shows api and postgres-exporter as UP.
   - `promtool check config` and `promtool check rules` pass (run in the prometheus container).
8. Run a small load script (pylast scrobbling a few hundred tracks, some deliberately invalid or badly signed, plus UI API calls). Then open Grafana at `localhost:3000`:
   - All 4 dashboards in the "Scrobbler" folder show data.
   - Latency and error panels move.
   - Ignore reasons and auth failures reflect the bad requests.
   - The alert rules are listed.
9. CI/CD:
   - Each push shows green `ci.yml` runs in Gitea Actions, and each run posts to the `ScrobblingService` ntfy topic, including a deliberately failing test on a throwaway branch (which should arrive with high priority, then be deleted).
   - A push to main publishes both images to the ProGet Docker feed (`docker pull pkgs.daubney.dev/<feed>/scrobbler-api:main` works) and uploads the asset bundle.
   - Tag `v0.1.0` publishes the wheel to the PyPI feed (`uv pip install --index-url …` works) and version-tagged images.

## Out of scope for v1
- Legacy Audioscrobbler 1.2 handshake.
- ListenBrainz API.
- Importing history from Last.fm.
- Normalized artist/album tables and MusicBrainz lookups.
- Rate limiting (code 29 is reserved).
- A frontend build toolchain.
