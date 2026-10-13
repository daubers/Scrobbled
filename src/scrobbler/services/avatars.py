"""Profile images.

The bytes live on disk, in an "avatars" folder inside ART_STORAGE_DIR (so they share its
volume and its backups), one file per user named by user id with no extension. The users
row records where the image came from and its content type; that is the source of truth
for whether there is one.
"""

import hashlib
import os
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from flask import current_app

from scrobbler import metrics
from scrobbler.extensions import db
from scrobbler.models import User
from scrobbler.services.art import IMAGE_TYPES, UnsupportedImage, sniff_image_type

__all__ = [
    "IMAGE_TYPES",
    "GravatarDisabled",
    "GravatarMissing",
    "GravatarUnavailable",
    "UnsupportedImage",
    "read",
    "remove",
    "save",
    "sniff_image_type",
    "use_gravatar",
]

GRAVATAR_SIZE = 256  # px; plenty for a header icon or a profile page
GRAVATAR_MAX_BYTES = 1024 * 1024
GRAVATAR_TIMEOUT = 5  # seconds - this runs inside a web request


class GravatarDisabled(Exception):
    """The admin hasn't turned Gravatar on."""


class GravatarMissing(Exception):
    """Gravatar has no image for the user's email address."""


class GravatarUnavailable(Exception):
    """Gravatar couldn't be reached, or sent something unusable."""


def _path(user_id: int) -> Path:
    return Path(current_app.config["ART_STORAGE_DIR"]) / "avatars" / str(user_id)


def read(user: User) -> bytes | None:
    """The user's image bytes, or None if they have none (or the file has gone missing)."""
    if not user.has_avatar:
        return None
    try:
        return _path(user.id).read_bytes()
    except FileNotFoundError:
        return None


def _write(user_id: int, data: bytes) -> None:
    """Writes via a temp file and a rename, so a reader never sees half an image."""
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{user_id}.")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def save(user: User, data: bytes, content_type: str, source: str) -> None:
    """Make `data` the user's image. The caller has already validated it. The file is
    written first, so a failure leaves the previous image (and row) intact."""
    _write(user.id, data)
    user.avatar_source = source
    user.avatar_content_type = content_type
    user.avatar_updated_at = datetime.now(UTC)
    db.session.commit()
    metrics.avatar_actions_total.labels("uploaded" if source == "upload" else source).inc()


def remove(user: User) -> bool:
    """Drops the user's image. False if they didn't have one."""
    if not user.has_avatar:
        return False
    user.avatar_source = "none"
    user.avatar_content_type = None
    user.avatar_updated_at = datetime.now(UTC)
    db.session.commit()
    _path(user.id).unlink(missing_ok=True)
    metrics.avatar_actions_total.labels("removed").inc()
    return True


def gravatar_url(email: str) -> str:
    """Where Gravatar serves this address's image; d=404 asks for an error, not a stock
    picture, when it has none. Gravatar keys on the SHA-256 of the trimmed, lower-cased email."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    base = current_app.config["GRAVATAR_URL"]
    return f"{base}{digest}?s={GRAVATAR_SIZE}&d=404"


def _fetch_gravatar(email: str) -> bytes:
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(gravatar_url(email), timeout=GRAVATAR_TIMEOUT) as response:
            data = response.read(GRAVATAR_MAX_BYTES + 1)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            metrics.avatar_gravatar_fetches_total.labels("none").inc()
            raise GravatarMissing from err
        metrics.avatar_gravatar_fetches_total.labels("error").inc()
        raise GravatarUnavailable(str(err)) from err
    except (urllib.error.URLError, TimeoutError) as err:
        metrics.avatar_gravatar_fetches_total.labels("error").inc()
        raise GravatarUnavailable(str(err)) from err
    finally:
        metrics.avatar_gravatar_seconds.observe(time.perf_counter() - start)
    if len(data) > GRAVATAR_MAX_BYTES:
        metrics.avatar_gravatar_fetches_total.labels("too_large").inc()
        raise GravatarUnavailable("image too large")
    return data


def use_gravatar(user: User) -> None:
    """Fetch the Gravatar for the user's email and make it their image (also how it's
    refreshed). Raises GravatarDisabled, GravatarMissing or GravatarUnavailable, leaving
    any current image alone. The bytes are kept on disk, so browsers never talk to Gravatar."""
    if not current_app.config["AVATAR_GRAVATAR_ENABLED"]:
        raise GravatarDisabled
    data = _fetch_gravatar(user.email)
    try:
        content_type = sniff_image_type(data)
    except UnsupportedImage as err:
        metrics.avatar_gravatar_fetches_total.labels("bad_type").inc()
        raise GravatarUnavailable("not an image") from err
    save(user, data, content_type, "gravatar")
    metrics.avatar_gravatar_fetches_total.labels("ok").inc()
