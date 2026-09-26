"""Federation's own tables. All prefixed federation_; the only link to the core schema
is a foreign key to users. Migrations live on the `federation` Alembic branch."""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from scrobbler.extensions import db

VISIBILITIES = ("followers", "unlisted", "public")
NOW_PLAYING_MODES = ("off", "profile", "posts")


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
    # An IANA zone name (validated with zoneinfo), used to decide when a Monday starts.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    post_weekly_summary: Mapped[bool] = mapped_column(Boolean, default=True)
    post_milestones: Mapped[bool] = mapped_column(Boolean, default=True)
    # off, profile or posts (posts implies profile too)
    now_playing_mode: Mapped[str] = mapped_column(String(16), default="off")
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
    inbox: Mapped[str] = mapped_column(String(1024))
    shared_inbox: Mapped[str | None] = mapped_column(String(1024))
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


class FederationFollower(db.Model):
    """A remote actor following one of our users."""

    __tablename__ = "federation_followers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    remote_actor_id: Mapped[int] = mapped_column(
        ForeignKey("federation_remote_actors.id", ondelete="CASCADE")
    )
    state: Mapped[str] = mapped_column(String(16))  # pending or accepted
    follow_activity_id: Mapped[str] = mapped_column(String(2048))
    follow_activity: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    actor: Mapped[FederationRemoteActor] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("user_id", "remote_actor_id", name="uq_federation_follower"),
    )


class FederationBlock(db.Model):
    """A user blocking a remote actor (their follows are refused)."""

    __tablename__ = "federation_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    remote_actor_id: Mapped[int] = mapped_column(
        ForeignKey("federation_remote_actors.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    actor: Mapped[FederationRemoteActor] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("user_id", "remote_actor_id", name="uq_federation_block"),)


class FederationInboxItem(db.Model):
    """A delivery received at an inbox, queued for the worker to verify and process."""

    __tablename__ = "federation_inbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[str] = mapped_column(String(2048), unique=True)
    activity_type: Mapped[str] = mapped_column(String(64))
    actor_uri: Mapped[str] = mapped_column(String(2048))
    inbox_path: Mapped[str] = mapped_column(String(512))  # as received, for the signature
    headers: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    body: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(
        String(16), default="queued"
    )  # queued, processed, rejected, failed
    reason: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_federation_inbox_status_received", "status", "received_at"),)


class FederationActivity(db.Model):
    """An activity we sent (Accept, Reject, Block; posts in phase 3)."""

    __tablename__ = "federation_activities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID, part of its URL
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    activity_type: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    public: Mapped[bool] = mapped_column(Boolean, default=False)  # may be fetched by anyone
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FederationDelivery(db.Model):
    """One activity to one inbox, with retries."""

    __tablename__ = "federation_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("federation_activities.id", ondelete="CASCADE"), index=True
    )
    inbox: Mapped[str] = mapped_column(String(1024))  # keeps the unique index small
    # pending, delivered, failed or abandoned
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    activity: Mapped[FederationActivity] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("activity_id", "inbox", name="uq_federation_delivery"),
        Index("ix_federation_deliveries_due", "status", "next_attempt_at"),
    )


class FederationPost(db.Model):
    """A published Note (weekly summary or milestone), wrapped in the Create activity it
    was sent with. One row per (user, key): the unique constraint is what makes posting
    idempotent, since a key like `weekly:2026-W39` can only be inserted once."""

    __tablename__ = "federation_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # weekly or milestone
    key: Mapped[str] = mapped_column(String(64))  # e.g. weekly:2026-W39, milestone:scrobbles:10000
    activity_id: Mapped[str] = mapped_column(
        ForeignKey("federation_activities.id", ondelete="CASCADE"), unique=True
    )
    visibility: Mapped[str] = mapped_column(String(16))  # as posted; unaffected by later changes
    content_text: Mapped[str] = mapped_column(Text)
    content_html: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    activity: Mapped[FederationActivity] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_federation_post_key"),)


class FederationMilestoneMark(db.Model):
    """A milestone threshold a user has reached. `posted=False` for thresholds passed
    silently (the baseline set when a user enables sharing, or after an import finishes)
    so they're marked done without ever producing a post."""

    __tablename__ = "federation_milestone_marks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(64))  # e.g. scrobbles:10000, artist:radiohead:500
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_federation_milestone_mark"),)


class FederationPendingCheck(db.Model):
    """A user whose milestones need checking, queued by the scrobbles_stored event
    receiver and drained by the worker. One row per user: further scrobbles before the
    worker gets to it just update `since`, so scrobbling stays fast and the receiver never
    does real work inside a scrobble request."""

    __tablename__ = "federation_pending_checks"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str] = mapped_column(String(32), default="scrobble")


class FederationNowPlaying(db.Model):
    """What we've last told the fediverse is playing, as opposed to `now_playing` in the
    core schema, which is what's actually playing. A "key" is `artist\\x1ftrack\\x1f
    started_at`: playing the same track again later is a new now-playing moment, not a
    stale repeat, and it doubles as federation_posts.key for the posts side."""

    __tablename__ = "federation_now_playing"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    profile_key: Mapped[str | None] = mapped_column(String(512))
    profile_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_key: Mapped[str | None] = mapped_column(String(512))
    post_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
