"""Federation's own tables. All prefixed federation_; the only link to the core schema
is a foreign key to users. Migrations live on the `federation` Alembic branch."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from scrobbler.extensions import db

VISIBILITIES = ("followers", "unlisted", "public")


def utcnow() -> datetime:
    return datetime.now(UTC)


class FederationSettings(db.Model):
    __tablename__ = "federation_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    visibility: Mapped[str] = mapped_column(String(16), default="followers")
    manually_approves_followers: Mapped[bool] = mapped_column(Boolean, default=False)
    discoverable: Mapped[bool] = mapped_column(Boolean, default=False)
    indexable: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    bio: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FederationKey(db.Model):
    """An actor's RSA key pair. user_id is NULL for the instance actor."""

    __tablename__ = "federation_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    public_key_pem: Mapped[str] = mapped_column(Text)
    private_key_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FederationRemoteActor(db.Model):
    """A cached copy of another server's actor (who follows us, and their key)."""

    __tablename__ = "federation_remote_actors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uri: Mapped[str] = mapped_column(String(2048), unique=True)
    host: Mapped[str] = mapped_column(String(255), index=True)
    inbox: Mapped[str] = mapped_column(String(2048))
    shared_inbox: Mapped[str | None] = mapped_column(String(2048))
    username: Mapped[str | None] = mapped_column(String(255))  # preferredUsername
    display_name: Mapped[str | None] = mapped_column(String(255))
    public_key_id: Mapped[str] = mapped_column(String(2048), index=True)
    public_key_pem: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    gone: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def handle(self) -> str:
        return f"@{self.username or '?'}@{self.host}"

    @property
    def delivery_inbox(self) -> str:
        return self.shared_inbox or self.inbox
