from flask import Response
from flask_smorest import Blueprint, abort

from scrobbler.api.decorators import authenticated
from scrobbler.schemas import AlbumArtArgsSchema, ErrorSchema
from scrobbler.services import art

blp = Blueprint(
    "Art",
    __name__,
    description="Album art, resolved from MusicBrainz / Cover Art Archive by a background worker",
)


@blp.route("/album", methods=["GET"])
@authenticated(blp)
@blp.arguments(AlbumArtArgsSchema, location="query")
@blp.response(200, content_type="image/jpeg", description="The album's cover art")
@blp.alt_response(404, schema=ErrorSchema, description="No cover art available yet")
def album_art(args):
    """An album's cover art

    Served from a local cache that a background worker task keeps filled; this endpoint
    never makes a live MusicBrainz/Cover Art Archive call, so it can't be slowed down by
    one. An album seen for the first time returns 404 until the worker resolves it,
    typically within a few seconds - cache this response client-side and retry later.
    """
    row = art.get_cached(args["artist"], args["album"])
    if row is None:
        art.want(args["artist"], args["album"])
        abort(404, code="no_album_art", message="Not resolved yet")
    image = art.read_file(row.id) if row.status == "found" else None
    if image is None:
        if row.status == "found":
            art.requeue(row)  # resolved before, but the file is missing - ask again
        abort(404, code="no_album_art", message="No cover art found for this album")
    response = Response(image, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=604800"  # 7 days; art rarely changes
    return response
