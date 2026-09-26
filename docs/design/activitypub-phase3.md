# ActivityPub phase 3: posts

**Status: done** on `feature/activitypub` (`bc717c2` … `860d4d0`). The GoToSocial interop test now covers posts too: a weekly summary reaches a follower's home timeline as `private` with the expected content, and disappears from it when deleted. As with phases 1-2, a manual pass with a real Mastodon account through a tunnel is still to do before merging.

Where this differed from the plan:

- **A top-10 entry needs a real top 10 first.** With fewer than `TOP_N` distinct artists, every first play of a new artist would trivially "enter the top 10" — caught by a test that used the real (unpatched) thresholds instead of always overriding them down for speed. Nothing is posted until a user actually has that many distinct artists.
- **`post_now()` is a separate path from the normal check.** `flask federation post-weekly --now` and the Sharing page's preview both need the week still *in progress*, not the last one whose Monday-09:00 gate has passed; they share the posting logic (`_post_for_window`) with the normal `check_and_post`, but compute a different window.
- **The Note's `url` needed the username, not just the post's id.** `post.html` (the UI page a browser lands on) has no other way to know whose post to look up; caught while wiring up the UI, not by a test written against the plan.

This implements phase 3 of [activitypub.md](activitypub.md): **weekly summaries** and **milestones**. Now playing is phase 4. It builds on [phase 2](activitypub-phase2.md): followers, signed delivery and retries.

**Outcome:**

- A user who shares gets two kinds of post, delivered to their followers: a summary of the past week, every Monday morning in their own time zone, and occasional milestone posts.
- Posts are **followers only by default**, following the user's visibility setting.
- Each kind can be switched off, and any post can be deleted. Turning sharing off deletes the posts from followers' servers.
- Existing history never produces posts retroactively.

Everything is in `scrobbler.federation` except one small addition to the core stats service (§1), and the boundary tests keep holding.

## 1. Core: stats over a date range

- `services.stats` gains top artists, top tracks and play counts for an explicit window (`start`, `end`). Today it only knows the rolling periods (`7day` and so on).
- The existing functions keep working, re-expressed on top of the window version. This is a core change and is tested like one. Federation uses it through the public service, as the boundary rules require.

## 2. Settings and data (migration on the `federation` branch)

- **`federation_settings` gains:**
  - `timezone`: an IANA name such as `Europe/London`, validated with `zoneinfo`. The Sharing page fills it in from the browser the first time.
  - `post_weekly_summary`: default on.
  - `post_milestones`: default on.
- **`federation_posts`**, one row per post:
  - user, `kind` (`weekly` or `milestone`)
  - `key`, unique per user, e.g. `weekly:2026-W39` or `milestone:scrobbles:10000`
  - the Note's UUID, the visibility it was sent with, plain-text and HTML content
  - `created_at`, and `deleted_at` (a tombstone)

  The unique key makes posting **idempotent**: a worker restart or a repeated check can never post twice.
- **`federation_milestone_marks`**: user plus key, with `posted` true or false. It records thresholds already passed, including ones deliberately *not* posted (see §5).
- **`federation_pending_checks`**: user, `since`, `reason`. It's a small queue, filled by event receivers (§5), so receivers never do real work inside a scrobble request.

## 3. Vocabulary (pure: `protocol/vocab.py`)

- **`note(...)`**: a `Note` with:
  - `id`, `attributedTo`, `content` (HTML) plus `contentMap` (`{"en": …}`)
  - `published`, `url` (the UI post page)
  - `to` and `cc` from the visibility setting
  - `tag` (`Hashtag` objects: `#Scrobbler`, and `#NowPlaying` in phase 4)
  - `sensitive: false`
- **`create(...)`**: `Create` wrapping the Note, with the same addressing.
- **`delete(...)`**: `Delete` whose object is a `Tombstone` of the Note (the same shape Mastodon sends).
- **`ordered_collection_page(...)`**, used for outbox paging.
- **Addressing** comes from one function (`protocol/addressing.py`), unit-tested for each visibility:
  - followers only: `to` followers, no `cc`
  - unlisted: `to` followers, `cc` Public
  - public: `to` Public, `cc` followers
