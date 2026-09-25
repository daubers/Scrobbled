from flask import g
from flask_smorest import Blueprint, abort

from scrobbler.api.decorators import authenticated
from scrobbler.schemas import ApproveTokenSchema, AuthTokenInfoSchema, ErrorSchema
from scrobbler.services import accounts

blp = Blueprint(
    "ui_tokens",
    __name__,
    description=(
        "Approve Last.fm desktop-auth tokens. Clients send the user to `/api/auth/`, "
        "which redirects to the UI's approve page, which calls these endpoints."
    ),
)


def _token_or_404(token: str):
    try:
        return accounts.get_auth_token(token)
    except accounts.AuthTokenError as err:
        abort(404, code=f"token_{err.reason}", message="Unknown or expired token")


@blp.route("/<string:token>", methods=["GET"])
@authenticated(blp)
@blp.response(200, AuthTokenInfoSchema)
@blp.alt_response(404, schema=ErrorSchema, description="Unknown or expired token")
def token_info(token):
    """Describe a token awaiting approval

    Returns the name of the application asking for access.
    """
    auth_token = _token_or_404(token)
    return {"api_app": auth_token.api_app, "approved": auth_token.user_id is not None}


@blp.route("/approve", methods=["POST"])
@authenticated(blp)
@blp.arguments(ApproveTokenSchema)
@blp.response(204)
@blp.alt_response(404, schema=ErrorSchema, description="Unknown or expired token")
def approve(body):
    """Approve a token

    Grants the requesting application a session for your account once it calls
    auth.getSession.
    """
    _token_or_404(body["token"])
    accounts.approve_auth_token(g.user, body["token"])
