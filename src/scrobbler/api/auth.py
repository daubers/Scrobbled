from flask import g
from flask_smorest import Blueprint, abort

from scrobbler import metrics
from scrobbler.api.decorators import authenticated, bearer_token
from scrobbler.schemas import ErrorSchema, LoginSchema, RegisterSchema, TokenSchema, UserSchema
from scrobbler.services import accounts

blp = Blueprint("ui_auth", __name__, description="Web UI accounts and login")


@blp.route("/register", methods=["POST"])
@blp.arguments(RegisterSchema)
@blp.response(201, TokenSchema)
@blp.alt_response(409, schema=ErrorSchema, description="Username taken")
@blp.alt_response(400, schema=ErrorSchema, description="Invalid username or password")
def register(body):
    """Create an account

    Registers a user and logs them in, returning a bearer token.
    """
    try:
        user = accounts.register(body["username"], body["email"], body["password"])
    except accounts.AccountError as err:
        abort(409 if err.code == "username_taken" else 400, code=err.code, message=err.message)
    return {"token": accounts.issue_ui_token(user), "user": user}


@blp.route("/login", methods=["POST"])
@blp.arguments(LoginSchema)
@blp.response(200, TokenSchema)
@blp.alt_response(401, schema=ErrorSchema, description="Wrong username or password")
def login(body):
    """Log in

    Exchanges a username and password for a bearer token valid for 30 days.
    """
    user = accounts.authenticate(body["username"], body["password"])
    metrics.ui_logins_total.labels(result="success" if user else "failure").inc()
    if user is None:
        abort(401, code="invalid_credentials", message="Wrong username or password")
    return {"token": accounts.issue_ui_token(user), "user": user}


@blp.route("/logout", methods=["POST"])
@authenticated(blp)
@blp.response(204)
def logout():
    """Log out

    Revokes the bearer token used for this request.
    """
    accounts.revoke_ui_token(bearer_token())


@blp.route("/me", methods=["GET"])
@authenticated(blp)
@blp.response(200, UserSchema)
def me():
    """Current user"""
    return g.user
