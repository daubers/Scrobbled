"""Building a user's actor document: shared by the live `GET /users/<u>` route and the
now-playing `Update(Person)` push, so both render the same shape.

The now-playing field is always computed live from the core `now_playing` table, never
from federation's own throttled push state (`federation_now_playing`) - a fetch should
never lie, even if the last proactive Update was a few minutes ago.
"""

from scrobbler.federation import keys
from scrobbler.federation.protocol import vocab
from scrobbler.services.scrobbles import get_now_playing

NOW_PLAYING_FIELD = "Now playing"


def now_playing_field(user) -> list[tuple[str, str]]:
    """The actor's now-playing `PropertyValue` field. Empty when nothing's playing
    (`get_now_playing` already excludes expired tracks).

    Doesn't look at the user's now_playing_mode: core's now_playing table is populated
    for every user regardless of federation settings, so a caller must gate this itself
    (build_document does) rather than leak listening activity for users who never turned
    now playing on at all.
    """
    playing = get_now_playing(user)
    if playing is None:
        return []
    return [(NOW_PLAYING_FIELD, f"{playing.track} by {playing.artist}")]


def build_document(urls: vocab.ActorUrls, user, settings) -> dict:
    fields = now_playing_field(user) if settings.now_playing_mode != "off" else []
    return vocab.person(
        urls,
        username=user.username,
        name=settings.display_name or user.username,
        summary=settings.bio,
        public_key_pem=keys.key_for(user.id).public_key_pem,
        manually_approves_followers=settings.manually_approves_followers,
        discoverable=settings.discoverable,
        indexable=settings.indexable,
        published=settings.created_at.isoformat().replace("+00:00", "Z"),
        fields=fields,
    )
