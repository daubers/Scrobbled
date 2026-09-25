from flask import g
from flask_smorest import Blueprint, abort

from scrobbler.api.decorators import authenticated
from scrobbler.schemas import ApiAppCreateSchema, ApiAppSchema, ErrorSchema
from scrobbler.services import accounts

blp = Blueprint(
    "Apps",
    __name__,
    description=(
        "API applications. Each has an API key and shared secret that a scrobbling "
        "client uses against the Last.fm-compatible `/2.0/` endpoint."
    ),
)


@blp.route("", methods=["GET"])
@authenticated(blp)
@blp.response(200, ApiAppSchema(many=True))
def list_apps():
    """List your API applications"""
    return accounts.list_api_apps(g.user)


@blp.route("", methods=["POST"])
@authenticated(blp)
@blp.arguments(ApiAppCreateSchema)
@blp.response(201, ApiAppSchema)
def create_app(body):
    """Create an API application"""
    return accounts.create_api_app(g.user, body["name"])


@blp.route("/<string:api_key>", methods=["DELETE"])
@authenticated(blp)
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="No such application")
def delete_app(api_key):
    """Delete an API application

    Also revokes every session issued to it.
    """
    if not accounts.delete_api_app(g.user, api_key):
        abort(404, code="not_found", message="No such application")
