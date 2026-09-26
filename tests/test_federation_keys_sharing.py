import pytest
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from scrobbler.extensions import db
from scrobbler.federation import keys, sharing
from scrobbler.federation.models import FederationKey


def test_defaults_are_private(fed_ctx, user):
    settings = sharing.settings_for(user.id)
    assert (settings.enabled, settings.visibility) == (False, "followers")
    assert not (settings.discoverable or settings.indexable or settings.manually_approves_followers)
    assert sharing.shared_user("alice") is None


def test_unsaved_defaults_have_every_field_populated(fed_ctx, user):
    """settings_for() builds a transient object when a user has never saved settings; its
    column defaults apply only on INSERT, so every field has to be set by hand."""
    settings = sharing.settings_for(user.id)
    assert (settings.timezone, settings.post_weekly_summary, settings.post_milestones) == (
        "UTC",
        True,
        True,
    )


def test_enabling_creates_a_key_pair_that_signs(fed_ctx, user):
    _, toggled = sharing.update(user.id, {"enabled": True})
    assert toggled is True
    key = db.session.scalar(db.select(FederationKey).filter_by(user_id=user.id))
    assert key.public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")
    assert b"PRIVATE KEY" not in key.private_key_encrypted  # encrypted at rest

    message = b"hello fediverse"
    signature = keys.private_key(key).sign(message, padding.PKCS1v15(), hashes.SHA256())
    public = serialization.load_pem_public_key(key.public_key_pem.encode())
    public.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())  # no exception


def test_keys_survive_disable_and_enable(fed_ctx, user):
    sharing.update(user.id, {"enabled": True})
    first = keys.key_for(user.id).public_key_pem
    assert sharing.update(user.id, {"enabled": False})[1] is False
    assert sharing.update(user.id, {"enabled": True})[1] is True
    assert keys.key_for(user.id).public_key_pem == first


def test_the_wrong_secret_cannot_decrypt(fed_ctx, fed_app, user):
    sharing.update(user.id, {"enabled": True})
    key = keys.key_for(user.id)
    real = fed_app.extensions["federation"]
    fed_app.extensions["federation"] = type(real)(
        enabled=True, domain=real.domain, base_url=real.base_url, key_secret="y" * 40
    )
    try:
        with pytest.raises(InvalidToken):
            keys.private_key(key)
    finally:
        fed_app.extensions["federation"] = real


def test_instance_actor_key(fed_ctx):
    assert keys.key_for(None).user_id is None
    assert keys.key_for(None).id == keys.key_for(None).id  # created once


def test_shared_user_is_case_insensitive_and_requires_sharing(fed_ctx, user, make_user):
    sharing.update(user.id, {"enabled": True})
    make_user(username="bob")  # exists, not sharing
    assert sharing.shared_user("ALICE")[0].id == user.id
    assert sharing.shared_user("bob") is None
    assert sharing.shared_user("nobody") is None
    assert sharing.sharing_count() == 1


@pytest.mark.parametrize(
    "changes", [{"visibility": "everyone"}, {"user_id": 99}, {"enabled": True, "handle": "x"}]
)
def test_invalid_changes_are_rejected(fed_ctx, user, changes):
    with pytest.raises(ValueError):
        sharing.update(user.id, changes)
