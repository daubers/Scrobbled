from marshmallow import Schema, fields, validate

from scrobbler.federation.models import VISIBILITIES


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
    handle = fields.String(dump_only=True, metadata={"example": "@alice@scrobble.example"})
    actor_url = fields.String(dump_only=True)
    profile_url = fields.String(dump_only=True)
    followers = fields.Integer(
        dump_only=True, metadata={"description": "0 until following arrives"}
    )


class PublicProfileSchema(Schema):
    username = fields.String()
    display_name = fields.String()
    bio = fields.String(allow_none=True)
    handle = fields.String(metadata={"example": "@alice@scrobble.example"})
    actor_url = fields.String()


class SharingSettingsUpdateSchema(SharingSettingsSchema):
    """Any subset of the editable settings (read-only fields are rejected)."""
