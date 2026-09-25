from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from scrobbler.extensions import db


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("uq_users_username_lower", func.lower(username), unique=True),)


class ApiApp(db.Model):
    """A client application; scrobblers authenticate with its key and secret."""

    __tablename__ = "api_apps"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_key: Mapped[str] = mapped_column(String(32), unique=True)
    shared_secret: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship()


class UiToken(db.Model):
    """Bearer token for the web UI. Only the sha256 of the token is stored."""

    __tablename__ = "ui_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class AuthToken(db.Model):
    """Last.fm desktop-auth token: issued unapproved, approved by a user in the UI."""

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(32), unique=True)
    api_app_id: Mapped[int] = mapped_column(ForeignKey("api_apps.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    api_app: Mapped[ApiApp] = relationship()
    user: Mapped[User | None] = relationship()


class Session(db.Model):
    """Last.fm session key granting a client app access to a user's account."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_key: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    api_app_id: Mapped[int] = mapped_column(ForeignKey("api_apps.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()
    api_app: Mapped[ApiApp] = relationship()


class Scrobble(db.Model):
    __tablename__ = "scrobbles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    artist: Mapped[str] = mapped_column(Text)
    track: Mapped[str] = mapped_column(Text)
    album: Mapped[str | None] = mapped_column(Text)
    album_artist: Mapped[str | None] = mapped_column(Text)
    track_number: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[int | None] = mapped_column(Integer)
    mbid: Mapped[str | None] = mapped_column(String(36))
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    api_app_id: Mapped[int | None] = mapped_column(ForeignKey("api_apps.id", ondelete="SET NULL"))

    __table_args__ = (
        UniqueConstraint("user_id", "played_at", "artist", "track", name="uq_scrobbles_dedupe"),
        Index("ix_scrobbles_user_played_at", "user_id", played_at.desc()),
    )


class NowPlaying(db.Model):
    __tablename__ = "now_playing"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    artist: Mapped[str] = mapped_column(Text)
    track: Mapped[str] = mapped_column(Text)
    album: Mapped[str | None] = mapped_column(Text)
    album_artist: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
