# Scrobbler

A self-hosted music scrobbling service. It speaks the **Last.fm (Audioscrobbler 2.0) API**, so players and scrobblers that let you set a custom Last.fm server can use it unchanged. It also has a web UI for your listening history.

```
players / scrobblers ──►  API  /2.0/      Last.fm-compatible
web UI (static files) ──►  API  /api/v1/   JSON API for the UI
                           API ──► PostgreSQL
Prometheus ◄── API :9100/metrics, postgres-exporter ──► Grafana
```

- **API** (`src/scrobbler/`): Flask under gunicorn. It is API-only and serves no pages.
- **Web UI** (`frontend/`): plain HTML, CSS and JavaScript with no build step. Host it anywhere static files can be served.
- **Docs**: OpenAPI at `/api/openapi.json`, Swagger UI at `/api/docs` and ReDoc at `/api/redoc`. A copy of the spec is committed at `docs/openapi.json`.
- **Worker**: `flask worker` runs background work, such as importing Last.fm history from export files or straight from Last.fm.
- **Monitoring**: Prometheus metrics, four provisioned Grafana dashboards, and alert rules.

## Run it

```sh
cp .env.example .env                      # then set SECRET_KEY and POSTGRES_PASSWORD
docker compose up -d                      # db, api (:5050), worker, ui (:8080)
docker compose --profile monitoring up -d # + Prometheus (:9090), Grafana (:3000)
```

Open http://localhost:8080, create an account, then go to **Apps** and create an app for each player.

To run the published images instead of building them locally:

```sh
SCROBBLER_IMAGE_TAG=main docker compose up -d --no-build
```

The images are `pkgs.daubney.dev/scrobbler/scrobbler-api` and `pkgs.daubney.dev/scrobbler/scrobbler-ui`.

