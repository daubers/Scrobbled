"""Publishing posts: building and storing the Note/Create, fanning out to accepted
followers, and deleting.

Each post kind (weekly.py, milestones.py, and later now-playing) decides *when* to post
and builds the text; this module handles what every post shares: idempotence (one row
per (user, key), enforced by federation_posts' unique constraint), addressing from the
user's current visibility, storage, delivery and deletion.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from scrobbler.extensions import db
from scrobbler.federation import activities, followers, ids, sharing
from scrobbler.federation.models import FederationActivity, FederationPost
from scrobbler.federation.protocol import addressing, vocab

DEFAULT_TAGS = ("Scrobbler",)


def _now() -> datetime:
    return datetime.now(UTC)


def has_post(user_id: int, kind: str, key: str) -> bool:
    """Whether a post with this (user, kind, key) already exists (posted or since
    deleted). Lets a caller skip expensive work — computing stats, say — before deciding
    whether to call publish() at all."""
    full_key = f"{kind}:{key}"
    return (
        db.session.scalar(db.select(FederationPost.id).filter_by(user_id=user_id, key=full_key))
        is not None
    )


def publish(
    user_id: int,
    username: str,
    kind: str,
    key: str,
    *,
    text: str,
    html: str,
    tags: tuple[str, ...] = DEFAULT_TAGS,
) -> FederationPost | None:
    """Post `text`/`html` for the user, keyed by `kind:key`.

    Idempotent: a second call with the same (user, kind, key) is a no-op, including when
    two worker passes race each other (caught as a duplicate-key error, not raised).
    Returns None, without posting, if the user isn't sharing or the key already exists.
    """
    settings = sharing.settings_for(user_id)
    if not settings.enabled:
        return None
    full_key = f"{kind}:{key}"
    already = db.session.scalar(
        db.select(FederationPost.id).filter_by(user_id=user_id, key=full_key)
    )
    if already:
        return None

    published = _now().isoformat().replace("+00:00", "Z")
    post_uuid = str(uuid.uuid4())
    to, cc = addressing.address(settings.visibility, ids.followers_url(username))
    note_doc = vocab.note(
        ids.note_url(username, post_uuid),
        actor_id=ids.actor_id(username),
        content_html=html,
        published=published,
        url=ids.post_page_url(username, post_uuid),
        to=to,
        cc=cc,
        tags=list(tags),
    )
    # activity.id is a Python-generated UUID (see activities.store), so it's already
    # usable below without a flush.
    activity = activities.store(
        user_id,
        lambda url: vocab.create(url, ids.actor_id(username), note_doc, to=to, cc=cc),
        public=addressing.is_public_or_unlisted(settings.visibility),
    )
    post = FederationPost(
        user_id=user_id,
        kind=kind,
        key=full_key,
        activity_id=activity.id,
        visibility=settings.visibility,
        content_text=text,
        content_html=html,
    )
    db.session.add(post)
    inboxes = followers.accepted_inboxes(user_id)
    if inboxes:
        activities.queue(activity, inboxes)
    try:
        db.session.commit()
    except IntegrityError:  # posted with this key by a concurrent pass
        db.session.rollback()
        return None
    return post


def delete_post(user_id: int, username: str, post: FederationPost) -> FederationActivity | None:
    """Delete one post: sends Delete to the same inboxes the Create went to, and
    tombstones the row (kept, so its key can never be reused). The caller commits.
    None if it's already deleted."""
    if post.deleted_at is not None:
        return None
    return _delete(user_id, username, post)


def delete_all_for(user_id: int, username: str) -> int:
    """Sharing switched off: delete every live post before the Rejects go out (call this
    before followers.reject_all — it needs accepted_inboxes() to still have somewhere to
    send the Deletes). The caller commits. Returns how many were deleted."""
    posts = db.session.scalars(
        db.select(FederationPost).filter_by(user_id=user_id, deleted_at=None)
    ).all()
    for post in posts:
        _delete(user_id, username, post)
    return len(posts)


def _delete(user_id: int, username: str, post: FederationPost) -> FederationActivity:
    # The post's own visibility at the time it was posted, not the user's current
    # setting: a Delete has to reach whoever could have seen the Create.
    note_id = post.activity.document["object"]["id"]
    to, cc = addressing.address(post.visibility, ids.followers_url(username))
    activity = activities.store(
        user_id,
        lambda url: vocab.delete(url, ids.actor_id(username), note_id, to=to, cc=cc),
        public=addressing.is_public_or_unlisted(post.visibility),
    )
    inboxes = followers.accepted_inboxes(user_id)
    if inboxes:
        activities.queue(activity, inboxes)
    post.deleted_at = _now()
    return activity
