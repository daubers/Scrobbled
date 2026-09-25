"""draft-cavage-http-signatures (version 12): what most of the fediverse still uses.

Signature: keyId="…",algorithm="rsa-sha256",headers="(request-target) host date digest",
           signature="<base64>"
"""

import re
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from scrobbler.federation.protocol.signatures.base import (
    PublicKeyFor,
    SignatureError,
    SignatureScheme,
    SignedRequest,
    VerifiedSignature,
    b64,
    check_time,
    sha256,
    unb64,
)

NAME = "draft-cavage"
_PARAM = re.compile(r'\s*([a-zA-Z]+)\s*=\s*"([^"]*)"\s*(?:,|$)')
_ALGORITHMS = {"rsa-sha256", "hs2019"}  # hs2019: "derive from the key"; ours are RSA
SIGN_GET = ("(request-target)", "host", "date")
SIGN_POST = ("(request-target)", "host", "date", "digest")


def _signature_header(request: SignedRequest) -> str | None:
    value = request.header("signature")
    if value:
        return value
    authorization = request.header("authorization") or ""
    if authorization[:10].lower() == "signature ":
        return authorization[10:]
    return None


def parse(value: str) -> dict[str, str]:
    params, position = {}, 0
    while position < len(value):
        match = _PARAM.match(value, position)
        if not match or match.end() == position:
            raise SignatureError("malformed", "unparseable Signature header")
        params[match.group(1)] = match.group(2)
        position = match.end()
    if "keyId" not in params or "signature" not in params:
        raise SignatureError("malformed", "keyId and signature are required")
    return params


def signing_string(request: SignedRequest, headers: list[str], params: dict[str, str]) -> str:
    lines = []
    for name in headers:
        if name == "(request-target)":
            value = f"{request.method.lower()} {request.path_and_query}"
        elif name in ("(created)", "(expires)"):
            value = params.get(name[1:-1])
            if value is None:
                raise SignatureError("malformed", f"{name} signed but not given")
        else:
            value = request.header(name)
            if value is None:
                raise SignatureError("malformed", f"signed header {name} is missing")
        lines.append(f"{name}: {value.strip()}")
    return "\n".join(lines)


def digest_header(body: bytes) -> str:
    return f"SHA-256={b64(sha256(body))}"


def check_digest(request: SignedRequest) -> None:
    header = request.header("digest")
    if not header:
        raise SignatureError("insufficient", "no Digest header")
    for part in header.split(","):
        algorithm, _, value = part.strip().partition("=")
        if algorithm.lower() == "sha-256":
            if unb64(value) != sha256(request.body):
                raise SignatureError("digest_mismatch")
            return
    raise SignatureError("unsupported", "no SHA-256 digest")


class DraftCavage(SignatureScheme):
    name = NAME

    def present(self, request: SignedRequest) -> bool:
        return _signature_header(request) is not None and request.header("signature-input") is None

    def sign(
        self, request: SignedRequest, key_id: str, private_key: RSAPrivateKey, now: datetime
    ) -> dict[str, str]:
        added = {"Host": request.host, "Date": format_datetime(now.astimezone(UTC), usegmt=True)}
        headers = SIGN_GET
        if request.body or request.method.upper() == "POST":
            added["Digest"] = digest_header(request.body)
            headers = SIGN_POST
        signing = SignedRequest(
            request.method, request.url, {**request.headers, **added}, request.body
        )
        base = signing_string(signing, list(headers), {})
        signature = private_key.sign(base.encode(), padding.PKCS1v15(), hashes.SHA256())
        added["Signature"] = (
            f'keyId="{key_id}",algorithm="rsa-sha256",headers="{" ".join(headers)}",'
            f'signature="{b64(signature)}"'
        )
        return added

    def verify(
        self,
        request: SignedRequest,
        public_key_for: PublicKeyFor,
        now: datetime,
        require: tuple[str, ...] | None = None,
    ) -> VerifiedSignature:
        """Verify the signature. `require` lists the headers that must be covered;
        by default the ActivityPub minimum for the request's method."""
        header = _signature_header(request)
        if header is None:
            raise SignatureError("missing")
        params = parse(header)
        algorithm = params.get("algorithm", "hs2019").lower()
        if algorithm not in _ALGORITHMS:
            raise SignatureError("unsupported", f"algorithm {algorithm}")
        headers = params.get("headers", "date").lower().split()

        if require is None:
            require = SIGN_POST if request.method.upper() == "POST" else SIGN_GET
        missing = set(require) - set(headers)
        if missing:
            raise SignatureError("insufficient", f"not signed: {sorted(missing)}")
        if "digest" in headers:
            check_digest(request)
        if "date" in headers:
            try:
                check_time(parsedate_to_datetime(request.header("date") or ""), now)
            except (TypeError, ValueError) as err:
                raise SignatureError("malformed", "bad Date header") from err

        key = public_key_for(params["keyId"])
        if key is None:
            raise SignatureError("unknown_key", params["keyId"])
        base = signing_string(request, headers, params)
        try:
            key.verify(
                unb64(params["signature"]), base.encode(), padding.PKCS1v15(), hashes.SHA256()
            )
        except InvalidSignature as err:
            raise SignatureError("bad_signature") from err
        return VerifiedSignature(NAME, params["keyId"])
