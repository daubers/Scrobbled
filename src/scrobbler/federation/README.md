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
| `FEDERATION_DOMAIN` | Handle domain: users are `@name@<domain>`. **Permanent once anyone follows.** |
| `FEDERATION_BASE_URL` | Public `https://` URL of the API, where actors live (`<base>/users/<name>`) |

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
