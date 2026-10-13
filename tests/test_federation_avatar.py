import io

import pytest

from scrobbler.federation import sharing
from scrobbler.services import avatars
from scrobbler.services.accounts import issue_ui_token

AP = {"Accept": "application/activity+json"}
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
API = "/api/v1/federation/profiles/alice"


@pytest.fixture
def storage(fed_app, tmp_path):
    saved = fed_app.config["ART_STORAGE_DIR"]
    fed_app.config["ART_STORAGE_DIR"] = str(tmp_path)
    yield tmp_path
    fed_app.config["ART_STORAGE_DIR"] = saved


@pytest.fixture
def avatar_user(sharing_user, storage):
    """The sharing user, with a profile image that they haven't chosen to share yet."""
    avatars.save(sharing_user, JPEG, "image/jpeg", "upload")
    return sharing_user


@pytest.fixture
def shared(avatar_user):
    sharing.update(avatar_user.id, {"share_avatar": True})
    return avatar_user


def actor(fed_client):
    return fed_client.get("/users/alice", headers=AP).get_json()


def test_sharing_the_avatar_is_off_by_default(fed_client, fed_ctx, user):
    auth = {"Authorization": f"Bearer {issue_ui_token(user)}"}
    settings = fed_client.get("/api/v1/federation/settings", headers=auth).get_json()
    assert settings["share_avatar"] is False
    changed = fed_client.patch(
        "/api/v1/federation/settings", json={"share_avatar": True}, headers=auth
    )
    assert changed.get_json()["share_avatar"] is True


def test_nothing_is_public_until_the_user_opts_in(fed_client, avatar_user):
    assert fed_client.get(API).get_json()["has_avatar"] is False
    assert fed_client.get(f"{API}/avatar").status_code == 404
    assert fed_client.get("/users/alice/avatar").status_code == 404
    assert "icon" not in actor(fed_client)


def test_opted_in_avatar_is_served_publicly(fed_client, shared, metric_delta):
    found = metric_delta("scrobbler_federation_lookups_total", kind="avatar", result="found")
    assert fed_client.get(API).get_json()["has_avatar"] is True
    for path in (f"{API}/avatar", "/users/alice/avatar"):
        response = fed_client.get(path)  # no credentials
        assert (response.status_code, response.data, response.mimetype) == (
            200,
            JPEG,
            "image/jpeg",
        )
        assert response.headers["Cache-Control"] == "public, max-age=300"
    assert found.delta == 2
    tag = fed_client.get("/users/alice/avatar").headers["ETag"]
    assert fed_client.get("/users/alice/avatar", headers={"If-None-Match": tag}).status_code == 304


def test_actor_gets_an_icon_that_changes_with_the_image(fed_client, shared):
    icon = actor(fed_client)["icon"]
    assert icon["type"] == "Image"
    assert icon["mediaType"] == "image/jpeg"
    version = int(shared.avatar_updated_at.timestamp())
    assert icon["url"] == f"https://scrobble.test/users/alice/avatar?v={version}"


def test_no_icon_without_an_image(fed_client, sharing_user, storage):
    sharing.update(sharing_user.id, {"share_avatar": True})
    assert "icon" not in actor(fed_client)
    assert fed_client.get(API).get_json()["has_avatar"] is False
    assert fed_client.get("/users/alice/avatar").status_code == 404


def test_removing_the_image_withdraws_it(fed_client, shared):
    avatars.remove(shared)
    assert fed_client.get("/users/alice/avatar").status_code == 404
    assert "icon" not in actor(fed_client)


def test_not_sharing_at_all_hides_the_avatar_too(fed_client, shared, make_user):
    sharing.update(shared.id, {"enabled": False})
    assert fed_client.get(f"{API}/avatar").status_code == 404
    assert fed_client.get("/users/alice/avatar").status_code == 404
    # ...and looks the same as someone who doesn't exist.
    assert fed_client.get("/users/nobody/avatar").data == fed_client.get("/users/alice/avatar").data


def test_unknown_users_have_no_avatar(fed_client, fed_ctx):
    assert fed_client.get("/api/v1/federation/profiles/nobody/avatar").status_code == 404


def test_the_file_going_missing_is_a_404_not_an_error(fed_client, shared, storage):
    (storage / "avatars" / str(shared.id)).unlink()
    assert fed_client.get("/users/alice/avatar").status_code == 404
    assert "icon" in actor(fed_client)  # the row still says there is one; only the fetch fails


def test_upload_through_the_api_is_what_gets_shared(fed_client, fed_ctx, sharing_user, storage):
    auth = {"Authorization": f"Bearer {issue_ui_token(sharing_user)}"}
    sharing.update(sharing_user.id, {"share_avatar": True})
    uploaded = fed_client.put(
        "/api/v1/me/avatar",
        headers=auth,
        data={"file": (io.BytesIO(JPEG), "me.jpg")},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 200
    assert fed_client.get("/users/alice/avatar").data == JPEG
