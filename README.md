# Scrobbler

A self-hosted music scrobbling service. It speaks the **Last.fm (Audioscrobbler 2.0) API**,
so players and scrobblers that let you set a custom Last.fm server can use it unchanged.
It also has a web UI for your listening history, and optional sharing on the fediverse
(ActivityPub) — followable from Mastodon, with weekly summaries, milestones and now
playing.

```
players / scrobblers ──►  API  /2.0/      Last.fm-compatible
web UI (static files) ──►  API  /api/v1/   JSON API for the UI
                           API ──► PostgreSQL
Prometheus ◄── API :9100/metrics, postgres-exporter ──► Grafana
```

## Docs

- **[Deploy](docs/deploy.md)** — running a real instance: images, configuration, TLS,
  monitoring, backups, releases.
- **[Set up](docs/setup.md)** — working on the code: local dev environment, migrations,
  tests, CI/CD.
- **[Use](docs/usage.md)** — accounts and apps, connecting a player, importing history,
  sharing on the fediverse.
- **API reference**: `/api/openapi.json`, Swagger UI at `/api/docs`, ReDoc at
  `/api/redoc` (a copy is committed at `docs/openapi.json`).
- **Federation internals**: `src/scrobbler/federation/README.md`. Design docs for how it
  was built: `docs/design/`.

## Try it

```sh
cp .env.example .env                      # then set SECRET_KEY and POSTGRES_PASSWORD
docker compose up -d                      # db, api (:5050), worker, ui (:8080)
```

Open http://localhost:8080, create an account, then go to **Apps** and create an app for
a player. See [docs/deploy.md](docs/deploy.md) for the published images, full
configuration reference and production notes.

## Licence

BSD 3-Clause; see [LICENSE](LICENSE). The fonts vendored in `frontend/fonts/` are under
the SIL Open Font License 1.1.
