import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class FederationConfigError(ValueError):
    pass


def _flag(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in value.split(",") if part.strip())


KNOWN_SCHEMES = ("draft-cavage", "rfc9421")


@dataclass(frozen=True)
class FederationConfig:
    enabled: bool = False
    domain: str = ""  # the handle's domain: @user@<domain>
    base_url: str = ""  # where actors live: <base_url>/users/<username>
    key_secret: str = ""  # encrypts actors' private keys at rest
    signature_schemes: tuple[str, ...] = KNOWN_SCHEMES  # outgoing order; next on a 401
    blocked_domains: tuple[str, ...] = field(default=())
    delivery_concurrency: int = 4
    # TESTS ONLY: allow http:// and private addresses so CI can federate on a private
    # network. Refused unless the domain is under the reserved .test TLD.
    insecure_testing: bool = False

    @classmethod
    def from_mapping(cls, config: Mapping) -> "FederationConfig":
        """Read FEDERATION_* from the Flask config, falling back to the environment."""

        def get(key: str, default: str = "") -> str:
            value = config.get(key)
            return str(value if value is not None else os.environ.get(key, default))

        try:
            concurrency = int(get("FEDERATION_DELIVERY_CONCURRENCY", "4"))
        except ValueError:
            raise FederationConfigError(
                "FEDERATION_DELIVERY_CONCURRENCY must be a number"
            ) from None
        result = cls(
            enabled=_flag(get("FEDERATION_ENABLED", "0")),
            domain=get("FEDERATION_DOMAIN").strip().lower(),
            base_url=get("FEDERATION_BASE_URL").strip().rstrip("/"),
            key_secret=get("FEDERATION_KEY_SECRET"),
            signature_schemes=_csv(get("FEDERATION_SIGNATURE_SCHEMES", ",".join(KNOWN_SCHEMES))),
            blocked_domains=_csv(get("FEDERATION_BLOCKED_DOMAINS")),
            delivery_concurrency=concurrency,
            insecure_testing=_flag(get("FEDERATION_INSECURE_TESTING", "0")),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.domain or "/" in self.domain or ":" in self.domain:
            raise FederationConfigError(
                "FEDERATION_DOMAIN must be a bare host name (e.g. scrobble.example) when "
                "FEDERATION_ENABLED is on. It becomes part of every handle and can't change "
                "once people follow."
            )
        if self.insecure_testing and not self.domain.endswith(".test"):
            raise FederationConfigError(
                "FEDERATION_INSECURE_TESTING is for automated tests only, and only allowed "
                "with a FEDERATION_DOMAIN under .test."
            )
        url = urlsplit(self.base_url)
        schemes = ("https", "http") if self.insecure_testing else ("https",)
        if url.scheme not in schemes or not url.netloc or url.query or url.fragment:
            raise FederationConfigError(
                "FEDERATION_BASE_URL must be the public https:// URL the API is served at "
                "(e.g. https://scrobble.example) when FEDERATION_ENABLED is on."
            )
        if len(self.key_secret) < 32:
            raise FederationConfigError(
                "FEDERATION_KEY_SECRET must be at least 32 characters when FEDERATION_ENABLED "
                "is on. It encrypts actors' private keys; keep it safe and never change it "
                "(e.g. python -c 'import secrets; print(secrets.token_urlsafe(48))')."
            )
        unknown = set(self.signature_schemes) - set(KNOWN_SCHEMES)
        if not self.signature_schemes or unknown:
            raise FederationConfigError(
                f"FEDERATION_SIGNATURE_SCHEMES must list some of {', '.join(KNOWN_SCHEMES)}"
            )
        if not 1 <= self.delivery_concurrency <= 32:
            raise FederationConfigError("FEDERATION_DELIVERY_CONCURRENCY must be 1-32")

    def is_blocked(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        return any(host == d or host.endswith("." + d) for d in self.blocked_domains)

    def __repr__(self) -> str:  # never print the secret
        return (
            f"FederationConfig(enabled={self.enabled}, domain={self.domain!r}, "
            f"base_url={self.base_url!r}, key_secret=***, "
            f"insecure_testing={self.insecure_testing})"
        )
