import math

from flask import Response, current_app, g, request
from flask_smorest import Blueprint, abort

from scrobbler import metrics
from scrobbler.api.decorators import authenticated
from scrobbler.schemas import (
    AlbumArtArgsSchema,
    AlbumArtCorrectionSchema,
    AlbumArtFailuresPageSchema,
    AlbumArtOverrideSchema,
    AlbumArtUploadSchema,
    ErrorSchema,
    PageArgsSchema,
)
from scrobbler.services import art

blp = Blueprint(
    "Art",
    __name__,
    description=(
        "Album art, resolved from MusicBrainz / Cover Art Archive by a background worker. "
        "You can also upload your own art, or correct a failed lookup; those only change "
        "what you see."
    ),
)


@blp.route("/album", methods=["GET"])
@authenticated(blp)
@blp.arguments(AlbumArtArgsSchema, location="query")
@blp.response(
    200, content_type="image/jpeg", description="The album's cover art (JPEG, PNG or WebP)"
)
@blp.alt_response(404, schema=ErrorSchema, description="No cover art available yet")
def album_art(args):
    """An album's cover art

    Your own uploaded or corrected art comes first. Otherwise it's served from a local
    cache that a background worker task keeps filled; this endpoint never makes a live
    MusicBrainz/Cover Art Archive call, so it can't be slowed down by one. An album seen
    for the first time returns 404 until the worker resolves it, typically within a few
    seconds - cache this response client-side and retry later.
    """
    own = art.get_override(g.user.id, args["artist"], args["album"])
    if own is not None and own.status == "found":
        data = art.read_override_file(own)
        if data is not None:
            metrics.art_requests_total.labels("override").inc()
            response = Response(data, mimetype=own.content_type or "image/jpeg")
            response.headers["Cache-Control"] = "private, no-cache"  # revalidate: it can change
            response.add_etag()
            return response.make_conditional(request)

    row = art.get_cached(args["artist"], args["album"])
    if row is None:
        art.want(args["artist"], args["album"])
        metrics.art_requests_total.labels("queued").inc()
        abort(404, code="no_album_art", message="Not resolved yet")
    image = art.read_file(row.id) if row.status == "found" else None
    if image is None:
        if row.status == "found":
            art.requeue(row)  # resolved before, but the file is missing - ask again
            result = "file_missing"
        else:
            result = "pending" if row.status == "pending" else "unavailable"
        metrics.art_requests_total.labels(result).inc()
        abort(404, code="no_album_art", message="No cover art found for this album")
    metrics.art_requests_total.labels("served").inc()
    response = Response(image, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=604800"  # 7 days; art rarely changes
    return response


@blp.route("/album", methods=["POST"])
@authenticated(blp)
@blp.arguments(AlbumArtUploadSchema, location="files")
@blp.arguments(AlbumArtArgsSchema, location="query")
@blp.response(201, AlbumArtOverrideSchema)
@blp.alt_response(400, schema=ErrorSchema, description="Empty file")
@blp.alt_response(413, schema=ErrorSchema, description="Image too large")
@blp.alt_response(415, schema=ErrorSchema, description="Not a JPEG, PNG or WebP image")
def upload_art(files, args):
    """Upload your own art for an album

    Replaces any earlier upload or correction of yours for this album. Only you see it.
    """
    limit = current_app.config["ART_UPLOAD_MAX_BYTES"]
    data = files["file"].read(limit + 1)
    if not data:
        metrics.art_uploads_total.labels("empty").inc()
        abort(400, code="empty_file", message="That file is empty.")
    if len(data) > limit:
        metrics.art_uploads_total.labels("too_large").inc()
        abort(413, code="file_too_large", message="That image is larger than this server accepts.")
    try:
        content_type = art.sniff_image_type(data)
    except art.UnsupportedImage:
        metrics.art_uploads_total.labels("bad_type").inc()
        abort(415, code="unsupported_image", message="Upload a JPEG, PNG or WebP image.")
    art.save_upload(g.user.id, args["artist"], args["album"], data, content_type)
    metrics.art_uploads_total.labels("ok").inc()
    metrics.art_upload_bytes.observe(len(data))
    return art.get_override(g.user.id, args["artist"], args["album"])


@blp.route("/album", methods=["DELETE"])
@authenticated(blp)
@blp.arguments(AlbumArtArgsSchema, location="query")
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="You have no art of your own for this album")
def delete_override(args):
    """Remove your own art for an album

    The album goes back to the shared lookup result, if there is one.
    """
    if not art.delete_override(g.user.id, args["artist"], args["album"]):
        abort(404, code="not_found", message="You have no art of your own for this album")


@blp.route("/album/correction", methods=["PUT"])
@authenticated(blp)
@blp.arguments(AlbumArtCorrectionSchema)
@blp.response(202, AlbumArtOverrideSchema)
def correct_art(body):
    """Correct a failed lookup

    Queues a new lookup with different search text, or straight from a MusicBrainz
    release-group or release ID. The background worker resolves it; poll the failures list, or just
    fetch the art later. Replaces any earlier upload or correction of yours.
    """
    mbid = body.get("release_group_mbid")
    release_mbid = body.get("release_mbid")
    art.request_correction(
        g.user.id,
        body["artist"],
        body["album"],
        body.get("search_artist"),
        body.get("search_album"),
        str(mbid) if mbid else None,
        str(release_mbid) if release_mbid else None,
    )
    return art.get_override(g.user.id, body["artist"], body["album"])


@blp.route("/album/retry", methods=["POST"])
@authenticated(blp)
@blp.arguments(AlbumArtArgsSchema, location="query")
@blp.response(202)
@blp.alt_response(409, schema=ErrorSchema, description="Nothing to retry")
def retry_art(args):
    """Look an album up again

    Re-queues the shared lookup for an album whose search found nothing or failed.
    """
    if not art.retry(args["artist"], args["album"]):
        abort(409, code="nothing_to_retry", message="This album's art is found or already queued")


@blp.route("/failures", methods=["GET"])
@authenticated(blp)
@blp.arguments(PageArgsSchema, location="query")
@blp.response(200, AlbumArtFailuresPageSchema)
def failures(args):
    """Albums of yours without art

    Albums you've scrobbled whose lookup found nothing or failed, plus ones where your
    own correction didn't work, most-scrobbled first. Albums you've fixed drop off.
    """
    items, total = art.list_failures(g.user.id, args["page"], args["limit"])
    return {
        "items": items,
        "page": args["page"],
        "per_page": args["limit"],
        "total": total,
        "total_pages": math.ceil(total / args["limit"]),
    }
