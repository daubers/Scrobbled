"""Last.fm / Audioscrobbler 2.0 compatible API, mounted at /2.0/."""

import time

from flask import Blueprint, Response, current_app, redirect, request

from scrobbler import metrics
from scrobbler.extensions import db
from scrobbler.lastfm import errors, signature
from scrobbler.lastfm.errors import LastFMError
from scrobbler.lastfm.registry import METHODS, Call, LastfmMethod
from scrobbler.lastfm.responses import render, render_error
from scrobbler.models import ApiApp, Session

bp = Blueprint("lastfm", __name__)

_AUTH_FAILURE_REASONS = {
    errors.INVALID_API_KEY: "bad_api_key",
    errors.INVALID_SIGNATURE: "bad_signature",
    errors.INVALID_SESSION_KEY: "bad_session",
    errors.AUTHENTICATION_FAILED: "bad_password",
    errors.TOKEN_EXPIRED: "token_expired",
    errors.UNAUTHORIZED_TOKEN: "token_unauthorized",
}


def _request_params() -> dict[str, str]:
    # Clients send params in the query string (GET) or a form body (POST); body wins.
    params = request.args.to_dict()
    params.update(request.form.to_dict())
    return params


def _authenticate(method: LastfmMethod, params: dict[str, str], fmt: str) -> Call:
    api_key = params.get("api_key")
    if not api_key:
        raise LastFMError(errors.INVALID_PARAMETERS, "Missing required parameter: api_key")
    api_app = db.session.scalar(db.select(ApiApp).filter_by(api_key=api_key))
    if api_app is None:
        raise LastFMError(errors.INVALID_API_KEY)
    if method.signed and not signature.is_valid(params, api_app.shared_secret):
        raise LastFMError(errors.INVALID_SIGNATURE)

    call = Call(params=params, api_app=api_app, format=fmt)
    if method.session:
        sk = params.get("sk")
        if not sk:
            raise LastFMError(errors.INVALID_PARAMETERS, "Missing required parameter: sk")
        session = db.session.scalar(db.select(Session).filter_by(session_key=sk))
        if session is None or session.api_app_id != api_app.id:
            raise LastFMError(errors.INVALID_SESSION_KEY)
        call.user = session.user

    # Array params (artist[i]) are validated by the handler, which knows the batch shape.
    missing = [
        p.name
        for p in method.params
        if p.required and not p.name.endswith("[i]") and not params.get(p.name)
    ]
    if missing:
        raise LastFMError(
            errors.INVALID_PARAMETERS, f"Missing required parameter: {', '.join(missing)}"
        )
    return call


@bp.route("/2.0/", methods=["GET", "POST"], strict_slashes=False)
def dispatch() -> Response:
    params = _request_params()
    fmt = "json" if params.get("format", "").lower() == "json" else "xml"
    requested = (params.get("method") or "").lower()
    method = METHODS.get(requested)
    label = method.name if method else "unknown"
    start = time.perf_counter()
    try:
        if method is None:
            raise LastFMError(errors.INVALID_METHOD)
        result = method.handler(_authenticate(method, params, fmt))
        if isinstance(result, Response):
            response = result
        else:
            el, force_list = result if isinstance(result, tuple) else (result, ())
            response = render(el, fmt, force_list=force_list)
        outcome = "ok"
    except LastFMError as err:
        db.session.rollback()
        response = render_error(err, fmt)
        outcome = "error"
        metrics.lastfm_errors_total.labels(lastfm_method=label, code=str(err.code)).inc()
        if reason := _AUTH_FAILURE_REASONS.get(err.code):
            metrics.auth_failures_total.labels(reason=reason).inc()

    metrics.lastfm_requests_total.labels(lastfm_method=label, format=fmt, outcome=outcome).inc()
    metrics.lastfm_request_duration_seconds.labels(lastfm_method=label).observe(
        time.perf_counter() - start
    )
    return response


@bp.route("/api/auth/", methods=["GET"], strict_slashes=False)
def approve_redirect() -> Response:
    """Where Last.fm clients send users to approve a token. Approval happens in the
    separately hosted web UI, so hand the query string over to its approve page."""
    query = request.query_string.decode()
    return redirect(f"{current_app.config['UI_BASE_URL']}/approve.html?{query}", code=302)


from scrobbler.lastfm import methods  # noqa: E402,F401  (register handlers)
