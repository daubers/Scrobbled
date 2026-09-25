"""JSON REST API used by the web UI, mounted at /api/v1."""

from flask import Flask, jsonify
from flask_smorest import Api
from werkzeug.exceptions import HTTPException

from scrobbler.schemas import ErrorSchema

BEARER = [{"bearerAuth": []}]


class ScrobblerApi(Api):
    ERROR_SCHEMA = ErrorSchema

    def handle_http_exception(self, error: HTTPException):
        """Render every HTTP error as {"error": {"code", "message", "details"?}}."""
        data = getattr(error, "data", None) or {}
        body = {
            "code": data.get("code") or error.name.lower().replace(" ", "_"),
            "message": data.get("message") or error.description,
        }
        details = data.get("errors") or data.get("messages")
        if details:
            body["details"] = details
        response = jsonify({"error": body})
        response.status_code = error.code or 500
        for name, value in (data.get("headers") or {}).items():
            response.headers[name] = value
        return response


api = ScrobblerApi()


def init_app(app: Flask) -> None:
    api.init_app(app)
    api.spec.components.security_scheme(
        "bearerAuth",
        {
            "type": "http",
            "scheme": "bearer",
            "description": "Token from POST /api/v1/auth/login",
        },
    )

    from scrobbler.api import apps, auth, sessions, tokens

    api.register_blueprint(auth.blp, url_prefix="/api/v1/auth")
    api.register_blueprint(apps.blp, url_prefix="/api/v1/apps")
    api.register_blueprint(sessions.blp, url_prefix="/api/v1/sessions")
    api.register_blueprint(tokens.blp, url_prefix="/api/v1/tokens")
