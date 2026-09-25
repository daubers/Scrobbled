"""Users, UI bearer tokens, API applications and Last.fm sessions."""

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from scrobbler import metrics
from scrobbler.extensions import db
from scrobbler.models import ApiApp, AuthToken, Session, UiToken, User

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")
MIN_PASSWORD_LENGTH = 8
# Checked against when the username doesn't exist, so failed logins take the same time.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


class AccountError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- Users ---------------------------------------------------------------------


def find_user(username: str) -> User | None:
    return db.session.scalar(db.select(User).where(func.lower(User.username) == username.lower()))


def register(username: str, email: str, password: str) -> User:
    if not USERNAME_RE.match(username):
        raise AccountError(
            "invalid_username", "Usernames are 2-32 letters, digits, dots, dashes or underscores"
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(
            "weak_password", f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    if find_user(username) is not None:
        raise AccountError("username_taken", "That username is already taken")
    user = User(username=username, email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    metrics.registrations_total.inc()
    return user


def authenticate(username: str, password: str) -> User | None:
    user = find_user(username)
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        return None
    return user if check_password_hash(user.password_hash, password) else None


# --- UI bearer tokens ----------------------------------------------------------


def issue_ui_token(user: User) -> str:
    raw = secrets.token_urlsafe(32)
    ttl = timedelta(days=current_app.config["UI_TOKEN_TTL_DAYS"])
    db.session.add(UiToken(token_hash=_sha256(raw), user=user, expires_at=_now() + ttl))
    db.session.commit()
    return raw


def resolve_ui_token(raw: str) -> User | None:
    token = db.session.scalar(db.select(UiToken).filter_by(token_hash=_sha256(raw)))
    if token is None or token.expires_at <= _now():
        return None
    if _now() - token.last_used_at > timedelta(minutes=1):
        token.last_used_at = _now()
        db.session.commit()
    return token.user


def revoke_ui_token(raw: str) -> None:
    db.session.execute(db.delete(UiToken).filter_by(token_hash=_sha256(raw)))
    db.session.commit()


# --- API applications ----------------------------------------------------------


def list_api_apps(user: User) -> list[ApiApp]:
    return list(
        db.session.scalars(
            db.select(ApiApp).filter_by(owner_id=user.id).order_by(ApiApp.created_at)
        )
    )


def create_api_app(user: User, name: str) -> ApiApp:
    api_app = ApiApp(
        api_key=secrets.token_hex(16), shared_secret=secrets.token_hex(16), name=name, owner=user
    )
    db.session.add(api_app)
    db.session.commit()
    return api_app


def delete_api_app(user: User, api_key: str) -> bool:
    result = db.session.execute(db.delete(ApiApp).filter_by(owner_id=user.id, api_key=api_key))
    db.session.commit()
    return result.rowcount > 0


# --- Last.fm sessions ----------------------------------------------------------


def create_session(user: User, api_app: ApiApp, flow: str) -> Session:
    session = Session(session_key=secrets.token_hex(16), user=user, api_app=api_app)
    db.session.add(session)
    db.session.commit()
    metrics.auth_sessions_created_total.labels(flow=flow).inc()
    return session


def list_sessions(user: User) -> list[Session]:
    return list(
        db.session.scalars(
            db.select(Session).filter_by(user_id=user.id).order_by(Session.created_at.desc())
        )
    )


def revoke_session(user: User, session_id: int) -> bool:
    result = db.session.execute(db.delete(Session).filter_by(user_id=user.id, id=session_id))
    db.session.commit()
    return result.rowcount > 0


# --- Last.fm desktop auth tokens -----------------------------------------------


class AuthTokenError(Exception):
    """Raised with a reason: "invalid", "expired" or "unauthorized"."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def create_auth_token(api_app: ApiApp) -> str:
    token = AuthToken(token=secrets.token_hex(16), api_app=api_app)
    db.session.add(token)
    db.session.commit()
    return token.token


def get_auth_token(token: str) -> AuthToken:
    """A token that exists and hasn't expired, or AuthTokenError."""
    auth_token = db.session.scalar(db.select(AuthToken).filter_by(token=token))
    if auth_token is None:
        raise AuthTokenError("invalid")
    ttl = timedelta(seconds=current_app.config["AUTH_TOKEN_TTL_SECONDS"])
    if auth_token.created_at + ttl <= _now():
        raise AuthTokenError("expired")
    return auth_token


def approve_auth_token(user: User, token: str) -> AuthToken:
    auth_token = get_auth_token(token)
    auth_token.user = user
    db.session.commit()
    return auth_token


def exchange_auth_token(api_app: ApiApp, token: str) -> Session:
    """Swap an approved token for a session key. Tokens are single use."""
    auth_token = get_auth_token(token)
    if auth_token.api_app_id != api_app.id:
        raise AuthTokenError("invalid")
    if auth_token.user is None:
        raise AuthTokenError("unauthorized")
    user = auth_token.user
    db.session.delete(auth_token)
    return create_session(user, api_app, flow="desktop")
