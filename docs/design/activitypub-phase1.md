# ActivityPub phase 1: discoverable profile

This implements phase 1 of [activitypub.md](activitypub.md). Handles and actor URLs are on the **UI's domain**, and posts are **followers only by default** (a per-user setting).

**Outcome:** once an operator enables federation, a user can switch on sharing, and searching `@alice@<UI host>` from Mastodon finds their profile. Following needs phase 2 (the inbox and deliveries). Until then the actor advertises `manuallyApprovesFollowers: true`, so a follow shows as a pending request rather than failing silently. With federation off, nothing changes.

Everything below lives in `scrobbler.federation` unless it says otherwise, and the boundary tests keep holding.

## 1. Data and keys

- **New dependency**: `cryptography` (Apache-2.0 or BSD).
- **Migration on the `federation` branch** (`flask db migrate --head federation@head`), adding two tables:
  - `federation_settings`, keyed by user:
    - `enabled` (false)
    - `visibility`: `followers` (the default), `unlisted` or `public`
    - `manually_approves_followers` (false)
    - `discoverable` and `indexable` (both false)
    - `display_name`, `bio`
    - `created_at` and `updated_at`
  - `federation_keys`:
    - a user ID, which is null for the instance actor
    - the public key PEM
    - the private key PEM encrypted with Fernet
    - `created_at`
- `keys.py`:
  - An RSA-2048 key pair is created the first time a user enables sharing. The instance actor's pair is created on first use.
  - Private keys are encrypted with a key derived by HKDF-SHA256 from **`FEDERATION_KEY_SECRET`**. That setting becomes required when federation is on, and is validated at start-up.
  - Keys are never regenerated, because followers pin them.

## 2. Protocol (pure: `federation/protocol/`)

- `media.py`: the ActivityPub content types (`application/activity+json`, and `application/ld+json` with the ActivityStreams profile), plus a check for whether a request's `Accept` header wants ActivityPub.
- `vocab.py`, which builds dicts with `@context`:
  - `person()`: `id`, `preferredUsername`, `name`, `summary`, `url`, `inbox`, `outbox`, `followers`, `following`, `endpoints.sharedInbox`, `publicKey`, `manuallyApprovesFollowers`, `discoverable`, `indexable`
  - `application()` for the instance actor
  - `ordered_collection()`
- `webfinger.py`: parses `acct:user@domain` resources (case-insensitive, trimmed, rejecting other domains) and builds the JRD: `subject`, `aliases`, a `self` link and a `profile-page` link.
- `nodeinfo.py`: the discovery document and the NodeInfo 2.1 document.
- `hostmeta.py`: the XRD for `/.well-known/host-meta`, which points at WebFinger.

These are unit-tested against the documented Mastodon shapes and examples from the WebFinger RFC.

## 3. ActivityPub endpoints (`federation/web.py`)

| Path | Response |
|---|---|
| `GET /.well-known/webfinger?resource=acct:u@DOMAIN` | JRD for users who opted in. **404 for unknown or non-opted users, with an identical body.** 400 for a malformed or foreign resource. |
| `GET /.well-known/host-meta` | XRD pointing at WebFinger |
| `GET /.well-known/nodeinfo`, `GET /nodeinfo/2.1` | Software `scrobbler`, the version, `activitypub`, and the count of users who opted in |
| `GET /users/<u>` | Actor JSON when the request asks for ActivityPub; otherwise a **302 to the UI profile page**; 404 if not opted in |
| `GET /users/<u>/outbox`, `/followers`, `/following` | Empty collections with `totalItems` (filled in phases 2 and 3) |
| `POST /users/<u>/inbox`, `POST /inbox` | 202 and discard, for now (phase 2 handles it) |
| `GET /actor` | The instance actor (an `Application`), used later for signed fetches |

- All IDs and URLs are built from `FEDERATION_BASE_URL`, never from the request, so a proxy misconfiguration can't produce wrong IDs.
- Responses are cacheable: `Cache-Control: max-age=300` on the actor and WebFinger.
- CORS stays unchanged: remote servers don't send CORS requests.

