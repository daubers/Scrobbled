"""Marshmallow schemas for the /api/v1 JSON API. They drive both request
validation and the generated OpenAPI document."""

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
