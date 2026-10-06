from flask import Response
from flask_smorest import Blueprint, abort

from scrobbler.api.decorators import authenticated
from scrobbler.schemas import AlbumArtArgsSchema, ErrorSchema
from scrobbler.services import art

blp = Blueprint(
    "Art", __name__, description="Album art, proxied from MusicBrainz / Cover Art Archive"
)


@blp.route("/album", methods=["GET"])
@authenticated(blp)
@blp.arguments(AlbumArtArgsSchema, location="query")
@blp.response(200, content_type="image/jpeg", description="The album's cover art")
@blp.alt_response(404, schema=ErrorSchema, description="No cover art found for this album")
@blp.alt_response(
    502, schema=ErrorSchema, description="MusicBrainz or Cover Art Archive is unreachable"
)
def album_art(args):
    """An album's cover art

    Resolved via MusicBrainz + Cover Art Archive and cached; bytes are proxied on every
    call, so cache this response client-side.
    """
    result = art.lookup(args["artist"], args["album"])
    if result.status == "not_found":
        abort(404, code="no_album_art", message="No cover art found for this album")
    if result.status == "error":
        abort(
            502,
            code="art_upstream_unavailable",
            message="Couldn't reach MusicBrainz or Cover Art Archive",
        )
    try:
        image = art.fetch_image(result.image_url)
    except art.ArtFetchFailed:
        abort(502, code="art_upstream_unavailable", message="Couldn't reach Cover Art Archive")
    response = Response(image.body, mimetype=image.content_type)
    response.headers["Cache-Control"] = "private, max-age=604800"  # 7 days; art rarely changes
    return response
