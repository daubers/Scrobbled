"""Signed requests to other servers, through the SSRF-safe client in net.py."""

import json

from flask import current_app

from scrobbler.federation import ids, keys
from scrobbler.federation.net import Client, FetchError, Response
from scrobbler.federation.protocol import signatures
from scrobbler.federation.protocol.media import ACTIVITY_JSON, LD_JSON
from scrobbler.federation.protocol.signatures import SignedRequest


def client() -> Client:
    """One pooled client per app (created on first use)."""
    existing = current_app.extensions.get("federation_client")
    if existing is None:
        from scrobbler import __version__

        config = current_app.extensions["federation"]
        existing = Client(
            user_agent=f"Scrobbler/{__version__} (+{config.base_url})",
            insecure_testing=config.insecure_testing,
        )
        current_app.extensions["federation_client"] = existing
    return existing


def _signed_headers(method, url, headers, body, key_id, private_key, scheme) -> dict[str, str]:
    request = SignedRequest(method, url, headers, body)
    return {**headers, **signatures.sign(request, scheme, key_id, private_key)}


def signed_get_json(url: str) -> dict:
    """GET an ActivityPub document, signed as the instance actor, so servers using
    'authorized fetch' answer too."""
    private = keys.private_key(keys.key_for(None))
    headers = {"Accept": f"{ACTIVITY_JSON}, {LD_JSON}"}
    schemes = current_app.extensions["federation"].signature_schemes
    response = None
    for scheme in schemes:
        signed = _signed_headers("GET", url, headers, b"", ids.instance_key_id(), private, scheme)
        response = client().request("GET", url, signed)
        if response.status != 401:
            break
    if response.status != 200:
        raise FetchError("http", f"{url} answered {response.status}", response.status)
    return response.json()


def signed_post(url: str, activity: dict, *, user_id: int, username: str) -> tuple[Response, str]:
    """POST an activity to an inbox, signed as the user. Tries the configured signature
    schemes in order, moving to the next only when the server answers 401. Returns the
    response and the scheme used."""
    body = json.dumps(activity).encode()
    private = keys.private_key(keys.key_for(user_id))
    headers = {"Content-Type": LD_JSON, "Accept": ACTIVITY_JSON}
    response, used = None, ""
    for used in current_app.extensions["federation"].signature_schemes:
        signed = _signed_headers("POST", url, headers, body, ids.key_id(username), private, used)
        response = client().request("POST", url, signed, body)
        if response.status != 401:
            break
    return response, used
