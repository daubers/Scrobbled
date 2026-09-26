"""Actor key pairs: RSA-2048 (what Mastodon's draft signatures need), private keys
encrypted at rest with a key derived from FEDERATION_KEY_SECRET.

Keys are created once and never regenerated: remote servers pin them.
"""

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app

from scrobbler.extensions import db
from scrobbler.federation.models import FederationKey

_HKDF_INFO = b"scrobbler federation actor keys v1"


def _fernet(secret: str) -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        secret.encode()
    )
    return Fernet(base64.urlsafe_b64encode(key))


def _secret() -> str:
    return current_app.extensions["federation"].key_secret


def _new_key(user_id: int | None) -> FederationKey:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return FederationKey(
        user_id=user_id,
        public_key_pem=public_pem.decode(),
        private_key_encrypted=_fernet(_secret()).encrypt(private_pem),
    )


def key_for(user_id: int | None) -> FederationKey:
    """The actor's key pair, created on first use. user_id=None is the instance actor."""
    query = db.select(FederationKey)
    query = query.filter(
        FederationKey.user_id.is_(None) if user_id is None else FederationKey.user_id == user_id
    )
    key = db.session.scalar(query)
    if key is None:
        key = _new_key(user_id)
        db.session.add(key)
        db.session.commit()
    return key


def private_key(key: FederationKey) -> RSAPrivateKey:
    pem = _fernet(_secret()).decrypt(key.private_key_encrypted)
    return serialization.load_pem_private_key(pem, password=None)
