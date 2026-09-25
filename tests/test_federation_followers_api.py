"""Managing followers: the /api/v1/federation followers and blocks endpoints."""

import pytest

from federation_helpers import RemoteAccount
from scrobbler import worker
from scrobbler.extensions import db
from scrobbler.federation import inbox, sharing
from scrobbler.federation.models import FederationActivity, FederationDelivery, FederationFollower

AP = {"Accept": "application/activity+json"}


@pytest.fixture
def auth_headers(sharing_user):
    from scrobbler.services.accounts import issue_ui_token

    return {"Authorization": f"Bearer {issue_ui_token(sharing_user)}"}


@pytest.fixture
def accounts(sharing_user, remote):
    """Two remote accounts whose inboxes accept everything."""
    made = {}
    for name in ("bob", "carol"):
        account = RemoteAccount(remote, name)
        remote.routes[f"/users/{name}/inbox"] = (202, {}, b"")
        made[name] = account
    return made


def follow(fed_client, account):
    account.deliver(fed_client, account.follow())
    while inbox.work_once():
        pass
    db.session.expire_all()


def sent_types():
    return [
        a.activity_type
        for a in db.session.scalars(
            db.select(FederationActivity).order_by(FederationActivity.created_at)
        )
    ]


def api(fed_client, method, path, headers, **kwargs):
    return getattr(fed_client, method)(f"/api/v1/federation{path}", headers=headers, **kwargs)


def test_followers_are_listed(fed_client, auth_headers, accounts):
    follow(fed_client, accounts["bob"])
    [bob] = api(fed_client, "get", "/followers", auth_headers).get_json()
    assert (bob["handle"], bob["state"], bob["display_name"]) == (
        "@bob@127.0.0.1",
        "accepted",
        "Bob",
    )
    assert bob["actor_url"] == accounts["bob"].uri
    settings = api(fed_client, "get", "/settings", auth_headers).get_json()
    assert (settings["followers"], settings["pending_followers"]) == (1, 0)


def test_requests_can_be_approved(fed_client, auth_headers, accounts, sharing_user):
    sharing.update(sharing_user.id, {"manually_approves_followers": True})
    follow(fed_client, accounts["bob"])
    follow(fed_client, accounts["carol"])
    pending = api(fed_client, "get", "/followers?state=pending", auth_headers).get_json()
    assert {p["handle"] for p in pending} == {"@bob@127.0.0.1", "@carol@127.0.0.1"}
    assert sent_types() == []

    bob_id = next(p["id"] for p in pending if p["handle"].startswith("@bob"))
    approved = api(fed_client, "post", f"/followers/{bob_id}/approve", auth_headers)
    assert approved.get_json()["state"] == "accepted"
    assert sent_types() == ["Accept"]
    # Approving twice is a conflict, not a second Accept
    assert api(fed_client, "post", f"/followers/{bob_id}/approve", auth_headers).status_code == 409


def test_decline_and_remove_send_reject(fed_client, auth_headers, accounts, sharing_user):
    sharing.update(sharing_user.id, {"manually_approves_followers": True})
    follow(fed_client, accounts["bob"])
    [request] = api(fed_client, "get", "/followers", auth_headers).get_json()
    assert (
        api(fed_client, "post", f"/followers/{request['id']}/decline", auth_headers).status_code
        == 204
    )
    assert sent_types() == ["Reject"]
    assert api(fed_client, "get", "/followers", auth_headers).get_json() == []

    sharing.update(sharing_user.id, {"manually_approves_followers": False})
    follow(fed_client, accounts["carol"])
    [carol] = api(fed_client, "get", "/followers", auth_headers).get_json()
    assert (
        api(fed_client, "post", f"/followers/{carol['id']}/remove", auth_headers).status_code == 204
    )
    assert sent_types() == ["Reject", "Accept", "Reject"]


def test_block_and_unblock(fed_client, auth_headers, accounts):
    follow(fed_client, accounts["bob"])
    [bob] = api(fed_client, "get", "/followers", auth_headers).get_json()
    assert api(fed_client, "post", f"/followers/{bob['id']}/block", auth_headers).status_code == 204
    assert sent_types()[-1] == "Block"
    [block] = api(fed_client, "get", "/blocks", auth_headers).get_json()
    assert block["handle"] == "@bob@127.0.0.1"

    follow(fed_client, accounts["bob"])  # tries again: refused
    assert api(fed_client, "get", "/followers", auth_headers).get_json() == []
    assert sent_types()[-1] == "Reject"

    assert api(fed_client, "delete", f"/blocks/{block['id']}", auth_headers).status_code == 204
    follow(fed_client, accounts["bob"])  # now allowed
    assert len(api(fed_client, "get", "/followers", auth_headers).get_json()) == 1


def test_other_users_followers_are_off_limits(
    fed_client, auth_headers, accounts, make_user, fed_app
):
    from scrobbler.services.accounts import issue_ui_token

    follow(fed_client, accounts["bob"])
    [bob] = api(fed_client, "get", "/followers", auth_headers).get_json()
    other = make_user(username="mallory")
    mallory = {"Authorization": f"Bearer {issue_ui_token(other)}"}
    for action in ("approve", "decline", "remove", "block"):
        assert (
            api(fed_client, "post", f"/followers/{bob['id']}/{action}", mallory).status_code == 404
        )
    assert api(fed_client, "get", "/followers", mallory).get_json() == []


def test_turning_sharing_off_rejects_everyone(fed_client, auth_headers, accounts, remote):
    follow(fed_client, accounts["bob"])
    follow(fed_client, accounts["carol"])
    while worker.run_once():  # send the Accepts
        pass
    api(fed_client, "patch", "/settings", auth_headers, json={"enabled": False})
    db.session.expire_all()
    assert db.session.scalars(db.select(FederationFollower)).all() == []
    assert sent_types().count("Reject") == 2

    # While the Rejects are on their way, other servers can still fetch the key to verify
    # them, but nothing else about the profile.
    actor = fed_client.get("/users/alice", headers=AP).get_json()
    assert actor["publicKey"]["publicKeyPem"].startswith("-----BEGIN PUBLIC KEY-----")
    assert (actor["name"], actor["summary"]) == ("alice", "")
    assert (
        fed_client.get("/.well-known/webfinger?resource=acct:alice@scrobble.test").status_code
        == 404
    )

    while worker.run_once():  # deliver the Rejects
        pass
    db.session.expire_all()
    assert {d.status for d in db.session.scalars(db.select(FederationDelivery))} == {"delivered"}
    assert fed_client.get("/users/alice", headers=AP).status_code == 404


def test_actor_reflects_approval_setting_and_follower_count(fed_client, accounts, sharing_user):
    assert (
        fed_client.get("/users/alice", headers=AP).get_json()["manuallyApprovesFollowers"] is False
    )
    sharing.update(sharing_user.id, {"manually_approves_followers": True})
    assert (
        fed_client.get("/users/alice", headers=AP).get_json()["manuallyApprovesFollowers"] is True
    )

    sharing.update(sharing_user.id, {"manually_approves_followers": False})
    follow(fed_client, accounts["bob"])
    assert fed_client.get("/users/alice/followers", headers=AP).get_json()["totalItems"] == 1
