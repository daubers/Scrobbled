"""/api/v1/federation: sharing settings for the web UI, and public profiles."""

from flask import current_app, g
from flask_smorest import Blueprint, abort

from scrobbler.api.decorators import authenticated
from scrobbler.federation import metrics as fed_metrics
from scrobbler.federation import sharing
from scrobbler.federation.schemas import (
    PublicProfileSchema,
    SharingSettingsSchema,
    SharingSettingsUpdateSchema,
)
from scrobbler.schemas import ErrorSchema

blp = Blueprint(
    "Federation",
    __name__,
    description=(
        "Sharing listening on the fediverse (ActivityPub). These endpoints only exist when "
        "the server has federation enabled; a 404 means it doesn't."
    ),
)


def _identity(username: str) -> dict:
    config = current_app.extensions["federation"]
    return {
        "handle": f"@{username}@{config.domain}",
        "actor_url": f"{config.base_url}/users/{username}",
        "profile_url": f"{current_app.config['UI_BASE_URL']}/profile.html?u={username}",
    }


def _settings_body(user, settings) -> dict:
    return {
        **{field: getattr(settings, field) for field in sharing.EDITABLE},
        **_identity(user.username),
        "followers": 0,
    }


@blp.route("/settings", methods=["GET"])
@authenticated(blp)
@blp.response(200, SharingSettingsSchema)
def get_settings():
    """Your sharing settings

    Sharing is off and posts are followers-only until you change them.
    """
    return _settings_body(g.user, sharing.settings_for(g.user.id))


@blp.route("/settings", methods=["PATCH"])
@authenticated(blp)
@blp.arguments(SharingSettingsUpdateSchema)
@blp.response(200, SharingSettingsSchema)
@blp.alt_response(422, schema=ErrorSchema, description="Invalid settings")
def update_settings(changes):
    """Change your sharing settings

    Turning sharing on makes you findable as your handle. Your handle can't change once
    people follow you. Visibility changes apply to new posts only.
    """
    try:
        settings, toggled = sharing.update(g.user.id, changes)
    except ValueError as err:
        abort(422, code="invalid_settings", message=str(err))
    if toggled is not None:
        fed_metrics.sharing_changes_total.labels(change="enabled" if toggled else "disabled").inc()
    return _settings_body(g.user, settings)


@blp.route("/profiles/<string:username>", methods=["GET"])
@blp.response(200, PublicProfileSchema)
@blp.alt_response(404, schema=ErrorSchema, description="No one sharing with that name")
def public_profile(username):
    """A public fediverse profile

    For the web UI's profile page. Only users who share are visible.
    """
    found = sharing.shared_user(username)
    if found is None:
        fed_metrics.lookups_total.labels(kind="profile", result="not_found").inc()
        abort(404, code="not_found", message="No one here shares their listening under that name.")
    fed_metrics.lookups_total.labels(kind="profile", result="found").inc()
    user, settings = found
    return {
        "username": user.username,
        "display_name": settings.display_name or user.username,
        "bio": settings.bio,
        **{k: v for k, v in _identity(user.username).items() if k != "profile_url"},
    }