- The HTML uses only tags Mastodon keeps (`p`, `br`, `strong`, `em`, `ol`, `li`), and every artist and track name is escaped. A formatting test covers awkward names (`<script>`, `&`, emoji, right-to-left text).

## 4. Publishing core (`federation/publishing/`)

- **`base.py`**: `publish(user, kind, key, text, html)`:
  1. Refuse if the key already exists, or if the user has stopped sharing.
  2. Store the post.
  3. Build `Create(Note)` with the user's current visibility, and store it as an activity, marked public when the visibility isn't followers only.
  4. Fan out one delivery per **distinct shared inbox** of the accepted followers (their own inbox when they have no shared one).

  Everything happens in one transaction; the existing delivery task sends it.
- **Post kinds are plug-ins.** `weekly.py` and `milestones.py` each register a kind with its settings flag, and phase 4's now playing will slot in the same way.
- **`delete_post(user, post)`**: sends `Delete(Tombstone)` to the same inboxes, marks the post deleted, and keeps the row so the key can't be reused.
- **Turning sharing off** now sends `Delete` for every live post *before* the Rejects (phase 2), in the same fan-out. The farewell actor (phase 2) stays up until those are delivered too.

## 5. The two kinds

**Weekly summary** (`publishing/weekly.py`, worker task `federation.weekly`, every 15 minutes):

- For each sharing user with summaries on:
  - once it's past **Monday 09:00 in their time zone**,
  - if `weekly:<ISO year-week of the week just ended>` doesn't exist yet,
  - build the summary from **Monday 00:00 to Sunday 24:00 local time** (correct across daylight-saving changes, since the boundaries are computed in their zone and converted to UTC).
- **Skipped** if the week had no plays; a `skipped` mark is kept so it isn't reconsidered.
- **Catch-up:** only the most recent week is posted. A worker that was down for three weeks posts one summary, not three.
- Example text (plain text shown; the HTML version has the same content):

  > **My week in music: 312 plays**
  > Top artists: 1. Radiohead (48) 2. Björk (31) 3. Portishead (22)
  > Most played: *Reckoner* by Radiohead (12 plays)
  > #Scrobbler

**Milestones** (`publishing/milestones.py`, worker task `federation.milestones`):

- **Rules:**
  - **Scrobble count**: 1,000, 5,000 and 10,000, then every 25,000.
  - **An artist's plays**: 100 and 500 (and 1,000).
  - **All-time top 10**: an artist enters it for the first time.
- **Detection is cheap and off the request path.**
  - The `scrobbles_stored` receiver (live scrobbles only, `source="scrobble"`) just upserts a pending check for the user.
  - The worker task then compares current totals with the marks and posts at most **one milestone per check**, the most notable, so a single batch can't spam followers.
- **No retroactive posts:**
  - When a user **turns sharing on**, every threshold they've already passed is marked `posted=false`.
  - When an **import finishes** (`import_finished`), the same happens: imported history moves the baseline silently. Imported plays therefore never trigger milestone posts, even if a live scrobble crosses a line soon after.
- Example: *"10,000 plays on Scrobbler 🎉"* or *"Björk just joined my all-time top 10 (at #7)"*.

## 6. Endpoints (served on the UI domain, documented in OpenAPI)

- **`GET /users/<u>/outbox`**: now paged, as an `OrderedCollection` with `first`, and `OrderedCollectionPage`s of 20, newest first.
  - It holds the user's **public and unlisted** `Create`s only.
  - Followers-only posts are never listed, since this is unauthenticated.
- **`GET /users/<u>/posts/<uuid>`**: the Note, for public and unlisted posts. Followers-only and deleted posts return 404 for now; Mastodon receives them by delivery, and signed fetches by followers can come later.
  - `/activities/<uuid>` already serves public `Create`s.
  - A browser (no ActivityPub `Accept`) is redirected to the UI post page.
- **`/api/v1/federation`**, bearer auth:
  - `GET /posts`: the user's posts, newest first, with kind, text, visibility, created and deleted.
  - `DELETE /posts/{id}`: sends `Delete` and tombstones the post.
  - `GET /preview/weekly`: renders what the next weekly summary would say, from the current partial week, without posting.
  - The settings gain `timezone`, `post_weekly_summary` and `post_milestones`.

