# Working on Scrobbler

Setting up a local development environment. For running a real instance, see the
[admin guide](admin-guide.md). For what the service does, see the
[user guide](user-guide.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python dependencies
- Docker, for Postgres (and optionally the full stack)

## Get running

```sh
docker compose up -d db                      # Postgres, with a scrobbler_test database too
uv sync
uv run flask --app scrobbler db upgrade heads
uv run flask --app scrobbler run --port 5050  # API (port 5000 is taken by AirPlay on macOS)
python -m http.server 8080 -d frontend        # UI
uv run flask --app scrobbler worker           # background work: imports, federation deliveries, …
```

`TEST_DATABASE_URL` (see `.env.example`) points the test suite at `scrobbler_test`.

```sh
uv run pytest
uv run ruff check && uv run ruff format --check
```

The test suite includes an end-to-end run with [pylast](https://github.com/pylast/pylast)
over HTTPS and a real two-worker gunicorn run, checking that metrics aggregate across
workers. `TestConfig` pins every setting a developer might have in their own `.env`
(`FEDERATION_ENABLED`, `LASTFM_API_KEY`, `UI_BASE_URL`, `CORS_ORIGINS`, …) so a local
`.env` used for manual testing can never change what the test suite exercises — if you
add a new setting that a test might reasonably assert an exact value for, pin it there
too rather than relying on its default.

## Migrations

Two Alembic branches, `core` and `federation`, so federation's tables can be changed or
dropped without touching core ones:

```sh
uv run flask --app scrobbler db migrate --head core@head -m "…"         # core models
uv run flask --app scrobbler db migrate --head federation@head -m "…"   # federation models
uv run flask --app scrobbler db upgrade heads                            # apply both
```

CI fails if the models and migrations disagree. See
`src/scrobbler/federation/README.md` for more on the federation branch specifically
(it also enforces that federation stays an optional, replaceable module — an
AST-based boundary test checks what it's allowed to import).

## API docs

After changing an endpoint:

```sh
uv run python -m scrobbler.openapi.export docs/openapi.json
```

It documents the API with every optional module (federation included) switched on. A
test fails if the committed spec is stale, if any route is missing from it, or if any UI
route is missing its bearer-auth declaration.

## Useful scripts

- `uv run python scripts/generate_traffic.py --minutes 5` — sends a realistic mix of
  scrobbles and errors, so the Grafana dashboards have something to show.
- `scripts/interop/run.sh` — runs Scrobbler against a real GoToSocial server on a private
  network and checks the whole federation follow/post/now-playing life cycle end to end.
  CI runs this on every push; run it locally the same way before changing anything in
  `scrobbler.federation`.

## Manual fediverse test

The interop test above proves the protocol works against one real server on a private
network. It can't prove WebFinger resolves from the public internet, or that a real
Mastodon instance renders things sensibly. Before merging federation changes that touch
anything user-visible, do a manual pass with a real account through a tunnel
(`cloudflared tunnel --url http://localhost:8080` needs no account for a quick HTTPS
URL) — see `docs/design/activitypub-phase4.md` for what was checked and two real bugs
that manual pass caught that no automated test had.

## CI/CD

Everything runs from `.gitea/workflows/ci.yml`:

- **Every push and PR**: lint, the migrations-vs-models check, tests against Postgres,
  and `promtool` checks of the Prometheus config and alert rules.
- **Pushes to `main` and tags**, once those all pass: images and assets publish (see
  [admin guide](admin-guide.md#releases)).
- **Every run** posts to the `ScrobblingService` topic on notify.daubney.dev.

Repository secrets: `PKGS_USER`, `PKGS_PASSWORD`, `PKGS_API_KEY`, `NTFY_USER`,
`NTFY_PASSWORD`. Variables: `PKGS_HOST`, `PKGS_DOCKER_FEED`, `PKGS_PYPI_FEED`,
`PKGS_ASSET_FEED`, `NTFY_URL`, `NTFY_TOPIC`.

## Conventions

- Keep the web UI (`frontend/`) entirely outside the API stack: Flask serves JSON only,
  never HTML.
- Every endpoint needs an OpenAPI entry and Prometheus metrics; every new dashboard
  metric needs a Grafana panel (or an existing one it plausibly extends).
- Commit at logical checkpoints through a piece of work, not only at the end.
- BSD-3-Clause licensed; avoid GPL/AGPL dependencies.
