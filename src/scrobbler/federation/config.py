import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


class FederationConfigError(ValueError):
    pass


def _flag(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class FederationConfig:
    enabled: bool = False
    domain: str = ""  # the handle's domain: @user@<domain>
    base_url: str = ""  # where actors live: <base_url>/users/<username>

    @classmethod
    def from_mapping(cls, config: Mapping) -> "FederationConfig":
        """Read FEDERATION_* from the Flask config, falling back to the environment."""

        def get(key: str, default: str = "") -> str:
            value = config.get(key)
            return str(value if value is not None else os.environ.get(key, default))

        result = cls(
            enabled=_flag(get("FEDERATION_ENABLED", "0")),
            domain=get("FEDERATION_DOMAIN").strip().lower(),
            base_url=get("FEDERATION_BASE_URL").strip().rstrip("/"),
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
        url = urlsplit(self.base_url)
        if url.scheme != "https" or not url.netloc or url.query or url.fragment:
            raise FederationConfigError(
                "FEDERATION_BASE_URL must be the public https:// URL the API is served at "
                "(e.g. https://api.scrobble.example) when FEDERATION_ENABLED is on."
            )
