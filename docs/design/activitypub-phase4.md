# ActivityPub phase 4: now playing

This implements phase 4 of [activitypub.md](activitypub.md): **now playing**, the last of the "still open" decisions there. It builds on [phase 3](activitypub-phase3.md): publishing, delivery and the web UI for posts.

Chosen (of the doc's three options): **profile field, plus opt-in posts** — the profile field is what a user gets as soon as they turn now playing on at all; posts are a further, separate opt-in on top of that, matching the doc's own "profile field mode, then optional posts" framing.

**Outcome:**

- A sharing user can turn now playing on. While something is genuinely playing, their fediverse profile shows a "Now playing: *Track* by Artist" field, refreshed automatically and cleared a few minutes after playback stops.
- If they also opt into posts, each track they play for at least 30 seconds gets a post to their followers (at most one every 30 minutes), with the previous now-playing post deleted when a new one goes out — so at most one exists at a time.
- Nothing here is retroactive or history-based: it only ever reflects what's playing *right now*, so there's no baseline/import concern like milestones has.

Everything is in `scrobbler.federation`, using the core's existing `services.scrobbles.get_now_playing()` (no core changes needed this time), and the boundary tests keep holding.

## 1. Settings and data (migration on the `federation` branch)

- **`federation_settings` gains `now_playing_mode`**: `off` (default), `profile`, or `posts`. Validated the same way `visibility` is.
- **`federation_now_playing`**, one row per user, the state of what Scrobbler has last told the fediverse (as opposed to `now_playing` in core, which is what's actually playing):
  - `profile_key` (nullable) and `profile_updated_at` (nullable): what the last `Update(Person)` said, and when it was sent
  - `post_key` (nullable) and `post_posted_at` (nullable): the key of the last now-playing post, and when

  A "key" is `artist\x1ftrack\x1fstarted_at` — including `started_at` means playing the same track again later is a genuinely new now-playing moment, not a stale repeat, and it's what `federation_posts.key` uses too (so posting is idempotent through the same unique-constraint mechanism phase 3 already has, no new machinery needed).

## 2. Vocabulary (pure: `protocol/vocab.py`)

- **`update(activity_id, actor_id, actor_object)`**: wraps a document as an `Update`, addressed the same way `undo()` already does (mirrors its shape).
- **`person(...)` already takes `fields`** (phase 1): a "Now playing" field is just one more `(name, value)` pair, `"Track by Artist"`.
- Now-playing posts reuse the existing `note()`/`create()` builders from phase 3, tagged `#NowPlaying` in addition to `#Scrobbler` (already anticipated in the phase 3 plan's vocabulary notes).

## 3. Live actor vs. pushed updates

Two different questions, answered two different ways:

- **`GET /users/<u>` (a pull)** always reflects the truth *right now*: it reads `get_now_playing()` directly and adds the field only while something unexpired is playing. No throttling — a fresh fetch should never lie.
- **`Update(Person)` (a push)**, telling already-following servers proactively so they don't have to poll, is throttled to **once every 5 minutes** (`federation_now_playing.profile_updated_at`). A server that fetches in between sees the live truth anyway via the pull path above; the push is purely so it doesn't have to.

This split means a fast-changing "now playing" never spams followers' inboxes, while the profile itself is never stale to a server that asks.

## 4. `publishing/now_playing.py`

A periodic worker task (`federation.now_playing`, every 30 seconds — well under both throttle windows, so they stay the binding constraint) checks every sharing user with `now_playing_mode != "off"`:

1. Read `get_now_playing(user)` (core service; federation reaches it through `scrobbler.services.scrobbles`, already inside the public surface) and compute `current_key` (`None` if nothing's playing or it's expired).
2. **Profile** (mode `profile` or `posts`): if `current_key != federation_now_playing.profile_key` and at least 5 minutes have passed since `profile_updated_at` (or it's never been sent), build the actor document with (or without) the now-playing field and send `Update(Person)` to accepted followers' inboxes. Update the stored key and timestamp either way the check runs, so a change that arrives mid-throttle isn't retried every 30 seconds — only reconsidered once the throttle clears.
3. **Posts** (mode `posts` only): if `current_key` is set, has been playing for **at least 30 seconds**, differs from `federation_now_playing.post_key`, and at least 30 minutes have passed since `post_posted_at` (or never posted): publish a `now_playing` post (through the same `publishing.publish()` as weekly/milestones — idempotent on `now_playing:<key>`), then delete the previous now-playing post if one exists, and update the stored key and timestamp.

No event receiver, no pending-checks queue: unlike milestones, now playing doesn't need to react within a scrobble request, and the throttle windows (5 and 30 minutes) are so much longer than a 30-second poll that a periodic scan (the same shape as `weekly.check_all`) is simplest and cheapest.

## 5. Endpoints

- **`GET /users/<u>`**: gains the live now-playing field (§3). No new endpoint.
- **`GET /users/<u>/outbox`, `GET /users/<u>/posts/<uuid>`, `/api/v1/federation/posts`, `DELETE /posts/{id}`**: unchanged — `now_playing` posts are just another `kind` in `federation_posts`, so they already show up everywhere phase 3's post endpoints look.
- **`/api/v1/federation/settings`**: gains `now_playing_mode`.

## 6. Web UI

- **Sharing page**, a new **Now playing** section: off / profile field / profile field + posts, as a single choice (matching the settings column), with a one-line explanation of the 5-minute and 30-minute throttles.
- **Posts page**: `now_playing` posts show there like any other kind (already handled generically).
- **Profile page**: shows the "Now playing" field when the profile response includes it (the public profile API already returns arbitrary settings-derived fields; this adds one, read from the same live path as the actor document).

## 7. Metrics, dashboard

- `scrobbler_federation_profile_updates_total{result}`: `sent` or `throttled`. (Posts already get `scrobbler_federation_posts_total{kind="now_playing",visibility}` for free, phase 3's existing metric.)
- Federation dashboard: a panel on the existing "Posts" row for profile updates/min, and the `kind="now_playing"` series already shows up in the existing posts-by-kind panel without changes.

## 8. Tests

- **Vocabulary**: `update()` shape.
- **Actor document**: the live now-playing field appears while playing, is absent once expired, absent when off.
- **`now_playing.py`**:
  - profile update sent on a genuine change, throttled within 5 minutes, cleared when playback stops
  - post sent only after 30 seconds played, throttled within 30 minutes, previous post deleted when a new one goes out
  - `profile` mode never posts; `off` mode does nothing at all
  - idempotence (the same key never posted twice, matching phase 3's pattern)
- **Settings API**: `now_playing_mode` validation and round-trip.
- **Interop (GoToSocial)**: extends the existing driver — now playing is turned on, a track is set as playing, and the test confirms GoToSocial's resolved account shows the "Now playing" field after the next `Update(Person)`; posts aren't re-tested here since they go through the same publish/delivery path phase 3 already covers end to end.

## 9. Docs

- README: the now-playing section, its modes and throttles.
- Federation README: `now_playing.py` added to the publishing plug-ins list.
- Regenerate the OpenAPI export.
- Mark this plan done.

## Order of work (a commit per step once its tests pass)

1. Settings column and `federation_now_playing` table
2. Vocabulary (`update()`) and the live actor now-playing field
3. `publishing/now_playing.py` and its worker task
4. Settings API and schema
5. Web UI (Sharing page section, profile page field)
6. Metrics and dashboard
7. Interop check
8. Docs

## Defaults I've chosen (easy to change)

| Choice | Default |
|---|---|
| Now playing | Off by default, per user |
| Profile push throttle | 5 minutes |
| Post throttle | 30 minutes |
| Minimum played before a post | 30 seconds |
| Deleting the previous now-playing post | Always, when a new one goes out |
| Worker poll interval | 30 seconds |
| Hashtags | `#Scrobbler` and `#NowPlaying` |
