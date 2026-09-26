"""WebFinger (RFC 7033): resolving acct:user@domain to an actor."""

import re

from scrobbler.federation.protocol.media import ACTIVITY_JSON

_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class ResourceError(ValueError):
    """The resource isn't something this server answers for (400, not 404)."""


def parse_resource(resource: str | None, *, domain: str, base_url: str) -> str:
    """The username in `acct:user@domain` (optionally with a leading @), or in an actor URL
    `<base_url>/users/<user>`. Raises ResourceError for anything else."""
    value = (resource or "").strip()
    if value.lower().startswith(f"{base_url.lower()}/users/"):
        username = value[len(base_url) + len("/users/") :]
    else:
        if value.lower().startswith("acct:"):
            value = value[5:]
        value = value.lstrip("@")
        username, at, host = value.rpartition("@")
        if not at or host.lower() != domain.lower():
            raise ResourceError("resource must be acct:user@" + domain)
    if not _USERNAME.match(username):
        raise ResourceError("invalid username")
    return username


def jrd(*, username: str, domain: str, actor_id: str, profile_page: str) -> dict:
    return {
        "subject": f"acct:{username}@{domain}",
        "aliases": [actor_id, profile_page],
        "links": [
            {"rel": "self", "type": ACTIVITY_JSON, "href": actor_id},
            {
                "rel": "http://webfinger.net/rel/profile-page",
                "type": "text/html",
                "href": profile_page,
            },
        ],
    }
