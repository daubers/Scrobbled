"""Marshmallow schemas for the /api/v1 JSON API. They drive both request
validation and the generated OpenAPI document."""

from flask_smorest.fields import Upload
from marshmallow import Schema, fields, validate


class ErrorDetailSchema(Schema):
    code = fields.String(required=True, metadata={"example": "invalid_credentials"})
    message = fields.String(required=True, metadata={"example": "Wrong username or password"})
    details = fields.Dict(metadata={"description": "Per-field validation errors, if any"})


class ErrorSchema(Schema):
    error = fields.Nested(ErrorDetailSchema, required=True)


class UserSchema(Schema):
    id = fields.Integer(dump_only=True)
    username = fields.String(dump_only=True, metadata={"example": "alice"})
    email = fields.String(dump_only=True, metadata={"example": "alice@example.com"})
    created_at = fields.DateTime(dump_only=True)


class RegisterSchema(Schema):
    username = fields.String(required=True, validate=validate.Length(min=2, max=32))
    email = fields.Email(required=True)
    password = fields.String(required=True, load_only=True, validate=validate.Length(min=8))


class LoginSchema(Schema):
    username = fields.String(required=True)
    password = fields.String(required=True, load_only=True)


class TokenSchema(Schema):
    token = fields.String(
        required=True, metadata={"description": "Bearer token for the Authorization header"}
    )
    user = fields.Nested(UserSchema, required=True)


class ApiAppSchema(Schema):
    name = fields.String(dump_only=True, metadata={"example": "Living room player"})
    api_key = fields.String(dump_only=True, metadata={"example": "a" * 32})
    shared_secret = fields.String(dump_only=True, metadata={"example": "b" * 32})
    created_at = fields.DateTime(dump_only=True)


class ApiAppCreateSchema(Schema):
    name = fields.String(required=True, validate=validate.Length(min=1, max=100))


class SessionSchema(Schema):
    id = fields.Integer(dump_only=True)
    app_name = fields.String(
        dump_only=True, attribute="api_app.name", metadata={"example": "Pano Scrobbler"}
    )
    created_at = fields.DateTime(dump_only=True)


class AuthTokenInfoSchema(Schema):
    app_name = fields.String(
        dump_only=True, attribute="api_app.name", metadata={"example": "Pano Scrobbler"}
    )
    approved = fields.Boolean(dump_only=True)


class ApproveTokenSchema(Schema):
    token = fields.String(required=True, metadata={"description": "Token from auth.getToken"})


PERIODS = ["overall", "7day", "1month", "3month", "6month", "12month"]


class PageArgsSchema(Schema):
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    limit = fields.Integer(load_default=50, validate=validate.Range(min=1, max=200))


class TopArgsSchema(PageArgsSchema):
    period = fields.String(load_default="7day", validate=validate.OneOf(PERIODS))


class CountsArgsSchema(Schema):
    period = fields.String(load_default="1month", validate=validate.OneOf(PERIODS))
    bucket = fields.String(load_default="day", validate=validate.OneOf(["day", "week", "month"]))


class PageMetaSchema(Schema):
    page = fields.Integer()
    per_page = fields.Integer()
    total = fields.Integer()
    total_pages = fields.Integer()


class ScrobbleSchema(Schema):
    artist = fields.String()
    track = fields.String()
    album = fields.String(allow_none=True)
    album_artist = fields.String(allow_none=True)
    duration = fields.Integer(allow_none=True)
    played_at = fields.DateTime()


class RecentPageSchema(PageMetaSchema):
    items = fields.List(fields.Nested(ScrobbleSchema))


class NowPlayingTrackSchema(Schema):
    artist = fields.String()
    track = fields.String()
    album = fields.String(allow_none=True)
    duration = fields.Integer(allow_none=True)
    started_at = fields.DateTime()
    expires_at = fields.DateTime()


class NowPlayingSchema(Schema):
    now_playing = fields.Nested(
        NowPlayingTrackSchema, allow_none=True, metadata={"description": "null when idle"}
    )


