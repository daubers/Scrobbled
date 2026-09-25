# ActivityPub federation: design proposal

Status: **proposal, for review.** Nothing here is built yet. The decisions still open are listed at the end.

## Summary

Let a user turn their Scrobbler account into a fediverse account, for example `@alice@scrobble.example`, that people on Mastodon, GoToSocial, Misskey and similar servers can follow. Followers see three kinds of post:

- a **weekly listening summary**
- **milestones** (for example "10,000th scrobble")
- **what the user is playing now**

The first version is **outbound only**: Scrobbler publishes, and it accepts and manages followers. Following other people and showing their listening in the UI is a possible later phase (see [Later](#later-following-and-listen-activities)). The data model is designed so it can be added without rework.

Federation is **off for everyone until they turn it on**. Until then a user can't be found by WebFinger and nothing about them is published.

**Decided:** we write the protocol layer ourselves, on `cryptography` plus the Flask, SQLAlchemy and worker setup we already have (about 1,500 lines, in the four phases below). Scrobbler is BSD-licensed, and the ready-made Python library is AGPL (see [Build or buy](#build-or-buy)).

All of it lives in **one self-contained package, `scrobbler.federation`**, behind a narrow interface. It can then be changed, replaced or switched off without touching the rest of the app, and changes to the specs (signature schemes, vocabulary, FEPs) stay inside it. See [Module structure](#module-structure).

## What users get

- **Settings → Sharing**, a new UI page:
  - A **Share my listening on the fediverse** switch. It shows the handle (`@alice@scrobble.example`) and warns that the handle can't change once people follow it.
  - Choose what to post:
    - **Weekly summary**: on by default once sharing is on.
    - **Milestones**: on by default.
    - **Now playing**: off by default, with two ways to show it. See [Now playing](#now-playing).
  - **Who can see posts**: *Followers only* (**the default**), *Unlisted* (anyone who visits the profile, but kept out of public timelines), or *Public*. The help text explains that followers-only is only as private as who can follow: with automatic approval anyone can follow and then see posts, so for real privacy it suggests turning on **Approve followers manually**.
  - **Approve followers manually** (off by default).
  - Profile fields: display name, bio, avatar. The avatar defaults to a generated cover-art-style tile.
- **Followers**: a list of remote followers, with *Remove*. There are *Approve* and *Decline* buttons when manual approval is on.
- **Turning sharing off** deletes the published posts from followers' servers (`Delete` activities) and removes the followers. The account goes back to being unreachable. Keys are kept, so turning it on again keeps the same identity. Remote servers may still cache the old profile.

## How it fits the current system

```
Mastodon, GoToSocial, …                        Scrobbler
────────────────────────────────────────────────────────────────────────────
GET /.well-known/webfinger?resource=acct:…  ─►  API  (only for opted-in users)
GET /users/alice  (Accept: activity+json)   ─►  API  actor document
POST /users/alice/inbox  or  /inbox          ─►  API  verify signature, record, 202
                                                 │   Follow → follower (+ Accept queued)
                                                 ▼
                                             Postgres ◄── worker (`flask worker`)
                                                 │         • delivers queued activities (signed POSTs)
◄──────────── POST {follower shared inbox} ──────┘         • weekly summaries, milestones, now playing
```

- The **API** serves everything a remote server fetches: WebFinger, NodeInfo, actors, outboxes and inboxes. The inbox only validates and records; it never makes outbound calls itself.
- The **worker** (today's import worker, generalised to `flask worker`) does all outbound work: `Accept`s, post deliveries, retries, and the scheduled weekly and milestone posts. It uses the same claim-with-`SKIP LOCKED` pattern as imports.
- The **UI** gains the Sharing and Followers pages, which call new `/api/v1/federation/*` endpoints.

## Module structure

Everything to do with ActivityPub lives in `src/scrobbler/federation/`. The rest of Scrobbler knows only two things about it: how to switch it on, and a handful of domain events. Replacing the implementation (with another library, or a rewrite when the specs move on) means replacing this package; nothing else changes.

```
src/scrobbler/
  events.py                 # core → anyone: blinker signals (scrobbles_stored, now_playing_changed, …)
  worker.py                 # generic worker: runs tasks registered by any module
  federation/
    __init__.py             # init_app(app): the ONLY entry point. No-op unless FEDERATION_ENABLED
    config.py               # FEDERATION_* settings and their validation
    protocol/               # pure ActivityPub: no Flask, no SQLAlchemy, no scrobbler imports
      vocab.py              #   JSON-LD builders: Person, Note, Create, Accept, Update, Delete, collections
      addressing.py         #   visibility → to/cc
      webfinger.py          #   JRD documents, acct: parsing
      nodeinfo.py
      signatures/
        base.py             #   Signer / Verifier interfaces, SignedRequest value object
        draft_cavage.py     #   one module per scheme; add/remove schemes here
        rfc9421.py
    net.py                  # SSRF-safe HTTP client; signed GET/POST using protocol.signatures
    models.py               # federation_* tables (own migrations, see below)
    keys.py                 # key generation, encryption at rest, rotation
    inbox.py                # activity-type → handler registry (like the Last.fm method registry)
    delivery.py             # queue, retries, shared-inbox fan-out (a worker task)
    publishing/             # what gets posted; each kind is a plug-in
      summary.py            #   weekly summary
      milestones.py
      now_playing.py
    subscribers.py          # connects core events to publishing
    web.py                  # Flask blueprint: webfinger, nodeinfo, actor, inbox, outbox, collections
    api.py                  # /api/v1/federation/* (settings, followers) for the UI
    metrics.py              # federation's own Prometheus metrics
```

**Rules that keep the boundary clean:**

1. **The core never imports `scrobbler.federation`.** The only exception is `create_app()`, which calls `federation.init_app(app)`.
   - Core code tells the outside world what happened by sending **domain events** from `scrobbler/events.py`:
     - `scrobbles_stored(user, scrobbles, source)`
     - `now_playing_changed(user, track or None)`
     - `import_finished(user, job)`
     - `user_deleted(user)`
   - These are blinker signals; blinker is already a Flask dependency. Federation subscribes to them; with federation off, nothing listens.
   - Imports can pass `source="import"`, which is how milestones know to ignore imported history.
2. **Federation reads core data only through core services** (`services.stats`, `services.accounts`), never by querying core tables directly. So core schema changes don't break it.
3. **`federation.protocol` is pure.** It has no Flask, SQLAlchemy or `scrobbler` imports: just dicts, dataclasses and `cryptography`.
   - This is where spec changes land. A new signature scheme is a new module under `signatures/`. A vocabulary change is a builder in `vocab.py`.
   - It's tested with spec test vectors and recorded requests alone.
4. **Pluggable pieces go through registries**, not `if` chains:
   - **Signature schemes.** `FEDERATION_SIGNATURE_SCHEMES=draft-cavage,rfc9421` sets which schemes are accepted and the order they're tried when sending. Dropping draft-cavage one day would be a config change followed by deleting a module.
   - **Inbox activity handlers**: activity type → handler, the same pattern as the Last.fm method registry.
   - **Post kinds**: summary, milestones and now playing each register as a publisher.
5. **Federation owns its data.** Its tables are all prefixed `federation_` and have foreign keys to `users` only.
   - Its migrations live in their own Alembic branch (`migrations/versions/federation_*`, `branch_labels=("federation",)`), so they can be dropped or rewritten without touching the core migration history.
   - Removing the module means dropping its branch, which drops its tables.
6. **Work in the background goes through a generic task runner.** Today's import worker becomes `scrobbler/worker.py`, which runs tasks registered by modules: imports register theirs, and federation registers delivery and scheduled posting. The worker doesn't know what ActivityPub is.
7. **Enforced by tests.** A boundary test walks every module's imports and fails if:
   - anything outside `scrobbler.federation` imports it (apart from `create_app`),
   - `federation.protocol` imports Flask, SQLAlchemy or `scrobbler`, or
   - federation code imports core models directly.

   There's also a test that the whole app works with `FEDERATION_ENABLED=0`: no routes registered, no signal receivers, no tables touched.

**Switching it off:** with `FEDERATION_ENABLED=0` (the default until an operator sets up a domain), `init_app` registers nothing. There are no routes, no subscribers and no worker tasks, and the app behaves exactly as it does today.

## Protocol pieces

### Identity and domains

**Decided: the UI's domain**, which is the name users already know. Two settings configure it:

- `FEDERATION_DOMAIN`: the handle's domain, as in `@alice@<FEDERATION_DOMAIN>`. This is the UI's host.
- `FEDERATION_BASE_URL`: where the actor documents live, as in `https://<host>/users/alice`. It's also the UI's origin, so actor links, profile links and the handle all show one domain, and the API hostname stays an internal detail.

The UI's nginx **forwards the ActivityPub paths to the API**:

- `/.well-known/webfinger`, `/.well-known/nodeinfo`, `/.well-known/host-meta`
- `/nodeinfo/`, `/users/`, `/inbox`, `/actor`

All other paths are the static UI, as now. None of these paths collide with the UI's pages. The proxy passes `Host` and `X-Forwarded-*` through, and the API must trust them (`FORWARDED_ALLOW_IPS`), so that signature checks, which cover the request target and host, see the URL the remote server actually signed.

A browser opening an actor URL (`/users/alice` without an ActivityPub `Accept` header) gets a redirect from the API to the UI's profile page.

The two settings are kept separate even though they're usually the same host: the design still allows handles on one domain and actors on another, should that ever be needed. Mastodon supports that split through WebFinger's `self` link.

**Both values become permanent once anyone follows**: actor IDs are how other servers identify the account. Renaming a user or moving domains later needs the `Move` flow and `alsoKnownAs` (see [Later](#later-following-and-listen-activities)). So we should:

- stop users renaming themselves once they've enabled sharing, and
- refuse to start if the domain settings change while followers exist, unless an explicit migration flag is set.

### WebFinger (RFC 7033)

- `GET /.well-known/webfinger?resource=acct:alice@domain` returns `subject` plus two links:
  - a `self` link of type `application/activity+json` pointing to the actor
  - a `profile-page` link to the UI
- It returns **404** for unknown users and for users who haven't opted in. The response is identical in both cases, so the endpoint can't be used to list accounts.
- It's required: Mastodon resolves `@user@domain` through it.

### Actor (`Person`)

`GET /users/{username}` with `Accept: application/activity+json` or `application/ld+json; profile="https://www.w3.org/ns/activitystreams"` returns the actor document. Its fields:

- `id`, `type: Person`, `preferredUsername`, `name`, `summary`, `url` (the UI profile), `icon`
- `inbox`, `outbox`, `followers`, `following`, and `endpoints.sharedInbox`
- `publicKey`: `{id: …#main-key, owner, publicKeyPem}`
- `manuallyApprovesFollowers`, and `discoverable` and `indexable`. The last two are **false** by default and are settings the user can change.
- `attachment`: `PropertyValue` profile fields, for example "Scrobbles: 12,345". This is also where [now playing](#now-playing) can live.

A browser (a non-ActivityPub `Accept` header) is redirected to the UI profile page.

### Keys and signatures

- **One RSA-2048 key pair per federated user.** Mastodon's draft-signature path requires RSA.
  - Private keys are **encrypted at rest** with a Fernet key derived from `FEDERATION_KEY_SECRET` (Mastodon 4.7 does the same).
  - Ed25519 keys ([FEP-521a](https://codeberg.org/fediverse/fep/src/branch/main/fep/521a/fep-521a.md)) can be added later without breaking anything.
- **Outgoing**: sign with the draft-cavage HTTP Signatures scheme, still the most widely accepted.
  - Signed headers: `(request-target) host date digest`, using `rsa-sha256`.
  - The body digest goes in `Digest: SHA-256=…`.
- **Incoming**: verify both **draft-cavage** and **RFC 9421** (`Signature-Input`/`Signature`, which covers `@method`, `@target-uri` and `Content-Digest`).
  - Mastodon 4.5+ accepts RFC 9421.
  - Mastodon 4.7 *sends* RFC 9421 when a draft-signed request fails ("double-knocking"). Accepting both avoids needless failures.
- **Verification rules**:
  - `Date` within ±12 hours.
  - The digest must match the body.
  - Fetch the `keyId` document and confirm its `owner` is the activity's `actor`.
  - Cache remote keys, and re-fetch once on failure in case the key was rotated.
  - Ignore Linked Data Signatures; Mastodon itself advises against implementing them.
- **Signed GETs**: some servers use "authorized fetch" (secure mode), which requires signed GETs. We always sign our fetches of remote actors, using an instance actor (`/actor`, type `Application`), so fetching works everywhere.

The signing code is small and well specified. Test vectors come from the specs and from recorded Mastodon and GoToSocial requests.

### Inbox

- `POST /users/{u}/inbox` and a shared `POST /inbox`. Limits: 256 KB body, and JSON-LD content types only.
- Once the signature is verified, an activity is **recorded and answered with 202**. Processing is quick and does no network I/O. Activity IDs are deduplicated.

| Activity | Handling |
|---|---|
| `Follow` (object = our actor) | Store the follower: `accepted`, or `pending` if manual approval is on. Queue an `Accept` (or wait for the user to decide). |
| `Undo` → `Follow` | Remove the follower. |
| `Delete` (actor) | Remove the follower and drop the cached actor. |
| `Update` (Person) | Refresh the cached actor, which may have a new shared inbox or key. |
| `Like`, `Announce`, `Create` (replies) | Recorded for metrics only; not shown in v1. |
| Anything else | Accepted and ignored. |

- **Blocks**:
  - A user can block a remote account, which also removes it as a follower.
  - An admin can block a whole domain with `FEDERATION_BLOCKED_DOMAINS`. Blocked senders get 403, and their `Follow`s are rejected.

### Collections

- `outbox`: an `OrderedCollection`, paged, holding the user's public and unlisted `Create` activities, newest first.
- `followers`: shows `totalItems` only. Items are hidden, as Mastodon does by default.
- `following`: an empty collection in v1.

### NodeInfo 2.1

Serve `/.well-known/nodeinfo` and `/nodeinfo/2.1`, giving the software name `scrobbler`, the version, protocols `["activitypub"]`, and user counts (opted-in users only). Fediverse tooling and statistics sites read it, and it's cheap.

### Delivery

- Every outgoing activity is saved, then turned into **delivery rows**: one per distinct inbox, using each follower's shared inbox where it has one.
- The worker delivers them with signed POSTs. Timeouts are 10s to connect and 20s to read.
- **Retries**: failures are retried with exponential backoff (1m, 5m, 30m, 2h, 6h, then every 12h) for up to **2 days**. After that the delivery is dropped and the peer marked unhealthy.
  - 410 Gone and permanent 4xx responses aren't retried.
  - 410 Gone for an actor removes it as a follower.
- **Outbound fetches are protected against SSRF**: HTTPS only, no private or loopback addresses (checked after DNS resolution), a 1 MB response limit, and at most 3 redirects.

## What gets posted

Posts are `Note` objects inside `Create` activities: Mastodon shows `Note` as a normal status, while other types are squashed into a title and link. The HTML uses only tags Mastodon keeps (`p`, `a`, `br`, `strong`, `em`, `ul`, `ol`, `li`), and all track and artist text is escaped. Each post also gets hashtags (`#NowPlaying`, `#Scrobbler`) as `Hashtag` tags. Visibility follows the user's setting, which defaults to **followers only**:

| Setting | `to` | `cc` |
|---|---|---|
| Followers only (default) | followers | *(none)* |
| Unlisted | followers | `as:Public` |
| Public | `as:Public` | followers |

What followers-only means in practice:

- Posts go only to followers' servers, which show them only to followers.
- They don't appear in the public outbox or on the profile for anyone else.
- They can't be boosted.
- Changing the setting affects new posts only: already-published posts keep the visibility they were sent with.

### Weekly summary

- The worker posts one per opted-in user, on Monday at 09:00 in the user's time zone. Users don't have a time zone yet, so the Sharing page adds one, defaulting to the browser's.
- Posting is idempotent: there's one summary per user per ISO week, so a worker restart doesn't post twice.
- Weeks with no plays are skipped.

Example:

> **This week: 312 scrobbles**
> Top artists: 1. Radiohead (48) 2. Björk (31) 3. Portishead (22)
> Top track: *Reckoner* by Radiohead (12 plays)
> #Scrobbler

### Milestones

These are checked after each scrobble batch and each import, and each one is posted at most once:

- **Scrobble counts**: the 1,000th, 5,000th and 10,000th scrobble, then every 25,000.
- **Artist plays**: an artist's 100th or 500th play.
- **All-time top 10**: a new artist enters it.

History imports never produce milestone posts: importing 50,000 old plays shouldn't set off a burst of them. Milestones only start counting after the import.

### Now playing

A new post for every track would flood followers' timelines. There are two options:

1. **Profile field (recommended default)**: a "Now playing: *Reckoner* by Radiohead" field on the profile. The field is updated with an `Update(Person)` when the track changes, at most once every 5 minutes, and cleared when playback stops. Nothing appears in timelines; followers see it on the profile. This option is cheap and quiet.
2. **Posts (opt-in)**: a `Note` when a track starts, at most one every 30 minutes, and only for tracks played for at least 30 seconds.
   - It uses the user's visibility setting (followers only by default).
   - Optionally, the previous now-playing post is deleted when the next one goes out, so only one exists at a time.

## Build or buy

| | Write it ourselves (recommended) | Pubby (Python) | Fedify (TypeScript) sidecar |
|---|---|---|---|
| Fits the codebase | Same Flask, SQLAlchemy, worker, metrics and OpenAPI patterns | Flask adapter and SQLAlchemy storage | A second language and service |
| Signatures | Draft-cavage and RFC 9421 (we write both) | HTTP Signatures (built in) | Draft, RFC 9421, FEP-8b32 |
| **Licence** | Ours | **AGPL-3.0-or-later**: offering the service over a network brings AGPL obligations for the whole app | MIT |
| Maturity | New code, but the scope is narrow | Active, but young (0.3.x, one maintainer) | The most complete framework |
| Effort | About 1,500 lines, mostly signatures, inbox and delivery | Less code, but we'd adapt its storage and flow to ours | A new service to run and monitor |

**Decided: write it ourselves.** Scrobbler is BSD-3-Clause licensed. Using Pubby inside the app would make the running program subject to the AGPL, which defeats the point of a permissive licence. Fedify is excellent, but it would split the backend across two languages. The protocol surface we need is small (four activity types in and three out), so writing it ourselves is the least total cost. It also keeps ActivityPub on the same metrics, dashboards and tests as everything else, and it can all live in the one replaceable module described above.

New dependency: `cryptography` (Apache-2.0 or BSD), for keys and signatures.

## Data model

All of these are federation's own tables, prefixed `federation_` and managed in its own Alembic branch (the names below leave the prefix off, except the first). The only link to the core schema is a foreign key to `users`.

| Table | Holds |
|---|---|
| `federation_settings` | Per user: `enabled`, visibility, which post types, now-playing mode, manual approval, discoverable and indexable, time zone, display name, bio, avatar |
| `actor_keys` | Per user, plus one instance actor: public key PEM, encrypted private key, created and rotated times |
| `remote_actors` | Cache: actor URI, inbox, shared inbox, key ID and PEM, handle, display name, `fetched_at`, health |
| `followers` | Local user ↔ remote actor, with state (`pending`, `accepted`), the Follow activity ID, and when |
| `blocks` | Per-user remote-actor blocks, plus admin domain blocks |
| `outbox_activities` | Our activities (ID, type, JSON, visibility, published). They are the source for the outbox collection and for deliveries |
| `deliveries` | Activity × target inbox: `attempts`, `next_attempt_at`, `status`, `last_error` |
| `inbox_activities` | Received activity IDs (for deduplication), type, actor, and outcome. Pruned after 30 days |
| `posted_milestones` / `weekly_posts` | One row per posted milestone or week, which keeps posting idempotent |

## Security and privacy

- **Opt-in.** Nothing about a user is published or discoverable until they turn sharing on. Existing listening history is never posted retroactively: summaries start from the next week, and milestones from the next scrobble.
- **Nothing that allows tracking**: we publish aggregates and choices the user made explicitly, never raw scrobble timestamps. Now-playing is throttled and can be switched off.
- **The inbox is the new attack surface**:
  - signature checks before any processing
  - body size limits
  - rate limits per remote host
  - activity deduplication
  - the SSRF-hardened fetcher described above
  - no HTML from remote servers is ever rendered in v1
- **Followers can be removed or blocked**, and admins can block whole domains.
- **Private keys are encrypted at rest**, with `FEDERATION_KEY_SECRET` kept separate from `SECRET_KEY`.

## Observability

Everything goes on the existing Prometheus and Grafana setup:

- **Metrics**:
  - `scrobbler_federation_inbox_activities_total{type,result}`
  - `scrobbler_federation_signature_failures_total{scheme,reason}`
  - `scrobbler_federation_deliveries_total{result}`
  - `scrobbler_federation_delivery_queue{status}`
  - `scrobbler_federation_followers`
  - `scrobbler_federation_posts_total{kind}`
- **Dashboard**: a new **Federation** dashboard.
- **Alerts**: the delivery queue growing for 30 minutes, and signature failures spiking.

## Testing and interoperability

- **Unit tests**:
  - signing and verification against spec test vectors and recorded requests
  - WebFinger, actor and collection shapes
  - inbox state transitions
  - delivery retry schedule
  - visibility addressing
- **In-process federation**: two Scrobbler instances in one test process following each other.
- **A real peer in CI**: a [GoToSocial](https://gotosocial.org) container, which is a single binary with SQLite. It follows a Scrobbler user and we assert that it receives the `Accept` and the posts. That's much lighter than running Mastodon in CI.
- **Manual pass before release**:
  - follow from a real Mastodon account
  - check the profile, posts, profile-field updates and unfollowing
  - check with [verify.funfedi.dev](https://verify.funfedi.dev/) or the [ActivityPub Academy](https://activitypub.academy/)

## Phases

Each phase can be released on its own, with its migration, docs, OpenAPI updates, metrics and tests.

0. **Groundwork**, with no user-visible change:
   - `scrobbler/events.py`, and the core sending its events
   - the generic `scrobbler/worker.py`, with imports moved onto it
   - the empty `scrobbler.federation` package with `init_app`
   - its Alembic branch
   - the boundary tests

   This lands first so every later phase is built inside the boundary from day one.
1. **Discoverable profile.** Settings, keys, WebFinger, the actor, NodeInfo and the Sharing page. A user can turn sharing on and be found from Mastodon, but can't be followed yet.
2. **Followers.** Inbox, signature verification (both schemes), the delivery queue with retries and signing, `Accept`, removing and blocking followers, the Followers page, and the GoToSocial CI test.
3. **Posts.** The outbox, weekly summaries, milestones, and deleting posts when sharing is turned off. Adds the Federation dashboard and alerts.
4. **Now playing.** The profile-field mode, then optional posts.

## Later: following and `Listen` activities

This is the "profiles plus friends' listening" option, deliberately left out of v1:

- Users follow other Scrobbler users, or any actor that publishes listens, and see a friends feed in the UI.
- **Wire format**: ActivityStreams has a `Listen` activity type. Funkwhale already federates `Listen` activities whose object is a `Track`. We'd send the same thing to followers on Scrobbler and Funkwhale servers only. Mastodon ignores activity types it doesn't know, and a stream of `Listen`s isn't something people want in their timelines anyway.
- **Changes needed**: an outbound `Follow` flow, storing received listens, a feed UI, and moderation of incoming content.
- **Account migration** (`Move` and `alsoKnownAs`) would come with this, so users can change handle or domain without losing followers.

## Decisions for you

Settled so far: **build it ourselves** (the project is BSD-3-Clause), inside **one replaceable module**.

Also settled:

- **Domain: the UI's.** Handles and actor URLs are both on the UI host, and the UI's nginx forwards the ActivityPub paths to the API.
- **Visibility: followers only by default**, and each user can change it on their Sharing settings.

Still open (neither is needed before phase 3):

1. **Now-playing default** once a user turns it on: the profile field (recommended), or posts?
2. **Does it need an admin?** Phase 2 assumes domain blocks set in configuration. A real moderation UI (reports, per-domain policies) would be a separate piece of work.

## Sources

- [Mastodon ActivityPub spec](https://docs.joinmastodon.org/spec/activitypub/): object rendering, supported activities, actor properties, visibility addressing, allowed HTML
- [Mastodon security spec](https://docs.joinmastodon.org/spec/security/): draft-cavage requirements, RFC 9421 accepted from 4.5, 12-hour clock skew, LD Signatures not advised
- [Mastodon 4.7 release notes](https://blog.joinmastodon.org/2026/08/mastodon-4.7/): RFC 9421 fallback when sending, FEP-521a keys (RSA, Ed25519, ML-DSA-44), FEP-8b32, encrypted private keys
- [Mastodon WebFinger spec](https://docs.joinmastodon.org/spec/webfinger/)
- [SocialHub: RFC 9421 HTTP signatures in 2026](https://socialhub.activitypub.rocks/t/rfc-9421-http-signatures-in-2026/8427)
- [Funkwhale federation docs](https://docs.funkwhale.audio/develop/developer/federation/index.html): the `Listen` activity with a `Track` object
- [Pubby on PyPI](https://pypi.org/project/pubby/): features and AGPL-3.0-or-later licence
