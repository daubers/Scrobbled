"""Profile images.

The bytes live on disk, in an "avatars" folder inside ART_STORAGE_DIR (so they share its
volume and its backups), one file per user named by user id with no extension. The users
row records where the image came from and its content type; that is the source of truth
for whether there is one.
"""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from flask import current_app

from scrobbler import metrics
from scrobbler.extensions import db
from scrobbler.models import User
from scrobbler.services.art import IMAGE_TYPES, UnsupportedImage, sniff_image_type

__all__ = ["IMAGE_TYPES", "UnsupportedImage", "read", "remove", "save", "sniff_image_type"]


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
