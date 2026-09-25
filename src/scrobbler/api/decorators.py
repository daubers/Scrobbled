import functools

from flask import g, request
from flask_smorest import Blueprint, abort

from scrobbler.api import BEARER
from scrobbler.schemas import ErrorSchema
from scrobbler.services import accounts


def bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def require_ui_token(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        token = bearer_token()
        user = accounts.resolve_ui_token(token) if token else None
        if user is None:
            abort(
                401,
                code="unauthorized",
                message="Missing, invalid or expired bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        g.user = user
        return func(*args, **kwargs)

    return wrapper


def authenticated(blp: Blueprint):
    """Require a UI bearer token and document it (security scheme + 401) in OpenAPI."""

    def decorator(func):
        func = require_ui_token(func)
        func = blp.alt_response(401, schema=ErrorSchema, description="Not authenticated")(func)
        return blp.doc(security=BEARER)(func)

    return decorator
