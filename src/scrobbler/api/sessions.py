from flask import g
from flask_smorest import Blueprint, abort

from scrobbler.api.decorators import authenticated
from scrobbler.schemas import ErrorSchema, SessionSchema
from scrobbler.services import accounts

blp = Blueprint(
    "ui_sessions",
    __name__,
    description="Last.fm session keys issued to scrobbling clients on your behalf.",
)


@blp.route("", methods=["GET"])
@authenticated(blp)
@blp.response(200, SessionSchema(many=True))
def list_sessions():
    """List client sessions"""
    return accounts.list_sessions(g.user)


@blp.route("/<int:session_id>", methods=["DELETE"])
@authenticated(blp)
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="No such session")
def revoke_session(session_id):
    """Revoke a client session

    The client's session key stops working immediately (Last.fm error 9).
    """
    if not accounts.revoke_session(g.user, session_id):
        abort(404, code="not_found", message="No such session")
