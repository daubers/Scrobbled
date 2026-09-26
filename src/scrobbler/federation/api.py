"""/api/v1/federation: sharing settings for the web UI, and public profiles."""

from flask import current_app, g
from flask_smorest import Blueprint, abort

from scrobbler.api.decorators import authenticated
from scrobbler.extensions import db
from scrobbler.federation import activities, ids, publishing, sharing
from scrobbler.federation import followers as follower_state
from scrobbler.federation import metrics as fed_metrics
from scrobbler.federation.models import FederationBlock, FederationFollower, FederationPost
from scrobbler.federation.publishing import weekly
from scrobbler.federation.schemas import (
    BlockSchema,
    FollowerListArgsSchema,
    FollowerSchema,
    PostSchema,
    PublicPostSchema,
    PublicProfileSchema,
    SharingSettingsSchema,
    SharingSettingsUpdateSchema,
    WeeklyPreviewSchema,
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
    counts = follower_state.counts(user.id)
    return {
        **{field: getattr(settings, field) for field in sharing.EDITABLE},
        **_identity(user.username),
        "followers": counts["accepted"],
        "pending_followers": counts["pending"],
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
    people follow you. Visibility changes apply to new posts only. Turning sharing off
    deletes your posts and removes all your followers (each is told).
    """
    try:
        settings, toggled = sharing.update(g.user.id, changes)
    except ValueError as err:
        abort(422, code="invalid_settings", message=str(err))
    if toggled is not None:
        fed_metrics.sharing_changes_total.labels(change="enabled" if toggled else "disabled").inc()
    if toggled is False:
        # Delete every live post first: reject_all removes the followers that
        # accepted_inboxes() needs to know where to send the Deletes.
        publishing.delete_all_for(g.user.id, g.user.username)
        follower_state.reject_all(g.user.id, g.user.username)
        db.session.commit()
    return _settings_body(g.user, settings)


@blp.route("/preview/weekly", methods=["GET"])
@authenticated(blp)
@blp.response(200, WeeklyPreviewSchema)
def preview_weekly():
    """Preview this week's summary

    What it would say right now, for the week still in progress. Never posted or stored.
    """
    result = weekly.preview(g.user.id)
    if result is None:  # pragma: no cover - g.user is always a real, current account
        abort(404, code="not_found", message="No such user")
    text, html = result
    return {"text": text, "html": html}


def _post_body(post: FederationPost) -> dict:
    return {
        "id": post.id,
        "kind": post.kind,
        "text": post.content_text,
        "visibility": post.visibility,
        "created_at": post.created_at,
        "deleted_at": post.deleted_at,
    }


@blp.route("/posts", methods=["GET"])
@authenticated(blp)
@blp.response(200, PostSchema(many=True))
def list_posts():
    """Your posts

    Newest first, including deleted ones (their text stays visible to you; deleted_at
    shows when).
    """
    posts = db.session.scalars(
        db.select(FederationPost)
        .filter_by(user_id=g.user.id)
        .order_by(FederationPost.created_at.desc())
    ).all()
    return [_post_body(p) for p in posts]


def _own_post(post_id: int) -> FederationPost:
    post = db.session.get(FederationPost, post_id)
    if post is None or post.user_id != g.user.id:
        abort(404, code="not_found", message="No such post")
    return post


@blp.route("/posts/<int:post_id>", methods=["DELETE"])
@authenticated(blp)
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="No such post")
@blp.alt_response(409, schema=ErrorSchema, description="Already deleted")
def delete_post(post_id):
    """Delete a post

    Sends a Delete to whoever received it.
    """
    post = _own_post(post_id)
    activity = publishing.delete_post(g.user.id, g.user.username, post)
    if activity is None:
        abort(409, code="already_deleted", message="That post is already deleted.")
    db.session.commit()


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


@blp.route("/profiles/<string:username>/posts/<string:post_uuid>", methods=["GET"])
@blp.response(200, PublicPostSchema)
@blp.alt_response(404, schema=ErrorSchema, description="Not found, not public, or deleted")
def public_post(username, post_uuid):
    """A public or unlisted post

    For the web UI's post page (the browser landing spot for a Note's `url`, since it
    can't itself speak ActivityPub).
    """
    found = sharing.shared_user(username)
    if found is not None:
        user, settings = found
        post = publishing.find_by_note_id(user.id, ids.note_url(username, post_uuid))
    else:
        post = None
    if (
        post is None
        or post.deleted_at is not None
        or post.visibility not in publishing.LISTED_VISIBILITIES
    ):
        fed_metrics.lookups_total.labels(kind="post", result="not_found").inc()
        abort(404, code="not_found", message="No such post.")
    fed_metrics.lookups_total.labels(kind="post", result="found").inc()
    return {
        "text": post.content_text,
        "html": post.content_html,
        "created_at": post.created_at,
        "display_name": settings.display_name or user.username,
        **{k: v for k, v in _identity(user.username).items() if k != "actor_url"},
    }


# --- Followers ---------------------------------------------------------------------------


def _follower_body(follower: FederationFollower) -> dict:
    actor = follower.actor
    return {
        "id": follower.id,
        "state": follower.state,
        "handle": actor.handle,
        "display_name": actor.display_name,
        "actor_url": actor.uri,
        "server": actor.host,
        "since": follower.created_at,
    }


def _own_follower(follower_id: int) -> FederationFollower:
    follower = db.session.get(FederationFollower, follower_id)
    if follower is None or follower.user_id != g.user.id:
        abort(404, code="not_found", message="No such follower")
    return follower


@blp.route("/followers", methods=["GET"])
@authenticated(blp)
@blp.arguments(FollowerListArgsSchema, location="query")
@blp.response(200, FollowerSchema(many=True))
def list_followers(args):
    """Your followers and follow requests

    Newest first. Filter with `state=pending` (requests) or `state=accepted`.
    """
    query = db.select(FederationFollower).filter_by(user_id=g.user.id)
    if args["state"]:
        query = query.filter_by(state=args["state"])
    followers = db.session.scalars(query.order_by(FederationFollower.created_at.desc())).all()
    return [_follower_body(f) for f in followers]


@blp.route("/followers/<int:follower_id>/approve", methods=["POST"])
@authenticated(blp)
@blp.response(200, FollowerSchema)
@blp.alt_response(404, schema=ErrorSchema, description="No such follower")
@blp.alt_response(409, schema=ErrorSchema, description="Not a pending request")
def approve_follower(follower_id):
    """Approve a follow request"""
    follower = _own_follower(follower_id)
    if follower.state != "pending":
        abort(409, code="not_pending", message="That follower isn't waiting for approval.")
    follower_state.approve(g.user.username, follower)
    db.session.commit()
    return _follower_body(follower)


@blp.route("/followers/<int:follower_id>/decline", methods=["POST"])
@authenticated(blp)
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="No such follower")
def decline_follower(follower_id):
    """Decline a follow request"""
    follower_state.remove(g.user.username, _own_follower(follower_id))
    db.session.commit()


@blp.route("/followers/<int:follower_id>/remove", methods=["POST"])
@authenticated(blp)
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="No such follower")
def remove_follower(follower_id):
    """Remove a follower

    They stop receiving your posts. They can follow you again; block them to prevent it.
    """
    follower_state.remove(g.user.username, _own_follower(follower_id))
    db.session.commit()


@blp.route("/followers/<int:follower_id>/block", methods=["POST"])
@authenticated(blp)
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="No such follower")
def block_follower(follower_id):
    """Block a follower

    Removes them and refuses their future follows.
    """
    follower_state.block(g.user.username, _own_follower(follower_id))
    db.session.commit()


@blp.route("/blocks", methods=["GET"])
@authenticated(blp)
@blp.response(200, BlockSchema(many=True))
def list_blocks():
    """Accounts you've blocked"""
    blocks = db.session.scalars(
        db.select(FederationBlock)
        .filter_by(user_id=g.user.id)
        .order_by(FederationBlock.created_at.desc())
    ).all()
    return [
        {
            "id": b.id,
            "handle": b.actor.handle,
            "display_name": b.actor.display_name,
            "actor_url": b.actor.uri,
            "since": b.created_at,
        }
        for b in blocks
    ]


@blp.route("/blocks/<int:block_id>", methods=["DELETE"])
@authenticated(blp)
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="No such block")
def unblock(block_id):
    """Unblock an account

    Their server is told (an Undo of the Block). They can follow you again; they aren't
    re-added.
    """
    block = db.session.get(FederationBlock, block_id)
    if block is None or block.user_id != g.user.id:
        abort(404, code="not_found", message="No such block")
    activities.undo_block(g.user.id, g.user.username, block.actor)  # tell their server
    db.session.delete(block)
    db.session.commit()
