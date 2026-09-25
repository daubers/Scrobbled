"""HTTP signatures for ActivityPub: one module per scheme, chosen by name.

    verify(request, public_key_for)      whichever scheme the request carries
    sign(request, scheme, key_id, key)   headers to add, using the named scheme

Adding or dropping a scheme is a module here plus an entry in SCHEMES.
"""

from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from scrobbler.federation.protocol.signatures.base import (
    PublicKeyFor,
    SignatureError,
    SignatureScheme,
    SignedRequest,
    VerifiedSignature,
)
from scrobbler.federation.protocol.signatures.draft_cavage import DraftCavage
from scrobbler.federation.protocol.signatures.rfc9421 import Rfc9421

SCHEMES: dict[str, SignatureScheme] = {s.name: s for s in (Rfc9421(), DraftCavage())}

__all__ = [
    "SCHEMES",
    "PublicKeyFor",
    "SignatureError",
    "SignedRequest",
    "VerifiedSignature",
    "detect",
    "sign",
    "verify",
]


def detect(request: SignedRequest) -> SignatureScheme:
    for scheme in SCHEMES.values():  # RFC 9421 first: its Signature header differs
        if scheme.present(request):
            return scheme
    raise SignatureError("missing")


def verify(
    request: SignedRequest, public_key_for: PublicKeyFor, now: datetime | None = None
) -> VerifiedSignature:
    return detect(request).verify(request, public_key_for, now or datetime.now(UTC))


def sign(
    request: SignedRequest,
    scheme: str,
    key_id: str,
    private_key: RSAPrivateKey,
    now: datetime | None = None,
) -> dict[str, str]:
    try:
        chosen = SCHEMES[scheme]
    except KeyError:
        raise SignatureError("unsupported", f"scheme {scheme}") from None
    return chosen.sign(request, key_id, private_key, now or datetime.now(UTC))