## 7. Web UI

- **Sharing page**, a new **What to post** section:
  - on/off switches for **Weekly summary** and **Milestones**
  - a **time zone** picker (defaulting to the browser's), with "Summaries are posted on Monday mornings in this time zone"
  - **Preview this week's summary**, which shows the text it would post now
- **A Posts list** (on the Followers page, or its own page if it's long): recent posts with their visibility and a **Delete** button behind a confirmation. Deleted posts are shown struck through for a while.
- **`post.html?id=…`**: a public page for a post, where browsers land from a Note URL. It shows the text for public and unlisted posts, and "only visible to followers" otherwise.

## 8. Metrics, dashboard and alerts

- **Metrics:**
  - `scrobbler_federation_posts_total{kind,visibility}`
  - `scrobbler_federation_post_deletions_total{reason}`, where reason is `user` or `sharing_off`
  - `scrobbler_federation_milestone_checks_total{result}`: `posted`, `none`, `baseline`
  - `scrobbler_federation_weekly_total{result}`: `posted`, `skipped_empty`, `disabled`
- **Federation dashboard, new "Posts" row:** posts by kind, deletions, weekly results, milestone checks, and the delivery fan-out size per post (a histogram).
- **Alert**, `FederationWeeklySummariesMissing`: on a Monday after 12:00 UTC, no `weekly` results at all in the last 6 hours even though users are sharing. That means the task isn't running. It's evaluated only on Mondays, so it can't fire on quiet days.

## 9. Tests

- **Stats window (core):** week boundaries, the rolling periods still behaving as before.
- **Vocabulary and addressing:** Note, Create and Delete shapes; each visibility's `to`/`cc`; HTML escaping of hostile names.
- **Weekly scheduling:**
  - Monday 08:59 vs 09:00 in several zones (`Pacific/Auckland`, `America/Los_Angeles`, `Europe/London` across a DST change)
  - ISO weeks at a year boundary
  - an empty week skipped
  - posting twice impossible
  - three missed weeks give one post
  - summaries switched off
- **Milestones:**
  - each rule
  - one post per check
  - live scrobbles only
  - the import baseline and the enable-sharing baseline (no retroactive posts)
  - idempotence
- **Fan-out:** accepted followers only, one delivery per shared inbox, pending followers excluded.
- **Outbox and Note endpoints:** paging, only public and unlisted listed, followers-only and deleted Notes 404, the browser redirect.
- **Deleting:** a single post, and all posts plus Rejects when sharing is turned off, with the farewell actor serving until delivered.
- **Interop (GoToSocial):**
  - a new admin command `flask federation post-weekly --user <name> --now` (for operators too) posts immediately
  - the driver checks the post reaches the GoToSocial follower's home timeline with visibility `private` (followers only) and the expected text
  - then deletes it and checks it's gone there too

## 10. Docs

- README: what gets posted, and the settings.
- Federation README: the publishing plug-ins.
- Regenerate the OpenAPI export.
- Mark this plan done.

## Order of work (a commit per step once its tests pass)

1. Core stats windows
2. Settings columns, posts, marks and pending-checks tables
3. Vocabulary and addressing
4. Publishing core (publish, fan-out, delete) and deleting posts when sharing is turned off
5. Weekly summary task
6. Milestones (receivers, task, baselines)
7. Outbox paging and Note endpoints
8. API and web UI
9. Metrics, dashboard and alert
10. Admin command and interop checks
11. Docs

## Defaults I've chosen (easy to change)

| Choice | Default |
|---|---|
| When weekly summaries post | Monday 09:00 in the user's time zone, for the Monday–Sunday just ended |
| Milestone thresholds | Plays: 1k, 5k, 10k, then every 25k. An artist's plays: 100, 500, 1k. New artist in the all-time top 10 |
| Posts per milestone check | At most one (the most notable) |
| Post language | English only for now (`contentMap.en`) |
| Hashtag | `#Scrobbler` on every post |
| Deleting individual posts | Allowed, from the web UI |
