from flask import g
from flask_smorest import Blueprint

from scrobbler.api.decorators import authenticated
from scrobbler.schemas import (
    CountsArgsSchema,
    CountSchema,
    NowPlayingSchema,
    PageArgsSchema,
    RecentPageSchema,
    SummarySchema,
    TopArgsSchema,
    TopPageSchema,
)
from scrobbler.services import scrobbles, stats

blp = Blueprint("Stats", __name__, description="Your listening history and statistics")

TOP_KINDS = {
    "artists": stats.top_artists,
    "albums": stats.top_albums,
    "tracks": stats.top_tracks,
}


def _page_body(page: stats.Page) -> dict:
    return {
        "items": page.items,
        "page": page.page,
        "per_page": page.per_page,
        "total": page.total,
        "total_pages": page.total_pages,
    }


@blp.route("/now-playing", methods=["GET"])
@authenticated(blp)
@blp.response(200, NowPlayingSchema)
def now_playing():
    """What you're listening to now"""
    return {"now_playing": scrobbles.get_now_playing(g.user)}


@blp.route("/recent", methods=["GET"])
@authenticated(blp)
@blp.arguments(PageArgsSchema, location="query")
@blp.response(200, RecentPageSchema)
def recent(args):
    """Recent scrobbles, newest first"""
    return _page_body(stats.recent(g.user, page=args["page"], per_page=args["limit"]))


def _top(kind):
    def view(args):
        page = TOP_KINDS[kind](g.user, args["period"], args["page"], args["limit"])
        return {**_page_body(page), "period": args["period"]}

    view.__name__ = f"top_{kind}"
    view.__doc__ = f"Top {kind} over a period"
    return view


for _kind in TOP_KINDS:
    blp.route(f"/top/{_kind}", methods=["GET"])(
        authenticated(blp)(
            blp.arguments(TopArgsSchema, location="query")(
                blp.response(200, TopPageSchema)(_top(_kind))
            )
        )
    )


@blp.route("/counts", methods=["GET"])
@authenticated(blp)
@blp.arguments(CountsArgsSchema, location="query")
@blp.response(200, CountSchema(many=True))
def counts(args):
    """Scrobbles per day, week or month

    Day buckets include days with no scrobbles, so the series is continuous.
    """
    return stats.listen_counts(g.user, args["period"], args["bucket"])


@blp.route("/summary", methods=["GET"])
@authenticated(blp)
@blp.response(200, SummarySchema)
def summary():
    """Totals: scrobbles, distinct artists and tracks"""
    return stats.summary(g.user)