class TopItemSchema(Schema):
    rank = fields.Integer()
    name = fields.String()
    artist = fields.String(allow_none=True, metadata={"description": "Albums and tracks only"})
    playcount = fields.Integer()


class TopPageSchema(PageMetaSchema):
    period = fields.String()
    items = fields.List(fields.Nested(TopItemSchema))


class CountSchema(Schema):
    start = fields.Date(metadata={"description": "First day of the bucket (UTC)"})
    count = fields.Integer()


class TrackSearchArgsSchema(Schema):
    q = fields.String(required=True, validate=validate.Length(min=1, max=200))
    limit = fields.Integer(load_default=20, validate=validate.Range(min=1, max=50))


class TrackSearchResultSchema(Schema):
    artist = fields.String()
    track = fields.String(attribute="name")
    playcount = fields.Integer()


class TrackDetailArgsSchema(PageArgsSchema):
    artist = fields.String(required=True, validate=validate.Length(min=1, max=500))
    track = fields.String(required=True, validate=validate.Length(min=1, max=500))


class AlbumArtArgsSchema(Schema):
    artist = fields.String(required=True, validate=validate.Length(min=1, max=500))
    album = fields.String(required=True, validate=validate.Length(min=1, max=500))


class TrackMetadataSchema(Schema):
    album = fields.String(allow_none=True)
    album_artist = fields.String(allow_none=True)
    track_number = fields.Integer(allow_none=True)
    duration = fields.Integer(allow_none=True)
    mbid = fields.String(allow_none=True)
    playcount = fields.Integer()
    first_played_at = fields.DateTime()
    last_played_at = fields.DateTime()


class TrackDetailSchema(PageMetaSchema):
    artist = fields.String()
    track = fields.String()
    metadata = fields.Nested(TrackMetadataSchema)
    items = fields.List(fields.Nested(ScrobbleSchema))
    counts = fields.List(
        fields.Nested(CountSchema), metadata={"description": "Daily plays over the last 90 days"}
    )


class SummarySchema(Schema):
    scrobbles = fields.Integer()
    artists = fields.Integer()
    tracks = fields.Integer()
    first_scrobble_at = fields.DateTime(allow_none=True)


IMPORT_SOURCES = ["csv", "json", "lastfm"]
IMPORT_STATUSES = ["pending", "running", "completed", "failed", "cancelled"]


class ImportOptionsSchema(Schema):
    sources = fields.List(
        fields.String(validate=validate.OneOf(IMPORT_SOURCES)),
        metadata={"description": "`lastfm` is only listed when the server has a Last.fm API key"},
    )
    max_upload_bytes = fields.Integer()


class ImportJobSchema(Schema):
    id = fields.Integer()
    source = fields.String(validate=validate.OneOf(IMPORT_SOURCES))
    status = fields.String(validate=validate.OneOf(IMPORT_STATUSES))
    filename = fields.String(allow_none=True)
    lastfm_username = fields.String(allow_none=True)
    total = fields.Integer(
        allow_none=True, metadata={"description": "Rows or tracks to process, once known"}
    )
    processed = fields.Integer()
    imported = fields.Integer(metadata={"description": "New scrobbles added"})
    duplicates = fields.Integer(metadata={"description": "Already in your history"})
    skipped = fields.Integer(metadata={"description": "No artist/track, bad date, or future"})
    error = fields.String(allow_none=True)
    created_at = fields.DateTime()
    started_at = fields.DateTime(allow_none=True)
    finished_at = fields.DateTime(allow_none=True)


class ImportFileSchema(Schema):
    file = Upload(
        required=True,
        metadata={"description": "CSV or JSON export, optionally gzipped"},
    )


class ImportFormatSchema(Schema):
    format = fields.String(
        load_default="auto",
        validate=validate.OneOf(["auto", "csv", "json"]),
        metadata={"description": "Detected from the file name and contents when `auto`"},
    )


class LastfmImportSchema(Schema):
    username = fields.String(
        required=True, validate=validate.Length(min=1, max=64), metadata={"example": "rj"}
    )
