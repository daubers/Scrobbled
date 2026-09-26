# Using Scrobbler

What the service does, once it's running. For getting an instance running, see
[deploy.md](deploy.md); for working on the code, see [setup.md](setup.md).

## Account and apps

Register in the web UI, then go to **Apps** and create one app per player or scrobbler
client you use — each gets its own API key and shared secret, and can be revoked on its
own from the **Sessions** page without affecting the others.

## Connecting a player

In the player's scrobbling settings, choose a custom or self-hosted Last.fm server:

- **API URL**: `https://your-host/2.0/`
- **API key and shared secret**: from an app on the **Apps** page
- **Username and password**: your Scrobbler account

Two sign-in flows are supported:

- **Mobile**: `auth.getMobileSession` with a username and password, sent as a POST body
  over HTTPS.
- **Desktop**: `auth.getToken`, then the user approves at `/api/auth/` (redirects to the
  web UI), then `auth.getSession`.

The legacy `authToken = md5(username + md5(password))` form isn't supported — checking
it would mean storing unsalted password hashes. pylast's `password_hash` option uses
that form; use `SessionKeyGenerator` (the desktop flow) instead.

Supported methods: `auth.getMobileSession`, `auth.getToken`, `auth.getSession`,
`track.scrobble` (up to 50 per request), `track.updateNowPlaying`, `user.getInfo`,
`user.getRecentTracks`, `user.getTopArtists`, `user.getTopAlbums`, `user.getTopTracks`.
`/api/docs` has the full parameters, rules and example responses for each.

A scrobble is ignored (not rejected) if the artist or track is empty, or the timestamp
is more than 14 days old or more than 5 minutes in the future. Resending the same
scrobble is accepted and stored once, so client retries are safe.

## Importing history

Open **Import** in the web UI:

- **An export file**:
  - CSV from [lastfm-to-csv](https://benjaminbenben.com/lastfm-to-csv/), no header row,
    artist/album/track/date columns.
  - Any CSV or TSV with a header row naming artist, track and a date or Unix-time
    column. Comma, semicolon and tab delimiters all work.
  - A JSON export of `user.getRecentTracks` pages (such as lastfm.ghan.nl's), a single
    `recenttracks` response, or a flat list of tracks.
  - Files can be gzipped.
- **Last.fm directly**: enter a Last.fm username and the worker pages through that
  account's public history. Only appears when the server has `LASTFM_API_KEY` set.

Imports run in the background; the page shows progress and can cancel one in flight.
Unlike live scrobbles, imports keep plays of any age (still skipping rows with no artist
or track, or plays dated in the future). Plays already in your history are skipped, so
importing the same export again is safe.

## Sharing on the fediverse

Only available when the server has federation enabled (ask whoever runs it, or see
[deploy.md](deploy.md#fediverse-sharing-optional) if that's you). Turn it on under
**Sharing**, and you're findable as `@name@<the server's host>` from Mastodon and other
fediverse apps:

- Follows are accepted automatically, or wait for your approval if you turn that on.
- **Followers** lists requests, followers and blocked accounts, with approve, decline,
  remove, block and unblock.

Once someone follows, **Sharing**'s **What to post** section controls what they see:

- **Weekly summary**: play count and top artist(s), posted once a week (Monday 09:00 in
  your own time zone, for the week that just ended). A live preview shows what it would
  say so far.
- **Milestones**: total scrobbles, an artist's plays, and a new entry into your all-time
  top 10 — each posted once, the first time it's reached. History already on file when
  you turn sharing on, or added by an import, is never posted about retroactively.

Both are on by default and can be switched off independently. Posts use the same
visibility as your profile (followers only by default, or unlisted/public); **Posts**
lists your own posts with delete (sends a `Delete` to whoever received it).

**Now playing** is a separate, three-way choice:

- **Off** (default): nothing shown or posted.
- **Show on my profile**: a "Now playing: *Track* by Artist" field, refreshed at most
  every 5 minutes while something's playing and cleared a few minutes after it stops.
- **Show on my profile and post**: the profile field, plus a post for each track played
  30 seconds or more, at most one every 30 minutes, with the previous now-playing post
  deleted once the new one is confirmed published.

Handles and actor URLs **can't change once anyone follows you**, so if you're the one
who gets to choose the server's domain, choose it carefully.
