from flask import Response, current_app, g, request
from flask_smorest import Blueprint, abort

from scrobbler import metrics
from scrobbler.api.decorators import authenticated
from scrobbler.schemas import AvatarUploadSchema, ErrorSchema, UserSchema
from scrobbler.services import avatars

blp = Blueprint(
    "Avatar",
    __name__,
    description="Your profile image: upload one, or remove it.",
)


@blp.route("", methods=["GET"])
@authenticated(blp)
@blp.response(200, content_type="image/jpeg", description="Your profile image (JPEG, PNG or WebP)")
@blp.alt_response(404, schema=ErrorSchema, description="You have no profile image")
def get_avatar():
    """Your profile image"""
    data = avatars.read(g.user)
    if data is None:
        abort(404, code="no_avatar", message="You have no profile image")
    response = Response(data, mimetype=g.user.avatar_content_type or "image/jpeg")
    response.headers["Cache-Control"] = "private, no-cache"  # revalidate: it can change
    response.add_etag()
    return response.make_conditional(request)


@blp.route("", methods=["PUT"])
@authenticated(blp)
@blp.arguments(AvatarUploadSchema, location="files")
@blp.response(200, UserSchema)
@blp.alt_response(400, schema=ErrorSchema, description="Empty file")
@blp.alt_response(413, schema=ErrorSchema, description="Image too large")
@blp.alt_response(415, schema=ErrorSchema, description="Not a JPEG, PNG or WebP image")
def upload_avatar(files):
    """Upload a profile image

    Replaces your current one, whatever its source.
    """
    limit = current_app.config["AVATAR_UPLOAD_MAX_BYTES"]
    data = files["file"].read(limit + 1)
    if not data:
        metrics.avatar_uploads_total.labels("empty").inc()
        abort(400, code="empty_file", message="That file is empty.")
    if len(data) > limit:
        metrics.avatar_uploads_total.labels("too_large").inc()
        abort(413, code="file_too_large", message="That image is larger than this server accepts.")
    try:
        content_type = avatars.sniff_image_type(data)
    except avatars.UnsupportedImage:
        metrics.avatar_uploads_total.labels("bad_type").inc()
        abort(415, code="unsupported_image", message="Upload a JPEG, PNG or WebP image.")
    avatars.save(g.user, data, content_type, "upload")
    metrics.avatar_uploads_total.labels("ok").inc()
    metrics.avatar_upload_bytes.observe(len(data))
    return g.user


@blp.route("", methods=["DELETE"])
@authenticated(blp)
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="You have no profile image")
def delete_avatar():
    """Remove your profile image"""
    if not avatars.remove(g.user):
        abort(404, code="no_avatar", message="You have no profile image")
