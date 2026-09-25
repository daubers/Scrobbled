"""Last.fm request signatures: https://www.last.fm/api/authspec#_8-signing-calls"""

import hashlib
import hmac
from collections.abc import Mapping

UNSIGNED_PARAMS = frozenset({"format", "callback", "api_sig"})


def sign(params: Mapping[str, str], secret: str) -> str:
    payload = "".join(
        f"{name}{params[name]}" for name in sorted(params) if name not in UNSIGNED_PARAMS
    )
    return hashlib.md5((payload + secret).encode("utf-8")).hexdigest()


def is_valid(params: Mapping[str, str], secret: str) -> bool:
    supplied = params.get("api_sig", "")
    return bool(supplied) and hmac.compare_digest(sign(params, secret), supplied.lower())
