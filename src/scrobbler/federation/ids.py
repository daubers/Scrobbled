"""Our ActivityPub IDs, all built from FEDERATION_BASE_URL (never the request)."""

from flask import current_app


def base_url() -> str:
    return current_app.extensions["federation"].base_url


def actor_id(username: str) -> str:
    return f"{base_url()}/users/{username}"


def key_id(username: str) -> str:
    return f"{actor_id(username)}#main-key"


def instance_actor_id() -> str:
    return f"{base_url()}/actor"


def instance_key_id() -> str:
    return f"{instance_actor_id()}#main-key"


def activity_url(activity_uuid: str) -> str:
    return f"{base_url()}/activities/{activity_uuid}"


def username_from_actor_id(uri: str | None) -> str | None:
    """`<base>/users/<name>` -> name; None for anything else."""
    prefix = f"{base_url()}/users/"
    if not uri or not uri.startswith(prefix):
        return None
    name = uri[len(prefix) :]
    return name if name and "/" not in name and "#" not in name and "?" not in name else None
