from datetime import UTC, datetime

from scrobbler.lastfm import errors
from scrobbler.lastfm.errors import LastFMError
from scrobbler.lastfm.registry import Call, Param, lastfm_method
from scrobbler.lastfm.responses import El
from scrobbler.models import User
from scrobbler.services import accounts, scrobbles, stats

USER_PARAM = Param("user", "Username to fetch data for", required=True, example="alice")
PAGE_PARAMS = (
    Param("limit", "Results per page (default 50, max 1000)", example="50"),
    Param("page", "Page number (default 1)", example="1"),
)
PERIOD_PARAM = Param(
    "period",
    "overall | 7day | 1month | 3month | 6month | 12month (default overall)",
    example="7day",
)


def _user(call: Call) -> User:
    user = accounts.find_user(call.params["user"])
    if user is None:
        raise LastFMError(errors.INVALID_PARAMETERS, "User not found")
    return user


def _int_param(call: Call, name: str, default: int, maximum: int) -> int:
    raw = call.get(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        raise LastFMError(errors.INVALID_PARAMETERS, f"{name} must be an integer") from None
    if not 1 <= value <= maximum:
        raise LastFMError(errors.INVALID_PARAMETERS, f"{name} must be between 1 and {maximum}")
    return value


def _paging(call: Call, max_limit: int = 1000) -> tuple[int, int]:
    return _int_param(call, "page", 1, 1_000_000), _int_param(call, "limit", 50, max_limit)


def _period(call: Call) -> str:
    period = call.get("period", "overall")
    if period not in stats.PERIODS:
        raise LastFMError(errors.INVALID_PARAMETERS, "Invalid period")
    return period


def _page_attrs(user: User, page: stats.Page) -> dict:
    return {
        "user": user.username,
        "page": page.page,
        "perPage": page.per_page,
        "totalPages": page.total_pages,
        "total": page.total,
    }


def _artist_el(name: str, tag: str = "artist") -> El:
    return El.of(tag, El.text_el("name", name), El.text_el("mbid", ""), El.text_el("url", ""))


@lastfm_method(
    "user.getInfo",
    summary="A user's profile and play count",
    params=(USER_PARAM,),
    example_xml=(
        '<lfm status="ok"><user><name>alice</name><realname></realname><url></url>'
        "<country></country><playcount>1234</playcount><artist_count>210</artist_count>"
        "<track_count>800</track_count><subscriber>0</subscriber>"
        '<registered unixtime="1700000000">1700000000</registered></user></lfm>'
    ),
)
def get_info(call: Call) -> El:
    user = _user(call)
    totals = stats.summary(user)
    registered = int(user.created_at.timestamp())
    return El.of(
        "user",
        El.text_el("name", user.username),
        El.text_el("realname", ""),
        El.text_el("url", ""),
        El.text_el("country", ""),
        El.text_el("playcount", totals["scrobbles"]),
        El.text_el("artist_count", totals["artists"]),
        El.text_el("track_count", totals["tracks"]),
        El.text_el("subscriber", 0),
        El.text_el("registered", registered, unixtime=registered),
    )


@lastfm_method(
    "user.getRecentTracks",
    summary="Recently scrobbled tracks, newest first, led by the now-playing track",
    params=(
        USER_PARAM,
        Param("limit", "Results per page (default 50, max 200)", example="50"),
        Param("page", "Page number (default 1)", example="1"),
        Param("from", "Only scrobbles at or after this Unix time"),
        Param("to", "Only scrobbles at or before this Unix time"),
    ),
    example_xml=(
        '<lfm status="ok"><recenttracks user="alice" page="1" perPage="50" totalPages="1" '
        'total="1"><track nowplaying="true"><artist mbid="">Radiohead</artist><name>Nude</name>'
        '<mbid></mbid><album mbid="">In Rainbows</album><url></url></track><track>'
        '<artist mbid="">Radiohead</artist><name>Reckoner</name><mbid></mbid>'
        '<album mbid="">In Rainbows</album><url></url>'
        '<date uts="1700000000">14 Nov 2023, 22:13</date></track></recenttracks></lfm>'
    ),
)
def get_recent_tracks(call: Call):
    user = _user(call)
    page, limit = _paging(call, max_limit=200)
    start = _timestamp(call, "from")
    end = _timestamp(call, "to")
    result = stats.recent(user, page=page, per_page=limit, start=start, end=end)

    tracks = []
    now_playing = scrobbles.get_now_playing(user) if page == 1 and end is None else None
    if now_playing:
        tracks.append(
            El.of(
                "track",
                *_recent_fields(now_playing.artist, now_playing.track, now_playing.album, None),
                nowplaying="true",
            )
        )
    for row in result.items:
        tracks.append(El.of("track", *_recent_fields(row.artist, row.track, row.album, row)))
    return El.of("recenttracks", *tracks, **_page_attrs(user, result)), ("track",)


def _timestamp(call: Call, name: str) -> datetime | None:
    raw = call.get(name)
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(int(raw), UTC)
    except (ValueError, OverflowError, OSError):
        raise LastFMError(errors.INVALID_PARAMETERS, f"{name} must be a Unix time") from None


def _recent_fields(artist: str, track: str, album: str | None, scrobble) -> list[El]:
    fields = [
        El.text_el("artist", artist, mbid=""),
        El.text_el("name", track),
        El.text_el("mbid", scrobble.mbid if scrobble else ""),
        El.text_el("album", album, mbid=""),
        El.text_el("url", ""),
    ]
    if scrobble is not None:
        uts = int(scrobble.played_at.timestamp())
        label = scrobble.played_at.astimezone(UTC).strftime("%d %b %Y, %H:%M")
        fields.append(El.text_el("date", label, uts=uts))
    return fields


@lastfm_method(
    "user.getTopArtists",
    summary="Most played artists over a period",
    params=(USER_PARAM, PERIOD_PARAM, *PAGE_PARAMS),
    example_xml=(
        '<lfm status="ok"><topartists user="alice" page="1" perPage="50" totalPages="1" '
        'total="1"><artist rank="1"><name>Radiohead</name><playcount>42</playcount>'
        "<mbid></mbid><url></url></artist></topartists></lfm>"
    ),
)
def get_top_artists(call: Call):
    user, period, (page, limit) = _user(call), _period(call), _paging(call)
    result = stats.top_artists(user, period, page, limit)
    artists = [
        El.of(
            "artist",
            El.text_el("name", item.name),
            El.text_el("playcount", item.playcount),
            El.text_el("mbid", ""),
            El.text_el("url", ""),
            rank=item.rank,
        )
        for item in result.items
    ]
    attrs = {**_page_attrs(user, result), "period": period}
    return El.of("topartists", *artists, **attrs), ("artist",)


@lastfm_method(
    "user.getTopAlbums",
    summary="Most played albums over a period",
    params=(USER_PARAM, PERIOD_PARAM, *PAGE_PARAMS),
    example_xml=(
        '<lfm status="ok"><topalbums user="alice" page="1" perPage="50" totalPages="1" '
        'total="1"><album rank="1"><name>In Rainbows</name><playcount>20</playcount>'
        "<mbid></mbid><url></url><artist><name>Radiohead</name><mbid></mbid><url></url>"
        "</artist></album></topalbums></lfm>"
    ),
)
def get_top_albums(call: Call):
    user, period, (page, limit) = _user(call), _period(call), _paging(call)
    result = stats.top_albums(user, period, page, limit)
    albums = [
        El.of(
            "album",
            El.text_el("name", item.name),
            El.text_el("playcount", item.playcount),
            El.text_el("mbid", ""),
            El.text_el("url", ""),
            _artist_el(item.artist),
            rank=item.rank,
        )
        for item in result.items
    ]
    attrs = {**_page_attrs(user, result), "period": period}
    return El.of("topalbums", *albums, **attrs), ("album",)


@lastfm_method(
    "user.getTopTracks",
    summary="Most played tracks over a period",
    params=(USER_PARAM, PERIOD_PARAM, *PAGE_PARAMS),
    example_xml=(
        '<lfm status="ok"><toptracks user="alice" page="1" perPage="50" totalPages="1" '
        'total="1"><track rank="1"><name>Reckoner</name><duration>290</duration>'
        "<playcount>12</playcount><mbid></mbid><url></url><artist><name>Radiohead</name>"
        "<mbid></mbid><url></url></artist></track></toptracks></lfm>"
    ),
)
def get_top_tracks(call: Call):
    user, period, (page, limit) = _user(call), _period(call), _paging(call)
    result = stats.top_tracks(user, period, page, limit)
    tracks = [
        El.of(
            "track",
            El.text_el("name", item.name),
            El.text_el("duration", item.duration or 0),
            El.text_el("playcount", item.playcount),
            El.text_el("mbid", ""),
            El.text_el("url", ""),
            _artist_el(item.artist),
            rank=item.rank,
        )
        for item in result.items
    ]
    attrs = {**_page_attrs(user, result), "period": period}
    return El.of("toptracks", *tracks, **attrs), ("track",)