### Configuration

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | api | local Postgres | SQLAlchemy URL (`postgresql+psycopg://…`) |
| `SECRET_KEY` | api | `dev-insecure-secret` | Flask secret |
| `CORS_ORIGINS` | api | `http://localhost:8080` | Comma-separated UI origins allowed to call `/api/v1` |
| `UI_BASE_URL` | api | `http://localhost:8080` | Where `/api/auth/` sends users to approve desktop sign-ins |
| `OPENAPI_DOCS_ENABLED` | api | `1` | Set to `0` to hide `/api/docs` and `/api/redoc` |
| `METRICS_PORT` | api | `9100` | Prometheus metrics port (keep it off the public network) |
| `WEB_CONCURRENCY` | api | `2` | gunicorn workers |
| `RUN_MIGRATIONS` | api image | `1` | Apply database migrations on start |
| `LASTFM_API_KEY` | api, worker | *(unset)* | Enables importing straight from Last.fm ([get a key](https://www.last.fm/api/account/create)) |
| `IMPORT_MAX_BYTES` | api | 200 MB | Largest export file accepted |
| `WORKER_METRICS_PORT` | worker | `9101` | The worker's Prometheus metrics port (`IMPORT_WORKER_METRICS_PORT` still works) |
| `API_BASE_URL` | ui image | `http://localhost:5050` | API location, written into `config.js` and the CSP |

In `docker compose` the host ports are set with `DB_PORT`, `API_PORT`, `UI_PORT`, `PROMETHEUS_PORT` and `GRAFANA_PORT`.

## Connecting a player

In the player's scrobbling settings, choose a custom or self-hosted Last.fm server and enter:

- **API URL**: `https://your-host/2.0/`
- **API key and shared secret**: from an app on the **Apps** page
- **Username and password**: your Scrobbler account

Two ways of signing in are supported:

- **Mobile**: `auth.getMobileSession` with a username and password, sent as a POST body over HTTPS.
- **Desktop**: `auth.getToken`, then the user approves at `/api/auth/` (which redirects to the web UI), then `auth.getSession`.

The legacy `authToken = md5(username + md5(password))` form is **not supported**, because checking it would mean storing unsalted password hashes. pylast's `password_hash` option uses that form. With pylast, use `SessionKeyGenerator` (the desktop flow) instead.

Supported methods:

- `auth.getMobileSession`, `auth.getToken`, `auth.getSession`
- `track.scrobble` (up to 50 per request), `track.updateNowPlaying`
- `user.getInfo`, `user.getRecentTracks`, `user.getTopArtists`, `user.getTopAlbums`, `user.getTopTracks`

`/api/docs` lists the parameters, rules and example responses for each method.

Scrobbles are ignored (not rejected) if the artist or track is empty, or if the timestamp is more than 14 days old or more than 5 minutes in the future. Sending the same scrobble again is accepted and stored once, so client retries are safe.

## Importing Last.fm history

Open **Import** in the web UI. You can import from:

- **An export file**:
  - CSV from [lastfm-to-csv](https://benjaminbenben.com/lastfm-to-csv/), with no header row and artist, album, track and date columns.
  - Any CSV or TSV with a header row naming artist, track and a date or Unix-time column. Comma, semicolon and tab delimiters all work.
  - A JSON export of `user.getRecentTracks` pages (such as lastfm.ghan.nl's), a single `recenttracks` response, or a flat list of tracks.
  - Files can be gzipped.
- **Last.fm directly**: enter a Last.fm username and the worker pages through that account's public history. This option only appears when the server has `LASTFM_API_KEY` set.

Imports run in the background in the `worker` service. The page shows progress, and an import can be cancelled.

Unlike live scrobbles, imports keep plays of any age. They still skip rows with no artist or track, and plays dated in the future. Plays already in your history are skipped, so importing again is safe.

## Develop

```sh
docker compose up -d db                      # Postgres with a scrobbler_test database
uv sync
uv run flask --app scrobbler db upgrade heads
uv run flask --app scrobbler run --port 5050 # API (port 5000 is taken by AirPlay on macOS)
python -m http.server 8080 -d frontend       # UI
uv run flask --app scrobbler worker          # background work (imports, …)
uv run pytest                                # needs TEST_DATABASE_URL (see .env.example)
uv run ruff check && uv run ruff format --check
```

- **Migrations** come in two branches, `core` and `federation`. After changing core models, run `uv run flask --app scrobbler db migrate --head core@head -m "…"`; federation tables use `--head federation@head` (see `src/scrobbler/federation/README.md`). Apply them with `db upgrade heads`. CI fails if the models and migrations disagree.
- **API docs**: after changing an endpoint, run `uv run python -m scrobbler.openapi.export docs/openapi.json`. It documents the API with every optional module (such as federation) switched on. A test fails if the committed spec is stale. Every route has to appear in the spec, and every UI route has to declare bearer auth.
- **Demo traffic**: `uv run python scripts/generate_traffic.py --minutes 5` sends a realistic mix of scrobbles and errors, so the dashboards have something to show.

The test suite includes an end-to-end run with [pylast](https://github.com/pylast/pylast) over HTTPS and a real two-worker gunicorn run, which checks that metrics aggregate across workers.

## Monitoring

The API serves Prometheus metrics on `METRICS_PORT`, summed across gunicorn workers. All series are prefixed `scrobbler_`:

- **HTTP**: request rate, errors and latency per endpoint
- **Last.fm**: calls, errors by Last.fm error code, and authentication failures by reason
- **Scrobbles**: accepted, duplicate and ignored (with the reason), batch size, and how late scrobbles arrive
- **Database**: query latency and connection-pool use
- **Users**: registered, active, and listening now (computed from the database and cached for 60s)

Labels never contain users, keys or track names.

With the `monitoring` profile, Grafana (admin password `GRAFANA_ADMIN_PASSWORD`) has a **Scrobbler** folder with four dashboards:

- API overview
- Last.fm API
- Usage
- Database

Alert rules live in `deploy/prometheus/alerts.yml`:

- API down
- import worker down, or imports left waiting
- 5xx ratio
- p95 latency
- authentication-failure spike
- ignored-scrobble ratio
- database pool saturated
- Postgres down

They're evaluated by Prometheus and appear in Grafana. Nothing routes them anywhere yet; that needs Alertmanager.

Tests check that every metric a dashboard or alert uses actually exists.

## CI/CD (Gitea Actions)

Everything runs from `.gitea/workflows/ci.yml`:

- **Every push and PR**:
  - lint
  - the migrations check
  - tests against Postgres
  - `promtool` checks of the Prometheus config and alert rules
- **Pushes to `main` and `v*` tags**, once every check has passed:
  - both images are pushed to the ProGet `scrobbler` Docker feed. `main` builds are tagged `main` and `sha-<commit>`; tags are tagged `<version>`, `latest` and `sha-<commit>`.
  - the OpenAPI spec, dashboards and Prometheus config are uploaded to the `scrobbler-assets` feed under `scrobbler/<version>/` or `scrobbler/main/`.
- **`v*` tags** also publish the wheel and sdist to the `scrobbler-python` PyPI feed, as the `scrobby` distribution. The import name is `scrobbler`. (`scrobbler` on public PyPI is an unrelated project.)
- **Every run** is posted to the `ScrobblingService` topic on notify.daubney.dev.

The workflow needs these repository secrets and variables:

- **Secrets**: `PKGS_USER`, `PKGS_PASSWORD`, `PKGS_API_KEY`, `NTFY_USER`, `NTFY_PASSWORD`
- **Variables**: `PKGS_HOST`, `PKGS_DOCKER_FEED`, `PKGS_PYPI_FEED`, `PKGS_ASSET_FEED`, `NTFY_URL`, `NTFY_TOPIC`

To release, tag `vX.Y.Z` on `main` and push the tag. The tag sets the package and image version.

## Licence

BSD 3-Clause; see [LICENSE](LICENSE). The fonts vendored in `frontend/fonts/` are under the SIL Open Font License 1.1.
