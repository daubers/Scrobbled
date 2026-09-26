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
| `FEDERATION_SIGNATURE_SCHEMES` | Outgoing signing order; the next scheme is tried only on a 401. Default `draft-cavage,rfc9421` |
| `FEDERATION_BLOCKED_DOMAINS` | Comma-separated domains (subdomains included) refused at the inbox |
| `FEDERATION_DELIVERY_CONCURRENCY` | Parallel deliveries per worker (1-32, default 4) |
| `FEDERATION_INSECURE_TESTING` | **Tests only.** Allows `http://` and private addresses; refused unless the domain is under `.test` |

The UI container forwards these paths to the API when `API_INTERNAL_URL` is set (see `docker/ui-config.sh`):

- `/.well-known/webfinger`, `/.well-known/nodeinfo`, `/.well-known/host-meta`
- `/nodeinfo/`, `/users/`, `/inbox`, `/actor`

Every ActivityPub ID is built from `FEDERATION_BASE_URL`, never from the incoming request.

## Layout

| Module | Holds |
|---|---|
| `protocol/` | Pure protocol code: media types, vocabulary, WebFinger, NodeInfo, host-meta, and `signatures/` (draft-cavage and RFC 9421, one module each) |
| `net.py` | The SSRF-safe HTTP client, the only way out |
| `outbound.py` | Signed GETs (as the instance actor) and POSTs (as a user), with scheme fallback on 401 |
| `remote.py` | Remote actors: fetched, validated, cached; key id to actor |
| `inbox.py` | Intake (no network) and worker processing: signature check, identity checks, handlers by activity type |
| `followers.py` | Follower state: follows, undo, approve, remove, block, sharing off |
| `activities.py` | Activities we send (Accept, Reject, Block, Undo), stored and queued per inbox |
| `delivery.py` | The delivery worker task: leases, retries with backoff, gone inboxes, gauges |
| `models.py` | `federation_*` tables |
| `keys.py` | Key pairs, created once and encrypted at rest |
| `sharing.py` | Per-user settings, and who is sharing |
| `ids.py` | Our ActivityPub ids, all from `FEDERATION_BASE_URL` |
| `web.py` | The endpoints other servers call, including a paged outbox and individual posts |
| `api.py` and `schemas.py` | `/api/v1/federation/*` for the web UI |
| `openapi.py` | OpenAPI descriptions of `web.py`'s endpoints |
| `metrics.py` | Federation's own Prometheus metrics |
| `cli.py` | `flask federation ...` admin commands |
| `actors.py` | Builds a user's actor document body; shared by the live `GET /users/<u>` route and the now-playing `Update(Person)` push, so both render the same shape |
| `publishing/` | Posts: shared publish/delete machinery, and one module per kind (see below) |

## Publishing

`publishing/base.py` is the only place that knows how to actually post: build the
`Note`/`Create`, store it, address it from the user's current visibility, and fan it out
to accepted followers' inboxes. It's idempotent (one row per `(user, kind:key)`, via
`federation_posts`' unique constraint) and used for both publishing and deleting.

Each *kind* of post is its own small module that only decides **when** to post and what
the text says, then calls into `base.py`:

- `publishing/weekly.py`: once a week (`check_and_post`/`check_all`, the
  `federation.weekly` worker task), for the Monday–Sunday week whose posting gate has
  passed. `post_now()` instead posts the week still in progress immediately, ignoring the
  gate — used by `flask federation post-weekly --user <name> --now` and `preview()` (a
  read-only variant for the Sharing page's live preview, never stored).
- `publishing/milestones.py`: scrobble-count, artist-plays and top-10-entry thresholds,
  each posted once. A live scrobble queues a `FederationPendingCheck` row (collapsing
  repeats), drained by the `federation.milestones` worker task. `set_baseline()` marks
  every threshold already passed without posting, called when a user turns sharing on
  and after an import finishes, so old history never produces a burst of "milestone"
  posts.
- `publishing/now_playing.py`: not quite the same shape as the other two, since it
  covers two things at once - a throttled `Update(Person)` push for the profile field
  (`actors.py` owns the live version shown on a plain `GET`) and, opt-in, a post per
  track played 30+ seconds. Both are driven by one periodic scan
  (`federation.now_playing`, every 30 seconds) rather than an event receiver: the
  throttle windows (5 and 30 minutes) are far longer than the poll interval, and there's
  no history to react to retroactively the way milestones does.

Adding a fourth kind means adding another module here with the same shape as
`weekly.py`/`milestones.py` - decide when, build the text, call `publishing.publish()`
- not touching `base.py`.

## Worker tasks

`federation.inbox` (process received activities), `federation.deliver` (send queued
ones), `federation.inbox.maintenance` (re-queue stalled, prune after 30 days),
`federation.gauges` (queue and follower gauges), `federation.milestones` (drain pending
milestone checks), `federation.weekly` (check every sharing user for a due weekly
summary, every 15 minutes) and `federation.now_playing` (push profile updates and posts
for users with it on, every 30 seconds). All are registered only when federation is on.

## Interop

`scripts/interop/run.sh` runs Scrobbler against a real GoToSocial server on a private
`.test` network. `scripts/interop/gotosocial_interop.py` checks the whole follow life
cycle, then `run.sh` seeds a scrobble and a now-playing track, runs
`flask federation post-weekly --now` directly in the `api` container, and runs the
driver a second time (`--check-posts`) to confirm: the post reaches GoToSocial's home
timeline with the right content and `private` (followers-only) visibility and
disappears from it once deleted; and, with now playing turned on, GoToSocial's own
stored copy of the account (not a fresh `resolve()` fetch) picks up a "Now playing"
field from the periodic `Update(Person)` push. CI runs the whole thing on every push.
GoToSocial 0.22.1 ignores a Reject of an already-accepted follow (a TODO in its code,
fixed on its main branch), so that one check on its side is skipped until a release
includes the fix.

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