## 4. UI-facing API (`federation/api.py`, `/api/v1/federation/*`, documented in OpenAPI)

- **`GET` and `PUT /settings`** (bearer auth):
  - Returns `enabled`, `visibility`, `manually_approves_followers`, `discoverable`, `indexable`, `display_name`, `bio`, the read-only `handle` and `profile_url`, and `followers` (0 until phase 2).
  - A 404 means the server doesn't federate, and the UI hides the Sharing page.
- **`GET /profiles/<username>`** is **public** (no auth). It returns the profile shown on the UI profile page, for opted-in users only, and 404 otherwise.
- **Registration**: federation registers its smorest blueprint on the core's `Api` object. This means allowing `scrobbler.api` itself (the `Api` instance) in the boundary test's list of permitted imports, alongside `scrobbler.api.decorators`.

## 5. Web UI (in `frontend/`, not the Python module)

- **`sharing.html`**, shown in the nav only when `/federation/settings` exists:
  - A **Share my listening on the fediverse** switch, showing the handle and the warning that it's permanent.
  - A **Who can see posts** choice: *Followers only* (default), *Unlisted* or *Public*. Its help text says to turn on manual approval for real privacy.
  - **Approve followers manually**, **List my profile in directories** (`discoverable`) and **Allow search engines** (`indexable`).
  - Display name and bio.
- **`profile.html?u=<username>`**: a public page with no login needed, where browsers opening an actor URL land.
  - It shows the display name, handle, bio and how to follow ("search for `@alice@host` in your fediverse app"), with a copy button.
  - It uses the existing styles, including the dot-matrix display for the handle.

## 6. Proxying from the UI domain (`deploy/nginx/default.conf.template`)

- Forward these paths to `${API_INTERNAL_URL}` (default `http://api:8000`, set in the UI image):
  - `/.well-known/webfinger`, `/.well-known/nodeinfo`, `/.well-known/host-meta`
  - `/nodeinfo/`, `/users/`, `/inbox`, `/actor`
- Pass `Host`, `X-Forwarded-For` and `X-Forwarded-Proto` through. The proto comes from an outer TLS proxy when there is one.
- The API trusts the UI proxy via `FORWARDED_ALLOW_IPS`.
- No changes to the UI's CSP: these are server-to-server requests.
- Compose adds the federation settings to `api` and `worker`, and `API_INTERNAL_URL` to `ui`.

## 7. Metrics and dashboard

- `federation/metrics.py` holds federation's own metrics:
  - `scrobbler_federation_lookups_total{kind,result}`, where kind is `webfinger`, `actor`, `nodeinfo` or `profile`, and result is `found` or `not_found`
  - `scrobbler_federation_sharing_changes_total{change}`, for `enabled` and `disabled`
- Request rate and latency come free from the existing HTTP metrics (endpoint label `federation.*`).
- A new **Federation** Grafana dashboard: lookups by kind and result, sharing changes, and federation endpoint latency. Phases 2 and 3 add delivery and inbox panels.

## 8. Tests

- **Protocol**: WebFinger parsing, including other domains, mixed case and garbage; JRD, actor and NodeInfo shapes; `Accept` negotiation.
- **Keys**: encryption round trip, the wrong secret failing, and keys staying stable across enable and disable.
- **Endpoints**, against a second session app with federation on, sharing the same database:
  - opted-in users vs 404
  - identical 404s for unknown and non-opted users
  - the browser redirect to the profile page
  - IDs built from config
  - the 202 inbox stub
- **Settings API**: defaults (followers only, not discoverable), validation, and the public profile endpoint.
- **Still holding**: boundary tests, federation off meaning nothing is registered, and the OpenAPI drift test.
- **Manual pass**: Mastodon needs a public HTTPS domain. A tunnel (for example `cloudflared`) to a local stack, then searching the handle from a Mastodon account, confirms the profile and key resolve. GoToSocial in CI comes with phase 2.

## 9. Docs

- The federation README gains the proxy paths and `FEDERATION_KEY_SECRET`.
- The main README gets a "Sharing on the fediverse" section.
- The OpenAPI export is regenerated.

Commit after each numbered section once its tests pass.
