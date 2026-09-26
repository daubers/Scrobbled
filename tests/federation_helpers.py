"""A scripted remote fediverse account for federation tests."""

import dataclasses
import json
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from scrobbler.federation.protocol import signatures
from scrobbler.federation.protocol.signatures import SignedRequest

OUR_BASE = "https://scrobble.test"


class RemoteAccount:
    """An account on a FakeRemote server: serves its actor document and signs deliveries."""

    def __init__(self, server, name="bob"):
        self.server, self.name = server, name
        self.uri = server.url(f"/users/{name}")
        self.key_id = f"{self.uri}#main-key"
        self.rotate_key()
        self.serve()

    def rotate_key(self):
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @property
    def public_pem(self) -> str:
        return (
            self.private.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )

    def document(self, **overrides) -> dict:
        doc = {
            "@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/security/v1"],
            "id": self.uri,
            "type": "Person",
            "preferredUsername": self.name,
            "name": self.name.title(),
            "inbox": f"{self.uri}/inbox",
            "endpoints": {"sharedInbox": self.server.url("/inbox")},
            "publicKey": {"id": self.key_id, "owner": self.uri, "publicKeyPem": self.public_pem},
        }
        doc.update(overrides)
        return doc

    def serve(self, **overrides):
        self.server.routes[f"/users/{self.name}"] = (200, {}, self.document(**overrides))

    def activity(self, activity_type: str, obj, **extra) -> dict:
        return {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": self.server.url(f"/activities/{uuid.uuid4()}"),
            "type": activity_type,
            "actor": self.uri,
            "object": obj,
            **extra,
        }

    def follow(self, username="alice") -> dict:
        return self.activity("Follow", f"{OUR_BASE}/users/{username}")

    def deliver(
        self,
        client,
        activity: dict,
        path="/users/alice/inbox",
        scheme="draft-cavage",
        key=None,
        key_id=None,
        content_type="application/activity+json",
    ):
        """POST `activity` to our inbox, signed like a real server would."""
        body = json.dumps(activity).encode()
        request = SignedRequest("POST", OUR_BASE + path, {"Content-Type": content_type}, body)
        if scheme:
            added = signatures.sign(request, scheme, key_id or self.key_id, key or self.private)
            request = dataclasses.replace(request, headers={**request.headers, **added})
        return client.post(path, data=body, headers=dict(request.headers))
