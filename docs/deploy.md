# Deploying Scrobbler

This covers running a real instance: the published images, configuration, putting it
behind TLS, monitoring, backups and releases. For running the code locally to work on
it, see [setup.md](setup.md). For what the service does once it's running, see
[usage.md](usage.md).

## Services

```
players / scrobblers ──►  API  /2.0/      Last.fm-compatible
web UI (static files) ──►  API  /api/v1/   JSON API for the UI
                           API ──► PostgreSQL
Prometheus ◄── API :9100/metrics, postgres-exporter ──► Grafana
```

- **`api`**: Flask under gunicorn. API-only; it serves no HTML pages.
- **`ui`**: nginx serving the static frontend (`frontend/`, no build step).
- **`worker`**: `flask worker` — history imports and background tasks for optional
  modules (federation's deliveries, scheduled posts, and so on).
- **`db`**: PostgreSQL.
- With the `monitoring` profile: `prometheus`, `grafana`, `postgres-exporter`.

## Quick start

```sh
cp .env.example .env   # then set SECRET_KEY and POSTGRES_PASSWORD
SCROBBLER_IMAGE_TAG=main docker compose up -d --no-build   # or drop --no-build to build locally
```

Open `http://localhost:8080`, create an account, then go to **Apps** and create an app
for each player. Add `--profile monitoring` for Prometheus and Grafana.

Published images: `pkgs.daubney.dev/scrobbler/scrobbler-api` and
`pkgs.daubney.dev/scrobbler/scrobbler-ui`, tagged `main` (latest push to `main`),
`sha-<commit>`, and `<version>`/`latest` for tagged releases.

## Configuration

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | api | local Postgres | SQLAlchemy URL (`postgresql+psycopg://…`) |
| `SECRET_KEY` | api | `dev-insecure-secret` | Flask secret — set a real one |
| `CORS_ORIGINS` | api | `http://localhost:8080` | Comma-separated origins allowed to call `/api/v1`: wherever browsers actually load the UI from |
| `UI_BASE_URL` | api | `http://localhost:8080` | Where `/api/auth/` sends users to approve desktop sign-ins, and (with federation on) the public identity users are found under |
| `OPENAPI_DOCS_ENABLED` | api | `1` | Set to `0` to hide `/api/docs` and `/api/redoc` |
| `METRICS_PORT` | api | `9100` | Prometheus metrics port — keep it off the public network |
| `WEB_CONCURRENCY` | api | `2` | gunicorn workers |
| `RUN_MIGRATIONS` | api image | `1` | Apply database migrations on start |
| `LASTFM_API_KEY` | api, worker | *(unset)* | Enables importing straight from Last.fm ([get a key](https://www.last.fm/api/account/create)) |
| `IMPORT_MAX_BYTES` | api | 200 MB | Largest export file accepted |
| `WORKER_METRICS_PORT` | worker | `9101` | The worker's Prometheus metrics port |
| `API_BASE_URL` | ui image | `http://localhost:5050` | Where the browser reaches the API — written into `config.js` and the CSP |

In `docker compose`, host ports are set with `DB_PORT`, `API_PORT`, `UI_PORT`,
`PROMETHEUS_PORT` and `GRAFANA_PORT`.

`CORS_ORIGINS` and `UI_BASE_URL` answer different questions and can differ: the first is
*which browser origins may call the JSON API*, the second is *where the public identity
and sign-in redirects point*. Don't assume setting one sets the other — `docker-compose.yml`
passes them through independently.

### Fediverse sharing (optional)

Off unless the server enables it, and then off for each user until they turn it on:

1. Set on the **api** and **worker**:
   - `FEDERATION_ENABLED=1`
   - `FEDERATION_DOMAIN`: the UI's host, e.g. `scrobble.example`
   - `FEDERATION_BASE_URL`: the UI's origin, e.g. `https://scrobble.example`
   - `FEDERATION_KEY_SECRET`: 32+ random characters
2. Give the **ui** container `API_INTERNAL_URL` (compose sets `http://api:8000`). It
   forwards these paths to the API: `/.well-known/webfinger`, `/.well-known/nodeinfo`,
   `/.well-known/host-meta`, `/nodeinfo/`, `/users/`, `/inbox` and `/actor`.
3. Serve the UI over **real HTTPS** on that domain — other fediverse servers only talk to
   HTTPS, and won't accept a self-signed certificate.

Handles and actor URLs **can't change once anyone follows a user**, so choose the domain
carefully before enabling this in production.

`FEDERATION_KEY_SECRET` encrypts every user's signing key. **Back it up, and never
change it**: losing it means users' keys can't be read, and followers would have to
follow again.

Optional: `FEDERATION_BLOCKED_DOMAINS` (comma-separated, subdomains included),
`FEDERATION_DELIVERY_CONCURRENCY` (default 4), `FEDERATION_SIGNATURE_SCHEMES` (default
`draft-cavage,rfc9421`). The `worker` service must be running — it delivers outgoing
activities and processes incoming ones.

## Putting it behind TLS

Scrobbler itself only speaks plain HTTP; something in front of it needs to terminate
TLS. Any reverse proxy works — nginx, Caddy, or a tunnel like Cloudflare Tunnel for
testing without a public IP (see `docs/design/activitypub-phase4.md` for how the
project's own manual federation test used one). A minimal Caddy example, proxying
everything to the `ui` container (which itself forwards the fediverse paths to `api`):

```
scrobble.example {
    reverse_proxy localhost:8080
}
```

Point `UI_BASE_URL`/`FEDERATION_BASE_URL`/`FEDERATION_DOMAIN` at that same public
`https://scrobble.example` origin. The API (`API_PORT`, default 5050) doesn't need its
own public hostname unless you want browsers to reach it directly instead of through the
UI's proxy — set `API_BASE_URL` accordingly either way, since the browser calls it
directly for `/api/v1`.

## Monitoring

The API serves Prometheus metrics on `METRICS_PORT`, summed across gunicorn workers. All
series are prefixed `scrobbler_`: HTTP request rate/errors/latency, Last.fm calls and
auth failures, scrobble outcomes, database query latency and pool use, and gauges for
registered/active/currently-listening users. Labels never contain usernames, API keys or
track names.

With `--profile monitoring`, Grafana (`GRAFANA_ADMIN_PASSWORD`) provisions a
**Scrobbler** folder with dashboards for API overview, Last.fm API, usage, database and
(with federation on) federation. Alert rules live in `deploy/prometheus/alerts.yml` —
evaluated by Prometheus and visible in Grafana, but nothing routes them anywhere without
Alertmanager.

## Backups

Back up, at minimum:

- The Postgres database (all scrobble history, accounts, federation state).
- `FEDERATION_KEY_SECRET`, if federation is on — it's the only thing that can decrypt
  stored signing keys, and it's never derivable from the database alone.

## Releases

Gitea Actions (`.gitea/workflows/ci.yml`) builds and publishes on every push to `main`
and on `v*` tags, once lint, tests and the Prometheus config checks all pass:

- Both images push to the ProGet `scrobbler` Docker feed.
- The OpenAPI spec, dashboards and Prometheus config upload to the `scrobbler-assets`
  feed.
- `v*` tags also publish the wheel/sdist to the `scrobbler-python` PyPI feed (as
  `scrobby`; `scrobbler` on public PyPI is an unrelated project).

To release: tag `vX.Y.Z` on `main` and push the tag. That tag sets both the package and
image version. See [setup.md](setup.md) for the secrets/variables the workflow needs and
how to run it locally before tagging.
