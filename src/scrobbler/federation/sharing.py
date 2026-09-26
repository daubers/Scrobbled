"""Per-user sharing settings: whether a user federates, and how."""

from scrobbler.extensions import db
from scrobbler.federation import keys
from scrobbler.federation.models import VISIBILITIES, FederationSettings
from scrobbler.federation.protocol.schedule import is_valid_timezone
from scrobbler.services import accounts

EDITABLE = (
    "enabled",
    "visibility",
    "manually_approves_followers",
    "discoverable",
    "indexable",
    "display_name",
    "bio",
    "timezone",
    "post_weekly_summary",
    "post_milestones",
)


def settings_for(user_id: int) -> FederationSettings:
    """The user's settings; unsaved defaults if they've never changed them."""
    settings = db.session.get(FederationSettings, user_id)
    if settings is None:
        # Not added to the session: the model's column defaults apply on INSERT, not
        # here, so every field a caller might read has to be set explicitly.
        settings = FederationSettings(
            user_id=user_id,
            enabled=False,
            visibility="followers",
            manually_approves_followers=False,
            discoverable=False,
            indexable=False,
            timezone="UTC",
            post_weekly_summary=True,
            post_milestones=True,
        )
    return settings


def update(user_id: int, changes: dict) -> tuple[FederationSettings, bool | None]:
    """Apply changes. Returns the settings and True/False if sharing was switched
    on/off by this update (None if unchanged)."""
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise ValueError(f"not editable: {sorted(unknown)}")
    if changes.get("visibility", "followers") not in VISIBILITIES:
        raise ValueError("visibility must be one of " + ", ".join(VISIBILITIES))
    if "timezone" in changes and not is_valid_timezone(changes["timezone"]):
        raise ValueError(f"not a known time zone: {changes['timezone']!r}")
    settings = settings_for(user_id)
    was_enabled = bool(settings.enabled)
    for field, value in changes.items():
        setattr(settings, field, value)
    db.session.add(settings)
    db.session.commit()
    if settings.enabled and not was_enabled:
        keys.key_for(user_id)  # create the key pair before anyone can fetch the actor
    toggled = None if settings.enabled == was_enabled else bool(settings.enabled)
    return settings, toggled


def shared_user(username: str):
    """(user, settings) for a user who has switched sharing on, else None. Callers must
    treat 'unknown user' and 'not sharing' identically, so accounts can't be probed."""
    user = accounts.find_user(username)
    if user is None:
        return None
    settings = db.session.get(FederationSettings, user.id)
    if settings is None or not settings.enabled:
        return None
    return user, settings


def sharing_count() -> int:
    return db.session.scalar(
        db.select(db.func.count()).select_from(FederationSettings).filter_by(enabled=True)
    )


def farewell_user(username: str):
    """A user who has stopped sharing but still has messages on their way out (the
    Rejects telling followers). Their actor stays fetchable, minimally, so other servers
    can verify those messages. None otherwise."""
    from scrobbler.federation.models import FederationActivity, FederationDelivery

    user = accounts.find_user(username)
    if user is None:
        return None
    settings = db.session.get(FederationSettings, user.id)
    if settings is None or settings.enabled:
        return None
    pending = db.session.scalar(
        db.select(db.func.count())
        .select_from(FederationDelivery)
        .join(FederationActivity, FederationActivity.id == FederationDelivery.activity_id)
        .filter(FederationActivity.user_id == user.id, FederationDelivery.status == "pending")
    )
    return user if pending else None
