"""Shared pieces of the HTTP signature schemes."""

import base64
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

MAX_SKEW = timedelta(hours=12)  # Mastodon's window


class SignatureError(Exception):
    """Why a signature couldn't be verified: a short, metric-friendly reason."""

    REASONS = (
        "missing",
        "malformed",
        "expired",
        "digest_mismatch",
        "bad_signature",
        "unsupported",
        "unknown_key",
        "insufficient",  # doesn't cover what we require
    )

    def __init__(self, reason: str, detail: str = ""):
        assert reason in self.REASONS, reason
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class SignedRequest:
    """An HTTP request, as signed or as received."""

    method: str
    url: str  # absolute, as the signer addressed it
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str) -> str | None:
        name = name.lower()
        for key, value in self.headers.items():
            if key.lower() == name:
                return value
        return None

    @property
    def path_and_query(self) -> str:
        parts = urlsplit(self.url)
        return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc.lower()


@dataclass(frozen=True)
class VerifiedSignature:
    scheme: str
    key_id: str


# key id -> the public key it names (None if it can't be found)
PublicKeyFor = Callable[[str], RSAPublicKey | None]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as err:
        raise SignatureError("malformed", "bad base64") from err


def sha256(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


def check_time(when: datetime, now: datetime, max_skew: timedelta = MAX_SKEW) -> None:
    if abs(now - when) > max_skew:
        raise SignatureError("expired", f"signed at {when.isoformat()}")


class SignatureScheme:
    """Interface each scheme implements."""

    name: str

    def present(self, request: SignedRequest) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def sign(
        self, request: SignedRequest, key_id: str, private_key: RSAPrivateKey, now: datetime
    ) -> dict[str, str]:  # pragma: no cover - interface
        """Headers to add to the request."""
        raise NotImplementedError

    def verify(
        self, request: SignedRequest, public_key_for: PublicKeyFor, now: datetime
    ) -> VerifiedSignature:  # pragma: no cover - interface
        raise NotImplementedError
