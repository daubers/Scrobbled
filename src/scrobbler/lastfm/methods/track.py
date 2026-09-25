from scrobbler.lastfm import errors
from scrobbler.lastfm.errors import LastFMError
from scrobbler.lastfm.registry import Call, Param, lastfm_method
from scrobbler.lastfm.responses import El
from scrobbler.services import scrobbles
from scrobbler.services.scrobbles import TrackInput


def _corrected(tag: str, value) -> El:
    # This server never auto-corrects metadata, so corrected is always 0.
    return El.text_el(tag, value, corrected=0)


def _track_fields(item: TrackInput, ignored_code: int) -> list[El]:
    return [
        _corrected("track", item.track),
        _corrected("artist", item.artist),
        _corrected("album", item.album),
        _corrected("albumArtist", item.album_artist),
        *([El.text_el("timestamp", item.timestamp)] if item.timestamp is not None else []),
        El.text_el("ignoredMessage", scrobbles.IGNORED_MESSAGES[ignored_code], code=ignored_code),
    ]


_TRACK_PARAMS = (
    Param("artist[i]", "Artist name", required=True, example="Radiohead"),
    Param("track[i]", "Track title", required=True, example="Reckoner"),
    Param("timestamp[i]", "Unix time the track started playing", required=True),
    Param("album[i]", "Album title"),
    Param("albumArtist[i]", "Album artist, if different from the track artist"),
    Param("duration[i]", "Track length in seconds"),
    Param("trackNumber[i]", "Position on the album"),
    Param("mbid[i]", "MusicBrainz track id"),
)


@lastfm_method(
    "track.scrobble",
    summary="Scrobble up to 50 tracks",
    signed=True,
    session=True,
    params=_TRACK_PARAMS,
    notes=(
        "Tracks are array parameters indexed from 0 (`artist[0]`, `track[0]`, ...); a "
        "single un-indexed track is also accepted. Tracks are ignored (not an error) when "
        "the artist or track is empty (codes 1/2), the timestamp is over 14 days old (3) "
        "or more than 5 minutes in the future (4). Re-sending an already stored scrobble "
        "is accepted, so retries are safe."
    ),
    example_xml=(
        '<lfm status="ok"><scrobbles accepted="1" ignored="0"><scrobble>'
        '<track corrected="0">Reckoner</track><artist corrected="0">Radiohead</artist>'
        '<album corrected="0">In Rainbows</album><albumArtist corrected="0"></albumArtist>'
        '<timestamp>1700000000</timestamp><ignoredMessage code="0"></ignoredMessage>'
        "</scrobble></scrobbles></lfm>"
    ),
)
def scrobble(call: Call) -> El:
    try:
        items = scrobbles.parse_batch(call.params)
    except scrobbles.BatchError as err:
        raise LastFMError(errors.INVALID_PARAMETERS, str(err)) from err
    results = scrobbles.submit_scrobbles(call.user, call.api_app, items)
    ignored = sum(r.status == "ignored" for r in results)
    return El.of(
        "scrobbles",
        *[El.of("scrobble", *_track_fields(r.item, r.ignored_code)) for r in results],
        accepted=len(results) - ignored,
        ignored=ignored,
    )


@lastfm_method(
    "track.updateNowPlaying",
    summary="Set the track the user is listening to now",
    signed=True,
    session=True,
    params=(
        Param("artist", "Artist name", required=True, example="Radiohead"),
        Param("track", "Track title", required=True, example="Reckoner"),
        Param("album", "Album title"),
        Param("albumArtist", "Album artist"),
        Param("duration", "Track length in seconds; now-playing expires after it"),
        Param("trackNumber", "Position on the album"),
        Param("mbid", "MusicBrainz track id"),
    ),
    notes="Now playing expires after the track's duration (10 minutes if not given).",
    example_xml=(
        '<lfm status="ok"><nowplaying><track corrected="0">Reckoner</track>'
        '<artist corrected="0">Radiohead</artist><album corrected="0"></album>'
        '<albumArtist corrected="0"></albumArtist><ignoredMessage code="0"></ignoredMessage>'
        "</nowplaying></lfm>"
    ),
)
def update_now_playing(call: Call) -> El:
    item = scrobbles.track_input(call.params)
    code = scrobbles.update_now_playing(call.user, item)
    return El.of("nowplaying", *_track_fields(item, code))
