# `scrobbler.federation`

ActivityPub support, kept apart from the rest of the app so it can be changed, replaced
or switched off without touching anything else. Design: `docs/design/activitypub.md`.

## Boundary rules (enforced by `tests/test_federation_boundary.py`)

1. **Nothing outside this package imports it**, except `create_app()` calling
   `federation.init_app(app)`. The core reports what happened through the domain events
   in `scrobbler.events`; this package subscribes to them.
2. **`protocol/` is pure**: no Flask, SQLAlchemy, Werkzeug or `scrobbler` imports.
   Signature schemes, vocabulary and document formats live there.
3. **The rest of this package uses only the core's public surface**:
   - `scrobbler.__version__`
   - `scrobbler.api.get_api` (to register federation's API blueprint and document its paths)
   - `scrobbler.events`
   - `scrobbler.extensions` (`db`)
   - `scrobbler.worker`
   - `scrobbler.services.*`
   - `scrobbler.api.decorators`
   - `scrobbler.schemas`

   So no core models and no core metrics; federation defines its own.
4. **Off means off.** With `FEDERATION_ENABLED` unset, `init_app` registers no routes,
   no event receivers and no worker tasks.

## Configuration

| Variable | Meaning |
|---|---|
| `FEDERATION_ENABLED` | `1` to turn federation on (default off) |
| `FEDERATION_DOMAIN` | Handle domain, normally the web UI's host: users are `@name@<domain>`. **Permanent once anyone follows.** |
| `FEDERATION_BASE_URL` | Public `https://` URL actors live under (`<base>/users/<name>`). Normally the web UI's origin, with the UI's nginx forwarding the ActivityPub paths to the API. |
| `FEDERATION_KEY_SECRET` | 32+ characters; encrypts actors' private keys at rest (HKDF → Fernet). **Back it up; never change it.** |

The UI container forwards these paths to the API when `API_INTERNAL_URL` is set (see `docker/ui-config.sh`):

- `/.well-known/webfinger`, `/.well-known/nodeinfo`, `/.well-known/host-meta`
- `/nodeinfo/`, `/users/`, `/inbox`, `/actor`

Every ActivityPub ID is built from `FEDERATION_BASE_URL`, never from the incoming request.

## Layout

| Module | Holds |
|---|---|
| `protocol/` | Pure protocol code: media types, vocabulary, WebFinger, NodeInfo, host-meta |
| `models.py` | `federation_settings` and `federation_keys` |
| `keys.py` | Key pairs, created once and encrypted at rest |
| `sharing.py` | Per-user settings, and who is sharing |
| `web.py` | The endpoints other servers call |
| `api.py` and `schemas.py` | `/api/v1/federation/*` for the web UI |
| `openapi.py` | OpenAPI descriptions of `web.py`'s endpoints |
| `metrics.py` | Federation's own Prometheus metrics |

## Migrations

Federation's tables (all prefixed `federation_`) live in their own Alembic branch,
labelled `federation`, so they can be changed or dropped without touching core
migrations:

```sh
uv run flask --app scrobbler db migrate --head federation@head -m "…"   # federation tables
uv run flask --app scrobbler db migrate --head core@head -m "…"         # everything else
uv run flask --app scrobbler db upgrade heads                           # apply both branches
```

When generating a federation migration, check it only touches `federation_*` tables.
Autogenerate compares all models, so core changes would otherwise leak into it.
