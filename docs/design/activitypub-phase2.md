# ActivityPub phase 2: followers

**Status: done** on `feature/activitypub` (`a6d3fb6` … `fe95f59`). The GoToSocial interop test passes locally and in CI. Before merging, the plan still calls for a manual pass with a real Mastodon account through a tunnel.

Where this differed from the plan:

- **httpcore instead of httpx.** httpcore is the transport layer underneath httpx, and it publicly supports the custom network backend that connecting to a pre-checked address needs.
- **No in-process test with two Scrobbler instances.** Scrobbler can't initiate follows yet, so one instance has nothing to send the other. Scripted remote accounts over real HTTP cover the same paths, and GoToSocial is the real second server.
- **Undo of a Block.** Unblocking needed it: GoToSocial otherwise keeps the block.

This implements phase 2 of [activitypub.md](activitypub.md), building on [phase 1](activitypub-phase1.md).

**Outcome:** people on Mastodon, GoToSocial and similar servers can follow a user who shares. Follows are accepted automatically, or wait for the user's approval if they've turned it on. The user sees their followers and can approve, decline, remove or block them. Scrobbler signs everything it sends and checks the signature on everything it receives. Posts come in phase 3; this phase makes the relationship work end to end.

Everything below is inside `scrobbler.federation`, and the boundary tests keep holding. Federation off still means nothing is registered.

## 1. HTTP signatures (pure: `protocol/signatures/`)

- **`base.py`**:
  - A `SignedRequest` value (method, URL, headers, body).
  - A `SignatureScheme` interface with `sign(request, key_id, private_key)` and `verify(request, public_key_for) -> VerifiedSignature` (the key ID used and the scheme).
  - `SignatureError` carries a machine-readable reason: `missing`, `malformed`, `expired`, `digest_mismatch`, `bad_signature` or `unsupported`.
  - A registry keyed by scheme name.
