from marshmallow import Schema, ValidationError, fields, validate

from scrobbler.federation.models import NOW_PLAYING_MODES, VISIBILITIES
from scrobbler.federation.protocol.schedule import is_valid_timezone


def _valid_timezone(value: str) -> None:
    if not is_valid_timezone(value):
        raise ValidationError("Not a known time zone.")


class SharingSettingsSchema(Schema):
    enabled = fields.Boolean(metadata={"description": "Share listening on the fediverse"})
    visibility = fields.String(
        validate=validate.OneOf(VISIBILITIES),
        metadata={
            "description": (
                "Who sees posts: `followers` (default), `unlisted` (anyone visiting the "
                "profile, not public timelines) or `public`. Followers-only is only as private "
                "as who may follow: see `manually_approves_followers`."
            )
        },
    )
    manually_approves_followers = fields.Boolean()
    discoverable = fields.Boolean(metadata={"description": "May appear in profile directories"})
    indexable = fields.Boolean(metadata={"description": "Posts may be indexed for search"})
    display_name = fields.String(allow_none=True, validate=validate.Length(max=100))
    bio = fields.String(allow_none=True, validate=validate.Length(max=500))
    timezone = fields.String(
        validate=_valid_timezone,
        metadata={
            "description": (
                "IANA time zone, e.g. `Europe/London`. Decides when a week ends for the "
                "weekly summary."
            ),
            "example": "Europe/London",
        },
    )
    post_weekly_summary = fields.Boolean(
        metadata={"description": "Post a weekly summary of your listening"}
    )
    post_milestones = fields.Boolean(
        metadata={
            "description": "Post scrobble-count, artist-plays and top-10 milestones as you hit them"
        }
    )
    now_playing_mode = fields.String(
        validate=validate.OneOf(NOW_PLAYING_MODES),
        metadata={
            "description": (
                '`off` (default): nothing. `profile`: a "Now playing" field on your '
                "profile, refreshed at most every 5 minutes and cleared when you stop. "
                "`posts`: the profile field, plus a post for each track played 30+ "
                "seconds, at most one every 30 minutes, replacing the last one."
            )
        },
    )
    handle = fields.String(dump_only=True, metadata={"example": "@alice@scrobble.example"})
    actor_url = fields.String(dump_only=True)
    profile_url = fields.String(dump_only=True)
    followers = fields.Integer(dump_only=True, metadata={"description": "Accepted followers"})
    pending_followers = fields.Integer(
        dump_only=True, metadata={"description": "Waiting for approval"}
    )


class PublicProfileSchema(Schema):
    username = fields.String()
    display_name = fields.String()
    bio = fields.String(allow_none=True)
    handle = fields.String(metadata={"example": "@alice@scrobble.example"})
    actor_url = fields.String()


class SharingSettingsUpdateSchema(SharingSettingsSchema):
    """Any subset of the editable settings (read-only fields are rejected)."""


class FollowerSchema(Schema):
    id = fields.Integer()
    state = fields.String(validate=validate.OneOf(["pending", "accepted"]))
    handle = fields.String(metadata={"example": "@bob@mastodon.example"})
    display_name = fields.String(allow_none=True)
    actor_url = fields.String()
    server = fields.String(metadata={"example": "mastodon.example"})
    since = fields.DateTime(metadata={"description": "When they followed (or asked to)"})


class FollowerListArgsSchema(Schema):
    state = fields.String(load_default=None, validate=validate.OneOf(["pending", "accepted"]))


class BlockSchema(Schema):
    id = fields.Integer()
    handle = fields.String()
    display_name = fields.String(allow_none=True)
    actor_url = fields.String()
    since = fields.DateTime()


class PostSchema(Schema):
    id = fields.Integer()
    kind = fields.String(metadata={"description": "weekly, milestone or now_playing"})
    text = fields.String()
    visibility = fields.String()
    created_at = fields.DateTime()
    deleted_at = fields.DateTime(allow_none=True)


class WeeklyPreviewSchema(Schema):
    text = fields.String()
    html = fields.String()


class PublicPostSchema(Schema):
    """A public or unlisted post, for the web UI's post page (post.html)."""

    text = fields.String()
    html = fields.String()
    created_at = fields.DateTime()
    display_name = fields.String()
    handle = fields.String(metadata={"example": "@alice@scrobble.example"})
    profile_url = fields.String()
