# User guide

What Scrobbler does once you're signed in: connecting a player, importing your old
history, and sharing your listening on the fediverse. If you're setting up or running an
instance rather than using one, see the [admin guide](admin-guide.md) instead.

## Getting started

Register in the web UI, then go to **Apps** and create one app per player or scrobbler
client you use — each gets its own API key and shared secret, and can be revoked on its
own from the **Sessions** page without affecting the others. If a friend runs your
instance, ask them for its address; that's the only thing you need from them to get
going.

## Connecting a player

In the player's scrobbling settings, choose a custom or self-hosted Last.fm server:

- **API URL**: `https://your-host/2.0/`
- **API key and shared secret**: from an app on the **Apps** page
- **Username and password**: your Scrobbler account

Two sign-in flows are supported, and most players use one or the other automatically:

- **Mobile**: you enter your username and password directly in the player.
- **Desktop**: the player opens a browser page for you to approve, then signs itself in
  — no password ever passed to the player.

A sign-in method that hashes your password before sending it (sometimes offered as a
"stored password" or "password hash" option, e.g. pylast's `password_hash`) isn't
supported — use the plain username/password or desktop-approval flow instead.

Supported methods, if your player lets you check: `auth.getMobileSession`,
`auth.getToken`, `auth.getSession`, `track.scrobble` (up to 50 per request),
`track.updateNowPlaying`, `user.getInfo`, `user.getRecentTracks`, `user.getTopArtists`,
`user.getTopAlbums`, `user.getTopTracks`. `/api/docs` on your instance has the full
parameters and example responses for each, if you're curious or debugging a client.

A play is silently skipped (not rejected as an error) if the artist or track name is
empty, or its timestamp is more than 14 days old or more than 5 minutes in the future.
Sending the same play twice — a common side effect of a player retrying after a network
blip — is accepted both times but only stored once, so retries are always safe.

**A legacy scrobbler that speaks the old 1.2 "handshake" protocol (rather than 2.0) won't
work here** — this is a client limitation, not a setting to fix. VLC's built-in
scrobbler is one example; look for a plugin or a different player with 2.0 support.

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
- **Last.fm directly**: enter a Last.fm username and the import runs against that
  account's public history. Only appears if your instance's admin has set this up.

Imports run in the background — the page shows progress and you can cancel one in
flight. Unlike live scrobbles, imports keep plays of any age (still skipping rows with no
artist or track, or plays dated in the future). Plays already in your history are
skipped, so importing the same export again, or importing overlapping exports, is safe.

## Sharing on the fediverse

Only available if your instance's admin has turned this on — ask them, or check whether
**Sharing** appears in the navigation. Turn it on there, and you're findable as
`@name@<your instance's host>` from Mastodon and other fediverse apps.

- Follows are accepted automatically, or wait for your approval if you turn that on
  under **Followers and discovery**.
- **Followers** lists requests, followers and blocked accounts, with approve, decline,
  remove, block and unblock.

Once someone follows you, **Sharing**'s **What to post** section controls what they see:

- **Weekly summary**: your play count and top artist(s), posted once a week (Monday
  09:00 in your own time zone, for the week that just ended). A live preview shows what
  it would say so far.
- **Milestones**: total scrobbles, an artist's plays, and a new entry into your all-time
  top 10 — each posted once, the first time you reach it. History already on file when
  you turn sharing on, or added by an import, is never posted about retroactively.

Both are on by default and can be switched off independently. Posts use the same
visibility as your profile (followers only by default, or unlisted/public); **Posts**
lists your own posts, each with a delete button.

**Now playing** is a separate, three-way choice:

- **Off** (default): nothing shown or posted.
- **Show on my profile**: a "Now playing: *Track* by Artist" field on your profile,
  refreshed at most every 5 minutes while something's playing and cleared a few minutes
  after it stops.
- **Show on my profile and post**: the profile field, plus a post for each track played
  30 seconds or more, at most one every 30 minutes, with the previous now-playing post
  deleted once the new one is confirmed published.

**Your handle can't change once anyone follows you** — if you get to choose the
instance's domain yourself, or you're choosing which instance to join, bear that in mind
before you start sharing.

## Troubleshooting

**I turned Sharing on but can't see it in the menu.** Try a hard refresh
(Ctrl/Cmd+Shift+R) or a private/incognito window. The menu remembers whether sharing is
available for the length of a browser tab, so it can lag behind a setting your admin just
changed.

**My profile 404s on a big instance like mastodon.social right after I start sharing.**
Some servers cache a "not found" for a little while after they first look you up. Give it
an hour, or try searching from a smaller instance to confirm your handle really works.

**A change I made under Sharing doesn't seem to have taken effect.** Make sure you
clicked **Save sharing settings** at the bottom of the page — ticking a box alone doesn't
save it.