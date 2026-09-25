# ActivityPub phase 0: groundwork

**Status: done** on `feature/activitypub`:

| Commit | Steps |
|---|---|
| `59ff1bd` | events |
| `098eab0` | worker |
| `c79c42e` | module and migrations |
| `eb5802f` | boundary tests |

This implements phase 0 of [activitypub.md](activitypub.md): the seams that federation will plug into. There's **no user-visible change**. All existing tests must keep passing, and the app behaves exactly as before with federation off, which is the default.

## 1. Domain events: `scrobbler/events.py`

Blinker signals in a `scrobbler` namespace. They're sent **after** the relevant commit, so receivers only ever see stored data.

| Signal | Sender | Keyword arguments | Sent from |
|---|---|---|---|
| `scrobbles_stored` | the `User` | `scrobbles: list[TrackInput]` (newly stored only), `source: "scrobble" \| "import"` | `services.scrobbles.submit_scrobbles`, and `services.imports.store_chunk` via the job loop |
| `now_playing_changed` | the `User` | `track: TrackInput \| None` (`None` means stopped or cleared) | `update_now_playing`, and clearing when the same track is scrobbled |
| `import_finished` | the `User` | `job: ImportJob` | `services.imports`, when a job completes, fails or is cancelled |

- Duplicates and ignored scrobbles aren't included, so listeners never see a play twice.
- A receiver that raises is logged and swallowed. A broken listener must never fail a scrobble.

## 2. Generic worker: `scrobbler/worker.py`

- **A task registry.**
  - `register_task(name, fn)` for work queues. `fn()` returns `True` if it did something, and the worker loops straight away while there's work.
  - `register_periodic(name, every, fn)` for scheduled jobs.
- **Imports move onto it.** `services.imports` registers:
  - `imports` (claim and process one job)
  - `imports.requeue_stalled` (every 60s, and once at start-up)
- **CLI.** A new `flask worker` command.
  - `flask imports worker` stays as an alias, so existing deployments keep working.
  - `docker-compose.yml` switches to `flask worker`.
- **Metric.** `scrobbler_worker_tasks_total{task,outcome}`, where outcome is `worked` or `error`, plus a panel on the Usage dashboard.
- **Isolation.** A task that raises is logged and counted, and the worker keeps going, so one module's bug can't stop another's work.

## 3. Empty federation module: `scrobbler/federation/`

- `__init__.py` holds `init_app(app)`, the only entry point, which `create_app()` calls. With `FEDERATION_ENABLED` off (the default) it returns without registering anything.
- `config.py` holds `FEDERATION_ENABLED`, `FEDERATION_DOMAIN` and `FEDERATION_BASE_URL`, and validates them. Enabling federation without a domain and an HTTPS base URL fails at start-up with a clear message.
- `protocol/__init__.py` states the purity rule; it's otherwise empty.
- `README.md` inside the package states the boundary rules and how to add migrations.

## 4. Alembic branches

- Label the existing core history `core` by adding `branch_labels = ("core",)` to the initial revision.
- Add a federation root revision:
  - revision ID `federation_0001`
  - `branch_labels = ("federation",)`
  - `depends_on` the core revision that creates `users`
  - no tables yet
- **Everything that runs `db upgrade` now upgrades `heads`**: the API entrypoint, CI, the test fixtures and the README.
- New core migrations use `flask db migrate --head core@head`; federation ones use `--head federation@head`.

## 5. Boundary tests: `tests/test_federation_boundary.py`

Static checks (using `ast`) over `src/scrobbler`:

1. Nothing outside `scrobbler.federation` imports it, except `scrobbler/__init__.py`.
2. `scrobbler.federation.protocol` imports nothing from `flask*`, `sqlalchemy`, `werkzeug` or `scrobbler`, apart from itself.
3. The rest of `scrobbler.federation` imports only these parts of the core:
   - `scrobbler.events`
   - `scrobbler.extensions`
   - `scrobbler.worker`
   - `scrobbler.services`
   - `scrobbler.api.decorators`
   - `scrobbler.schemas`

   So no core models, and no core metrics.

Runtime checks:

4. With federation off: no routes from federation blueprints, no signal receivers defined in `scrobbler.federation`, and no worker tasks named `federation.*`.
5. With federation on but misconfigured, `create_app` raises a clear error. Correctly configured, it starts.

## 6. Tests for the seams

- **Events**: a scrobble batch sends only the newly stored plays; now-playing sends on start and on clear; a finished import sends `import_finished`; a failing receiver doesn't break scrobbling.
- **Worker**: registered tasks run, the loop keeps running while there's work, periodic tasks keep to their interval, and a failing task is counted but doesn't stop the others.
- **Migrations**: `heads` upgrades both branches, `db check` passes, and the federation branch is independent (it can be downgraded on its own).

## 7. Docs and CI

- README: `flask worker`, `db upgrade heads`, and the two-branch migration workflow.
- CI: upgrade `heads` before `db check`.
- Commit after each numbered section once its tests pass.