- **`draft_cavage.py`**:
  - **Signing** (the header shapes are in [Mastodon's security spec](https://docs.joinmastodon.org/spec/security/)):
    - `Signature: keyId="…",algorithm="rsa-sha256",headers="(request-target) host date digest",signature="…"`
    - `Digest: SHA-256=<base64>` for POSTs
    - `(request-target)` is the lowercase method plus path and query.
  - **Verification**:
    - accepts `rsa-sha256` and `hs2019`, which means RSA when the key is RSA
    - for POSTs, requires `(request-target)`, `host`, `date` and `digest` to be signed
    - `Date` within ±12 hours
    - the digest must match the body
- **`rfc9421.py`**:
  - **Signing**: `Signature-Input: sig1=("@method" "@target-uri" "content-digest");created=…;keyid="…";alg="rsa-v1_5-sha256"`, with `Signature: sig1=:…:` and `Content-Digest: sha-256=:<base64>:`.
  - **Verification**:
    - one signature
    - `created` required and within the allowed skew
    - `@method` and `@target-uri` covered, and `content-digest` too when there's a body
  - A minimal parser for the RFC 8941 structured fields these headers use: inner lists, parameters and byte sequences.
- **Order**:
  - Outgoing: `FEDERATION_SIGNATURE_SCHEMES` (default `draft-cavage,rfc9421`) sets the order schemes are tried. The first is used, and the next is tried only if a server answers 401, the same "double-knocking" Mastodon 4.7 does.
  - Incoming: whichever scheme is present is verified.
- **Tests**:
  - the published test vectors (draft-cavage appendix C; RFC 9421 appendix B for `rsa-v1_5-sha256`)
  - sign-then-verify round trips for both schemes
  - tampered body, method, path, date and headers
  - expired and future dates, missing components, and a wrong key

## 2. Safe outbound HTTP and remote actors

- **New dependency**: `httpx` (BSD-3-Clause), for pooled clients, timeouts and response streaming.
- **`net.py`**, the only way federation code talks to other servers:
  - **HTTPS only**.
  - **SSRF protection**: a host's addresses are resolved once; the request is refused if any is loopback, private, link-local, multicast or otherwise reserved; and the connection goes to the checked address, using the original host name for SNI and `Host`. So a DNS change mid-request can't redirect it.
  - At most 3 redirects, each checked the same way.
  - Response bodies are capped at 1 MB.
  - Timeouts: 10s to connect and 20s to read.
  - A `User-Agent` of `Scrobbler/<version> (+<base url>)`.
  - `signed_get(url, as_actor)`, which signs with the instance actor by default so "authorized fetch" servers answer; and `signed_post(inbox, activity, as_user)`.
- **`FEDERATION_INSECURE_TESTING=1`** is **for automated tests only**. It allows `http://` and private addresses. It's refused unless the domain ends in `.test` (a reserved top-level domain) and logs a warning every minute while on. It exists so CI can federate with GoToSocial on a private Docker network.
- **`remote.py`**: the remote-actor cache.
  - `get_actor(uri, refresh=False)` fetches, validates and stores: the ID must equal the URI it was fetched from, the host must match, it must have an `inbox`, and `publicKey.owner` must equal the actor ID.
  - `key_for(key_id)` maps a key ID (`…#main-key`, or a separate key document) to its owner and PEM.
  - An actor is refreshed after 24 hours, or once immediately when a signature fails, in case the key was rotated.

## 3. Data (migration on the `federation` branch)

| Table | Holds |
|---|---|
| `federation_remote_actors` | URI, inbox, shared inbox, `preferredUsername`, host, display name, public key ID and PEM, `fetched_at`, `gone` |
| `federation_followers` | User, remote actor, state (`pending` or `accepted`), the Follow activity's ID and JSON, created and accepted times. Unique per user and actor. |
| `federation_blocks` | User and remote actor, or a whole domain. Operator domain blocks come from `FEDERATION_BLOCKED_DOMAINS`, not this table. |
| `federation_inbox` | Received deliveries: raw headers (a fixed allow-list), body, the inbox path, `received_at`, status (`queued`, `processed`, `rejected` or `failed`), reason, and the activity ID (unique, for deduplication). Pruned after 30 days. |
| `federation_activities` | Activities we send: UUID, user, type, JSON, and `created_at`. Dereferenceable at `/activities/<uuid>` when public, so it can be the outbox's source in phase 3. |
| `federation_deliveries` | Activity × inbox: status (`pending`, `delivered`, `failed` or `abandoned`), `attempts`, `next_attempt_at`, the last status code and error |

## 4. Inbox intake (`web.py`) and processing (a worker task)

- **Intake stays network-free.** `POST /inbox` and `POST /users/<u>/inbox` check the size (256 KB), the content type and that the JSON has `type`, `actor` and `id`, then store the delivery and answer **202**.
  - Deliveries from blocked domains are refused with 403.
  - Repeated activity IDs are acknowledged but not stored again.
  - The UI's nginx rate-limits the inbox paths per client IP (`limit_req`), as a first defence.
- **The `federation.inbox` worker task** processes queued deliveries, oldest first:
  1. **Check the signature** with the scheme present, fetching the key through `remote.key_for`.
     - On failure, it refreshes the actor once and retries.
     - Then it rejects with a reason, which is counted in metrics.
  2. **Check it's who they say.** The signing key's owner must equal the activity's `actor`, and the activity ID's host must equal the actor's host.
  3. **Dispatch** to a handler registered by activity type:

| Activity | Handling |
|---|---|
| `Follow` (object = a sharing user) | Store the follower. If manual approval is on, keep it `pending`; otherwise mark it `accepted` and queue an `Accept`. If already accepted (a repeat Follow), queue an `Accept` again. |
| `Undo` of a `Follow` | Remove the follower. The Undo must come from the same actor as the Follow. |
| `Delete` of an actor | Remove it from every user's followers and mark the cached actor gone. |
| `Update` of a `Person` or `Service` | Refresh the cached actor. |
| `Accept` or `Reject` | Recorded; nothing to do until we follow people. |
| `Like`, `Announce`, `Create` | Recorded for metrics only. |
| Anything else | Recorded as unsupported. |

- **A delivery that errors unexpectedly** is retried twice and then marked `failed` with the error. The worker never stops over it.

## 5. Sending: activities and deliveries

- `activities.py` builds and stores our activities:
  - **`Accept(Follow)`**: the object is the original Follow, embedded.
  - **`Reject(Follow)`**: used both to decline a request and to remove an existing follower. Mastodon drops the relationship on either.
  - **`Block`**: sent when a user blocks a follower.
- **Fan-out** turns an activity into delivery rows, one per distinct inbox. Replies to a Follow go to the follower's own inbox.
- **The `federation.deliver` worker task**:
  - Sends pending deliveries whose time has come, with signed POSTs through `net.signed_post`, trying the next scheme on a 401.
  - **Retries**: 1m, 5m, 30m, 2h, 6h, then every 12h, **abandoning after 2 days**.
  - No retry after a 4xx, except 401, 408 and 429.
  - **410 Gone** from an actor's inbox removes that follower.
  - It runs several deliveries at once (a bounded thread pool, default 4) and processes at most 50 per pass, so the worker stays responsive.
- **Actor changes**:
  - The actor now reflects the user's real `manually_approves_followers` setting; phase 1 always said true.
  - The followers collection's `totalItems` is the accepted-follower count.
- **Turning sharing off** queues a `Reject` for every follower, then removes them.

## 6. Managing followers (API and UI)

- **API** (bearer auth, under `/api/v1/federation`, documented in OpenAPI):
  - `GET /followers?state=accepted|pending`, returning handle, display name, actor URL, server and since when.
  - `POST /followers/{id}/approve`, which sends `Accept`.
  - `POST /followers/{id}/decline` and `POST /followers/{id}/remove`, which send `Reject`.
  - `POST /followers/{id}/block`, which sends `Block` and remembers it.
  - `GET /blocks` and `DELETE /blocks/{id}` to see and undo blocks. Unblocking doesn't restore the follow.
  - The settings response gains accepted and pending follower counts.
- **UI**:
  - A new `followers.html` shows requests (Approve / Decline) above followers (Remove / Block, each with a confirm step), and blocked accounts at the bottom.
  - Remote names are shown **as plain text only**; no remote HTML or images in this phase.
  - The Sharing page shows the follower count and links to the Followers page.
  - The "following arrives later" note goes.

## 7. Configuration

| Variable | Meaning |
|---|---|
| `FEDERATION_SIGNATURE_SCHEMES` | Outgoing order; default `draft-cavage,rfc9421` |
| `FEDERATION_BLOCKED_DOMAINS` | Comma-separated domains (subdomains included) whose deliveries are refused and whose follows are rejected |
| `FEDERATION_DELIVERY_CONCURRENCY` | Parallel deliveries in the worker (default 4) |
| `FEDERATION_INSECURE_TESTING` | Tests only; see §2 |

## 8. Metrics, dashboard and alerts

- **Metrics**:
  - `scrobbler_federation_inbox_activities_total{type,result}`, where result is `processed`, `rejected`, `duplicate`, `blocked` or `failed`
  - `scrobbler_federation_signatures_total{direction,scheme,result}`, where result is `ok` or a `SignatureError` reason
  - `scrobbler_federation_deliveries_total{result}`: `delivered`, `retry`, `abandoned`, `gone`
  - `scrobbler_federation_delivery_seconds` (a histogram)
  - `scrobbler_federation_queue{queue,status}`, a gauge set by the worker for the inbox and delivery queues
  - `scrobbler_federation_followers{state}`, a gauge set by the worker
- **Federation dashboard** gains:
  - inbox activity by type and result
  - signature failures by reason
  - delivery outcomes and latency
  - queue depths
  - follower totals
- **Alerts**:
  - `FederationDeliveryBacklog`: more than 100 pending deliveries for 30 minutes
  - `FederationInboxBacklog`: queued inbox items for 15 minutes
  - `FederationSignatureFailures`: more than 20% of incoming signatures failing for 15 minutes

## 9. Tests

- **Unit tests**:
  - signatures (§1)
  - the SSRF guard: private and reserved IPs, `http`, redirects to private addresses, the oversized-body cap and timeouts
  - remote-actor validation: ID mismatch, key owner mismatch, a missing inbox
  - each inbox handler
  - the retry schedule and 4xx/410 handling
  - addressing
- **In-process federation**: two federation-enabled apps with different `.test` domains serving each other over real HTTP in the test process, with insecure testing on. Scripted remote actors cover the full path: follow → signature check → Accept delivered and verified by the "remote" → Undo. Also covered:
  - manual approval
  - decline, remove and block
  - repeat Follow
  - bad signatures rejected with the right reason
  - a blocked domain
  - turning sharing off
- **A real peer in CI**: a new `federation-interop` job runs Scrobbler (API, worker and UI) and a GoToSocial container on one Docker network. The hosts are `scrobble.test` and `gts.test`, over HTTP, with each side's test-only settings. A script:
  1. creates a GoToSocial user with its admin CLI,
  2. gets a user token by driving GoToSocial's sign-in and OAuth screens with plain HTTP (this part gets a short spike first, since GoToSocial has no password grant),
  3. follows `@alice@scrobble.test`, and checks it's accepted,
  4. unfollows, and checks it's gone,
  5. with manual approval on, checks the request waits until approved through Scrobbler's API.

  The job posts to ntfy like every other job. It doesn't run on pull requests from forks.
- **Manual pass before merging**: follow and unfollow from a real Mastodon account through a tunnel.

## 10. Docs

- Update the README and the federation README: following now works, the new settings, and the operator domain blocks.
- Regenerate the OpenAPI export.
- Mark this plan done.

## Order of work (a commit per step once its tests pass)

1. Signatures, pure, with test vectors
2. `net.py` and the remote-actor cache
3. Tables and migration
4. Inbox intake and processing, with handlers
5. Outgoing activities and the delivery task
6. Follower management API and UI; the actor reflects the approval setting; turning sharing off
7. Metrics, dashboard and alerts
8. In-process federation tests, and the GoToSocial CI job
9. Docs

## Notes

- **Licences**:
  - `httpx` is BSD-3-Clause and `cryptography` is Apache-2.0/BSD.
  - GoToSocial is AGPL, but it runs only as a separate container in CI to test against. Nothing of it is linked into, or shipped with, Scrobbler, so it has no effect on Scrobbler's licence.
- **Scale**: this is the biggest phase, and signatures plus safe outbound HTTP are where the risk lies. They come first and have the most thorough tests.
