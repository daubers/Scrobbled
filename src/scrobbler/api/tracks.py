from flask import g
from flask_smorest import Blueprint, abort

from scrobbler.api.decorators import authenticated
from scrobbler.schemas import (
    ErrorSchema,
    TrackDetailArgsSchema,
    TrackDetailSchema,
    TrackSearchArgsSchema,
    TrackSearchResultSchema,
)
from scrobbler.services import stats

blp = Blueprint("Tracks", __name__, description="Look up a specific song's history and metadata")


@blp.route("/search", methods=["GET"])
@authenticated(blp)
@blp.arguments(TrackSearchArgsSchema, location="query")
@blp.response(200, TrackSearchResultSchema(many=True))
def search(args):
    """Find a song by track or artist name"""
    return stats.track_search(g.user, args["q"], args["limit"])


@blp.route("", methods=["GET"])
@authenticated(blp)
@blp.arguments(TrackDetailArgsSchema, location="query")
@blp.response(200, TrackDetailSchema)
@blp.alt_response(404, schema=ErrorSchema, description="You've never scrobbled this song")
def detail(args):
    """A song's play history and recorded metadata"""
    metadata = stats.track_metadata(g.user, args["artist"], args["track"])
    if metadata is None:
        abort(404, code="not_found", message="You've never scrobbled this song")
    history = stats.recent(
        g.user,
        page=args["page"],
        per_page=args["limit"],
        artist=args["artist"],
        track=args["track"],
    )
    counts = stats.listen_counts(
        g.user, period="3month", bucket="day", artist=args["artist"], track=args["track"]
    )
    return {
        "artist": metadata.artist,
        "track": metadata.track,
        "metadata": metadata,
        "items": history.items,
        "counts": counts,
        "page": history.page,
        "per_page": history.per_page,
        "total": history.total,
        "total_pages": history.total_pages,
    }
