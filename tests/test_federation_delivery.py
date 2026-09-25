"""Sending activities: signed delivery, retries, double-knocking, gone accounts."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization

from federation_helpers import RemoteAccount
from scrobbler import worker
from scrobbler.extensions import db
from scrobbler.federation import delivery, keys
from scrobbler.federation.models import (
    FederationDelivery,
    FederationFollower,
    FederationRemoteActor,
)
from scrobbler.federation.protocol import signatures
from scrobbler.federation.protocol.signatures import SignedRequest


@pytest.fixture
def bob(sharing_user, remote):
    account = RemoteAccount(remote, "bob")
    account.inbox_path = f"/users/{account.name}/inbox"
    remote.routes[account.inbox_path] = (202, {}, b"")
    return account


def run_worker():
    worker.run(once=True)
    db.session.expire_all()


def follow_and_process(fed_client, bob):
    bob.deliver(fed_client, bob.follow())
    run_worker()


def posts_to(remote, path):
    return [r for r in remote.requests if r["method"] == "POST" and r["path"] == path]


def the_delivery() -> FederationDelivery:
    return db.session.scalars(db.select(FederationDelivery)).one()


def test_accept_is_delivered_signed_as_the_user(
    fed_client, bob, remote, sharing_user, metric_delta
):
    delivered = metric_delta("scrobbler_federation_deliveries_total", result="delivered")
    follow_and_process(fed_client, bob)

    [post] = posts_to(remote, bob.inbox_path)
    activity = json.loads(post["body"])
    assert (activity["type"], activity["actor"]) == ("Accept", "https://scrobble.test/users/alice")
    assert activity["object"]["type"] == "Follow"

    # Bob's server can verify it with Alice's published key
    public = serialization.load_pem_public_key(
        keys.key_for(sharing_user.id).public_key_pem.encode()
    )
    request = SignedRequest("POST", remote.url(bob.inbox_path), post["headers"], post["body"])
    verified = signatures.verify(request, lambda key_id: public)
    assert (verified.scheme, verified.key_id) == (
        "draft-cavage",
        "https://scrobble.test/users/alice#main-key",
    )

    assert (the_delivery().status, the_delivery().last_status) == ("delivered", 202)
    assert delivered.delta == 1


def test_server_errors_are_retried_with_backoff(fed_client, bob, remote, metric_delta):
    retries = metric_delta("scrobbler_federation_deliveries_total", result="retry")
    remote.routes[bob.inbox_path] = (503, {}, b"busy")
    follow_and_process(fed_client, bob)
    d = the_delivery()
    assert (d.status, d.attempts, d.last_status) == ("pending", 1, 503)
    assert timedelta(seconds=50) < d.next_attempt_at - datetime.now(UTC) <= timedelta(minutes=1)
    assert retries.delta == 1

    # Not due yet: nothing is sent
    run_worker()
    assert len(posts_to(remote, bob.inbox_path)) == 1

    # When due, the second failure backs off further
    d.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.session.commit()
    run_worker()
    d = the_delivery()
    assert d.attempts == 2
    assert d.next_attempt_at - datetime.now(UTC) > timedelta(minutes=4)

    # Once it recovers, it's delivered
    remote.routes[bob.inbox_path] = (200, {}, b"")
    d.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.session.commit()
    run_worker()
    assert the_delivery().status == "delivered"


def test_deliveries_are_abandoned_after_two_days(fed_client, bob, remote, metric_delta):
    abandoned = metric_delta("scrobbler_federation_deliveries_total", result="abandoned")
    remote.routes[bob.inbox_path] = (500, {}, b"")
    follow_and_process(fed_client, bob)
    d = the_delivery()
    d.created_at = datetime.now(UTC) - timedelta(days=2, minutes=1)
    d.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.session.commit()
    run_worker()
    assert the_delivery().status == "abandoned"
    assert abandoned.delta == 1


@pytest.mark.parametrize(
    ("status", "outcome"), [(400, "abandoned"), (403, "abandoned"), (404, "abandoned")]
)
def test_client_errors_are_not_retried(fed_client, bob, remote, status, outcome):
    remote.routes[bob.inbox_path] = (status, {}, b"no")
    follow_and_process(fed_client, bob)
    assert (the_delivery().status, the_delivery().last_status) == (outcome, status)


@pytest.mark.parametrize("status", [408, 429])
def test_throttling_and_timeouts_are_retried(fed_client, bob, remote, status):
    remote.routes[bob.inbox_path] = (status, {}, b"")
    follow_and_process(fed_client, bob)
    assert the_delivery().status == "pending"


def test_rfc9421_is_tried_after_a_401(fed_client, bob, remote):
    """Double-knocking: a server that only accepts RFC 9421 gets it on the second try."""

    def rfc9421_only(request):
        return (202, {}, b"") if "Signature-Input" in request.headers else (401, {}, b"")

    remote.routes[bob.inbox_path] = rfc9421_only
    follow_and_process(fed_client, bob)
    first, second = posts_to(remote, bob.inbox_path)
    assert "Signature-Input" not in first["headers"]
    assert "Signature-Input" in second["headers"]
    assert the_delivery().status == "delivered"


def test_gone_inbox_removes_the_follower(fed_client, bob, remote, sharing_user, metric_delta):
    gone = metric_delta("scrobbler_federation_deliveries_total", result="gone")
    remote.routes[bob.inbox_path] = (410, {}, b"")
    follow_and_process(fed_client, bob)
    assert the_delivery().status == "abandoned"
    assert db.session.scalars(db.select(FederationFollower)).all() == []
    assert db.session.scalar(db.select(FederationRemoteActor).filter_by(uri=bob.uri)).gone
    assert gone.delta == 1


def test_unreachable_inbox_is_retried(fed_client, bob, remote):
    from scrobbler.federation import inbox

    bob.deliver(fed_client, bob.follow())
    inbox.work_once()  # processing the Follow needs bob's server...
    remote.close()  # ...which then goes away before we deliver the Accept
    run_worker()
    assert (the_delivery().status, the_delivery().attempts) == ("pending", 1)


def test_many_deliveries_in_one_pass(fed_client, bob, remote, sharing_user, make_user):
    from scrobbler.federation import sharing

    for name in ("carol", "dave", "erin"):
        user = make_user(username=name)
        sharing.update(user.id, {"enabled": True})
        bob.deliver(fed_client, bob.follow(name), path="/inbox")
    run_worker()
    assert len(posts_to(remote, bob.inbox_path)) == 3
    assert {d.status for d in db.session.scalars(db.select(FederationDelivery))} == {"delivered"}


def test_expired_lease_makes_a_delivery_due_again(fed_client, bob, remote):
    """A worker that died mid-send leaves a lease; after it runs out, it's sent again."""
    remote.routes[bob.inbox_path] = (202, {}, b"")
    bob.deliver(fed_client, bob.follow())
    from scrobbler.federation import inbox

    inbox.work_once()
    [claimed] = delivery.claim()  # claimed, then the "worker" dies
    d = db.session.get(FederationDelivery, claimed)
    assert d.next_attempt_at > datetime.now(UTC)
    d.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)  # the lease runs out
    db.session.commit()
    run_worker()
    assert the_delivery().status == "delivered"


def test_gauges(fed_client, bob, remote):
    from prometheus_client import REGISTRY

    remote.routes[bob.inbox_path] = (503, {}, b"")
    follow_and_process(fed_client, bob)
    delivery.maintenance()
    assert (
        REGISTRY.get_sample_value(
            "scrobbler_federation_queue", {"queue": "deliveries", "status": "pending"}
        )
        == 1
    )
    assert REGISTRY.get_sample_value("scrobbler_federation_followers", {"state": "accepted"}) == 1
