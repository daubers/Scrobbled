"""RFC 9421 HTTP Message Signatures, as Mastodon 4.5+ accepts and 4.7+ sends them.

Signature-Input: sig1=("@method" "@target-uri" "content-digest");created=…;keyid="…"
Signature: sig1=:<base64>:
Content-Digest: sha-256=:<base64>:

Includes the small part of RFC 8941 (structured fields) these headers use.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

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
    unb64,
)

NAME = "rfc9421"
ALGORITHM = "rsa-v1_5-sha256"
LABEL = "sig1"

# --- RFC 8941 structured fields (the subset used here) --------------------------------


@dataclass(frozen=True)
class Item:
    value: object  # str (string or token), int, bool or bytes
    params: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class InnerList:
    items: tuple[Item, ...]
    params: tuple[tuple[str, object], ...] = ()


class _Parser:
    def __init__(self, text: str):
        self.text, self.pos = text, 0

    def error(self, message: str):
        return SignatureError("malformed", f"{message} at {self.pos}")

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def skip(self, chars: str = " \t") -> None:
        while self.peek() and self.peek() in chars:
            self.pos += 1

    def key(self) -> str:
        start = self.pos
        if not (self.peek().islower() or self.peek() == "*"):
            raise self.error("expected a key")
        while self.peek() and (
            self.peek().islower() or self.peek().isdigit() or self.peek() in "_-.*"
        ):
            self.pos += 1
        return self.text[start : self.pos]

    def bare_item(self) -> object:
        char = self.peek()
        if char == '"':
            self.pos += 1
            out = []
            while True:
                char = self.peek()
                if not char:
                    raise self.error("unterminated string")
                self.pos += 1
                if char == "\\":
                    out.append(self.peek())
                    self.pos += 1
                elif char == '"':
                    return "".join(out)
                else:
                    out.append(char)
        if char == ":":
            end = self.text.find(":", self.pos + 1)
            if end < 0:
                raise self.error("unterminated byte sequence")
            value = unb64(self.text[self.pos + 1 : end])
            self.pos = end + 1
            return value
        if char == "?":
            self.pos += 2
            return self.text[self.pos - 1] == "1"
        if char == "-" or char.isdigit():
            start = self.pos
            self.pos += 1
            while self.peek().isdigit():
                self.pos += 1
            return int(self.text[start : self.pos])
        if char.isalpha() or char == "*":  # token
            start = self.pos
            while self.peek() and self.peek() not in ' \t,;()"=':
                self.pos += 1
            return self.text[start : self.pos]
        raise self.error("unexpected character")

    def params(self) -> tuple[tuple[str, object], ...]:
        out = []
        while self.peek() == ";":
            self.pos += 1
            self.skip(" ")
            name = self.key()
            value: object = True
            if self.peek() == "=":
                self.pos += 1
                value = self.bare_item()
            out.append((name, value))
        return tuple(out)

    def item_or_inner_list(self) -> Item | InnerList:
        if self.peek() == "(":
            self.pos += 1
            items = []
            while True:
                self.skip(" ")
                if self.peek() == ")":
                    self.pos += 1
                    return InnerList(tuple(items), self.params())
                items.append(Item(self.bare_item(), self.params()))
                if self.peek() not in " )":
                    raise self.error("bad inner list")
        return Item(self.bare_item(), self.params())

    def dictionary(self) -> dict[str, Item | InnerList]:
        out: dict[str, Item | InnerList] = {}
        self.skip()
        while self.peek():
            name = self.key()
            if self.peek() == "=":
                self.pos += 1
                out[name] = self.item_or_inner_list()
            else:
                out[name] = Item(True, self.params())
            self.skip()
            if not self.peek():
                break
            if self.peek() != ",":
                raise self.error("expected ','")
            self.pos += 1
            self.skip()
        return out


def parse_dictionary(text: str) -> dict[str, Item | InnerList]:
    return _Parser(text).dictionary()


def _serialize_bare(value: object) -> str:
    if isinstance(value, bool):
        return "?1" if value else "?0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, bytes):
        return f":{b64(value)}:"
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _serialize_params(params) -> str:
    return "".join(
        f";{name}" if value is True else f";{name}={_serialize_bare(value)}"
        for name, value in params
    )


def serialize_inner_list(inner: InnerList) -> str:
    items = " ".join(_serialize_bare(i.value) + _serialize_params(i.params) for i in inner.items)
    return f"({items}){_serialize_params(inner.params)}"


# --- Signature base (RFC 9421 §2.5) ------------------------------------------------------


def _component_value(request: SignedRequest, item: Item) -> str:
    name = str(item.value)
    if item.params:
        raise SignatureError("unsupported", f"component parameters on {name}")
    parts = urlsplit(request.url)
    if name == "@method":
        return request.method.upper()
    if name == "@target-uri":
        return request.url
    if name == "@authority":
        return parts.netloc.lower()
    if name == "@scheme":
        return parts.scheme.lower()
    if name == "@request-target":
        return request.path_and_query
    if name == "@path":
        return parts.path or "/"
    if name == "@query":
        return f"?{parts.query}"
    if name.startswith("@"):
        raise SignatureError("unsupported", f"component {name}")
    value = request.header(name)
    if value is None:
        raise SignatureError("malformed", f"covered header {name} is missing")
    return value.strip()


def signature_base(request: SignedRequest, inner: InnerList) -> str:
    lines = [f'"{item.value}": {_component_value(request, item)}' for item in inner.items]
    lines.append(f'"@signature-params": {serialize_inner_list(inner)}')
    return "\n".join(lines)


# --- Content-Digest (RFC 9530) -------------------------------------------------------------

_DIGESTS = {"sha-256": hashlib.sha256, "sha-512": hashlib.sha512}


def content_digest(body: bytes) -> str:
    return f"sha-256=:{b64(hashlib.sha256(body).digest())}:"


def check_content_digest(request: SignedRequest) -> None:
    header = request.header("content-digest")
    if not header:
        raise SignatureError("insufficient", "no Content-Digest header")
    for name, member in parse_dictionary(header).items():
        if name in _DIGESTS and isinstance(member, Item) and isinstance(member.value, bytes):
            if member.value != _DIGESTS[name](request.body).digest():
                raise SignatureError("digest_mismatch")
            return
    raise SignatureError("unsupported", "no sha-256 or sha-512 Content-Digest")


# --- The scheme ------------------------------------------------------------------------------


class Rfc9421(SignatureScheme):
    name = NAME

    def present(self, request: SignedRequest) -> bool:
        return request.header("signature-input") is not None

    def sign(
        self, request: SignedRequest, key_id: str, private_key: RSAPrivateKey, now: datetime
    ) -> dict[str, str]:
        added: dict[str, str] = {}
        components = ["@method", "@target-uri"]
        if request.body or request.method.upper() == "POST":
            added["Content-Digest"] = content_digest(request.body)
            components.append("content-digest")
        inner = InnerList(
            tuple(Item(c) for c in components),
            (("created", int(now.timestamp())), ("keyid", key_id), ("alg", ALGORITHM)),
        )
        signing = SignedRequest(
            request.method, request.url, {**request.headers, **added}, request.body
        )
        base = signature_base(signing, inner)
        signature = private_key.sign(base.encode(), padding.PKCS1v15(), hashes.SHA256())
        added["Signature-Input"] = f"{LABEL}={serialize_inner_list(inner)}"
        added["Signature"] = f"{LABEL}=:{b64(signature)}:"
        return added

    def verify(
        self,
        request: SignedRequest,
        public_key_for: PublicKeyFor,
        now: datetime,
        require: tuple[str, ...] | None = None,
    ) -> VerifiedSignature:
        """Verify the (first) signature. `require` lists components that must be covered;
        by default the ActivityPub minimum: method, target and, with a body, the digest."""
        raw_input, raw_signature = request.header("signature-input"), request.header("signature")
        if not raw_input or not raw_signature:
            raise SignatureError("missing")
        inputs, signatures = parse_dictionary(raw_input), parse_dictionary(raw_signature)
        label = next((name for name in inputs if name in signatures), None)
        if label is None:
            raise SignatureError("malformed", "no Signature for any Signature-Input label")
        inner, value = inputs[label], signatures[label]
        if (
            not isinstance(inner, InnerList)
            or not isinstance(value, Item)
            or not isinstance(value.value, bytes)
        ):
            raise SignatureError("malformed")
        params = dict(inner.params)
        covered = {str(item.value) for item in inner.items}

        if "keyid" not in params:
            raise SignatureError("malformed", "no keyid")
        if params.get("alg", ALGORITHM) != ALGORITHM:
            raise SignatureError("unsupported", f"alg {params['alg']}")
        if require is None:
            require = ("@method", "@target-uri") + (("content-digest",) if request.body else ())
            if "created" not in params:
                raise SignatureError("insufficient", "no created time")
        missing = set(require) - covered
        if missing:
            raise SignatureError("insufficient", f"not covered: {sorted(missing)}")
        if "created" in params:
            check_time(datetime.fromtimestamp(int(params["created"]), UTC), now)
        if "expires" in params and now.timestamp() > int(params["expires"]):
            raise SignatureError("expired", "past expires")
        if "content-digest" in covered:
            check_content_digest(request)

        key_id = str(params["keyid"])
        key = public_key_for(key_id)
        if key is None:
            raise SignatureError("unknown_key", key_id)
        base = signature_base(request, inner)
        try:
            key.verify(value.value, base.encode(), padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as err:
            raise SignatureError("bad_signature") from err
        return VerifiedSignature(NAME, key_id)
