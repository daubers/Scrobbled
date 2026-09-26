"""Actor document building (actors.py): shared by the live GET /users/<u> route and the
now-playing Update(Person) push."""

from scrobbler.federation import actors, sharing
from scrobbler.federation.protocol import vocab
from scrobbler.services.scrobbles import TrackInput, update_now_playing

URLS = vocab.actor_urls(
    "https://scrobble.test", "alice", "https://scrobble.test/profile.html?u=alice"
)


def test_now_playing_field_is_empty_when_nothing_is_playing(fed_ctx, user):
    assert actors.now_playing_field(user) == []


def test_now_playing_field_shows_the_current_track(fed_ctx, user):
    update_now_playing(user, TrackInput(artist="Radiohead", track="Reckoner"))
    assert actors.now_playing_field(user) == [("Now playing", "Reckoner by Radiohead")]


def test_build_document_includes_the_now_playing_field(fed_ctx, user):
    sharing.update(
        user.id,
        {"enabled": True, "display_name": "Alice", "bio": "Hi.", "now_playing_mode": "profile"},
    )
    update_now_playing(user, TrackInput(artist="Radiohead", track="Reckoner"))
    settings = sharing.settings_for(user.id)
    doc = actors.build_document(URLS, user, settings)
    assert doc["attachment"] == [
        {"type": "PropertyValue", "name": "Now playing", "value": "Reckoner by Radiohead"}
    ]


def test_build_document_has_no_now_playing_field_when_idle(fed_ctx, user):
    sharing.update(user.id, {"enabled": True, "now_playing_mode": "profile"})
    settings = sharing.settings_for(user.id)
    doc = actors.build_document(URLS, user, settings)
    assert doc["attachment"] == []


def test_build_document_never_shows_now_playing_when_the_mode_is_off(fed_ctx, user):
    """now_playing_mode defaults to off; core's now_playing table is populated for every
    user regardless, so a user who's never turned this on must never leak it."""
    sharing.update(user.id, {"enabled": True})
    update_now_playing(user, TrackInput(artist="Radiohead", track="Reckoner"))
    settings = sharing.settings_for(user.id)
    doc = actors.build_document(URLS, user, settings)
    assert doc["attachment"] == []
