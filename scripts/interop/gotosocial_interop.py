"""Federation interop test: a real GoToSocial account follows a Scrobbler user.

Runs inside the interop compose network (see deploy/interop/). Standard library only.
Exits non-zero, with a description, on the first thing that doesn't work.
"""

import html
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GTS = "http://gts.test"
SCROBBLER_API = "http://api:8000/api/v1"
GTS_EMAIL = os.environ.get("GTS_EMAIL", "gtsuser@gts.test")
GTS_PASSWORD = os.environ.get("GTS_PASSWORD", "Interop-Test-Password-1")
OOB = "urn:ietf:wg:oauth:2.0:oob"


def step(message: str) -> None:
    print(f"==> {message}", flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Browser:
    """Just enough of a browser to sign in and authorize an OAuth app."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect()
        )

    def request(self, method, url, form=None):
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.opener.open(req, timeout=20) as response:
                return response.status, dict(response.headers), response.read().decode()
        except urllib.error.HTTPError as err:  # includes 3xx, since we don't follow them
            return err.code, dict(err.headers), err.read().decode()


def api(method, url, token=None, body=None, form=None):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif form is not None:
        data, headers["Content-Type"] = (
            urllib.parse.urlencode(form).encode(),
            "application/x-www-form-urlencoded",
        )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw)
        except json.JSONDecodeError:
            return err.code, raw.decode(errors="replace")[:500]


def fail(message: str):
    print(f"FAILED: {message}", flush=True)
    sys.exit(1)


def gotosocial_token() -> str:
    """Sign in to GoToSocial's web UI and authorize an app (it has no password grant)."""
    status, app = api(
        "POST",
        f"{GTS}/api/v1/apps",
        form={
            "client_name": "scrobbler-interop",
            "redirect_uris": OOB,
            "scopes": "read write follow",
        },
    )
    if status != 200:
        fail(f"creating a GoToSocial app: {status} {app}")
    browser = Browser()
    authorize = f"{GTS}/oauth/authorize?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": app["client_id"],
            "redirect_uri": OOB,
            "scope": "read write follow",
        }
    )
    status, headers, body = browser.request("GET", authorize)
    print(f"   authorize -> {status} {headers.get('Location', '')}")
    status, headers, body = browser.request(
        "POST", f"{GTS}/auth/sign_in", {"username": GTS_EMAIL, "password": GTS_PASSWORD}
    )
    print(f"   sign in -> {status} {headers.get('Location', '')}")
    if status not in (302, 303):
        fail(f"sign-in didn't redirect: {status} {body[:300]}")
    location = urllib.parse.urljoin(GTS, headers.get("Location", "/oauth/authorize"))
    status, headers, body = browser.request("GET", location)
    print(f"   authorize page -> {status} {headers.get('Location', '')}")
    if status == 200:
        # The consent page: submit its form (with any hidden fields it has)
        fields = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', body))
        action = re.search(r'<form[^>]*action="([^"]+)"', body)
        target = urllib.parse.urljoin(
            GTS, html.unescape(action.group(1)) if action else "/oauth/authorize"
        )
        status, headers, body = browser.request(
            "POST", target, {k: html.unescape(v) for k, v in fields.items()}
        )
        print(f"   consent -> {status} {headers.get('Location', '')}")
    location = headers.get("Location", "")
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query).get("code", [None])[0]
    if code is None and status in (302, 303):
        # Out-of-band: the code is shown on the page we're sent to
        status, headers, body = browser.request("GET", urllib.parse.urljoin(GTS, location))
        match = re.search(r"<code>([^<]+)</code>", body) or re.search(
            r'value="([A-Za-z0-9_-]{20,})"', body
        )
        code = match.group(1) if match else None
    if not code:
        fail(f"no authorization code (last response {status}: {body[:300]})")
    status, token = api(
        "POST",
        f"{GTS}/oauth/token",
        form={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": app["client_id"],
            "client_secret": app["client_secret"],
            "redirect_uri": OOB,
        },
    )
    if status != 200:
        fail(f"exchanging the code: {status} {token}")
    return token["access_token"]


def wait_for(description, check, timeout=60):
    """Poll `check()` until it returns something truthy."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(1)
    fail(f"timed out waiting for: {description}")


class Scrobbler:
    def __init__(self, username="alice", password="interop-password-1"):
        status, body = api(
            "POST",
            f"{SCROBBLER_API}/auth/register",
            body={
                "username": username,
                "email": f"{username}@scrobble.test",
                "password": password,
            },
        )
        if status == 409:
            status, body = api(
                "POST",
                f"{SCROBBLER_API}/auth/login",
                body={
                    "username": username,
                    "password": password,
                },
            )
        if status not in (200, 201):
            fail(f"signing in to Scrobbler: {status} {body}")
        self.token = body["token"]

    def settings(self, **changes):
        status, body = api(
            "PATCH", f"{SCROBBLER_API}/federation/settings", self.token, body=changes
        )
        if status != 200:
            fail(f"changing Scrobbler settings: {status} {body}")
        return body

    def followers(self, state=None):
        query = f"?state={state}" if state else ""
        status, body = api("GET", f"{SCROBBLER_API}/federation/followers{query}", self.token)
        if status != 200:
            fail(f"listing followers: {status} {body}")
        return body

    def act(self, follower_id, action):
        status, body = api(
            "POST", f"{SCROBBLER_API}/federation/followers/{follower_id}/{action}", self.token
        )
        if status not in (200, 204):
            fail(f"{action} follower: {status} {body}")


class GoToSocial:
    def __init__(self, token):
        self.token = token

    def resolve(self, handle):
        status, body = api(
            "GET",
            f"{GTS}/api/v2/search?"
            + urllib.parse.urlencode({"q": handle, "resolve": "true", "type": "accounts"}),
            self.token,
        )
        if status != 200 or not body.get("accounts"):
            fail(f"GoToSocial couldn't find {handle}: {status} {body}")
        return body["accounts"][0]

    def follow(self, account_id):
        status, body = api(
            "POST", f"{GTS}/api/v1/accounts/{account_id}/follow", self.token, body={}
        )
        if status != 200:
            fail(f"following: {status} {body}")
        return body

    def unfollow(self, account_id):
        status, body = api(
            "POST", f"{GTS}/api/v1/accounts/{account_id}/unfollow", self.token, body={}
        )
        if status != 200:
            fail(f"unfollowing: {status} {body}")

    def relationship(self, account_id):
        status, body = api(
            "GET", f"{GTS}/api/v1/accounts/relationships?id[]={account_id}", self.token
        )
        if status != 200 or not body:
            fail(f"relationship: {status} {body}")
        return body[0]


def main():
    step("Getting a GoToSocial access token")
    gts = GoToSocial(gotosocial_token())

    step("Alice starts sharing on Scrobbler")
    alice = Scrobbler()
    alice.settings(
        enabled=True,
        display_name="Alice Interop",
        bio="Testing federation.",
        manually_approves_followers=False,
    )

    step("GoToSocial finds @alice@scrobble.test (WebFinger, actor, key)")
    account = gts.resolve("@alice@scrobble.test")
    print(f"   found {account['acct']} ({account['display_name']!r})")
    if account["display_name"] != "Alice Interop":
        fail(f"unexpected display name {account['display_name']!r}")
    account_id = account["id"]

    step("Follow: GoToSocial sees it accepted (our signed Accept verified)")
    gts.follow(account_id)
    wait_for("GoToSocial to be following", lambda: gts.relationship(account_id)["following"])
    followers = wait_for("Scrobbler to list the follower", lambda: alice.followers("accepted"))
    print(f"   Scrobbler follower: {followers[0]['handle']}")
    if followers[0]["handle"] != "@gtsuser@gts.test":
        fail(f"unexpected follower {followers[0]['handle']}")

    step("Unfollow: Scrobbler drops the follower (their signed Undo verified)")
    gts.unfollow(account_id)
    wait_for("Scrobbler to drop the follower", lambda: not alice.followers())

    step("Manual approval: the follow waits as a request")
    alice.settings(manually_approves_followers=True)
    gts.follow(account_id)
    wait_for(
        "GoToSocial to show a pending request", lambda: gts.relationship(account_id)["requested"]
    )
    pending = wait_for("Scrobbler to list the request", lambda: alice.followers("pending"))
    if gts.relationship(account_id)["following"]:
        fail("following before being approved")

    step("Approve: GoToSocial sees it accepted")
    alice.act(pending[0]["id"], "approve")
    wait_for("GoToSocial to be following", lambda: gts.relationship(account_id)["following"])

    step("Remove the follower: Scrobbler drops them and sends a Reject")
    alice.act(alice.followers()[0]["id"], "remove")
    if alice.followers():
        fail("the follower is still listed after removal")
    # GoToSocial 0.22.1 only applies a Reject to *pending* follow requests (its
    # federatingdb/reject.go has a TODO for accepted follows; fixed on its main branch).
    # Mastodon, and the ActivityPub consensus, drop the follow. Until a GoToSocial
    # release includes the fix, their side is checked only when asked to.
    if os.environ.get("GTS_REJECT_ENDS_FOLLOWS") == "1":
        wait_for(
            "GoToSocial to stop following", lambda: not gts.relationship(account_id)["following"]
        )
    else:
        print("   (skipping GoToSocial's side: its 0.22.1 ignores Reject for accepted follows)")
    gts.unfollow(account_id)  # tidy up their side for the next step
    wait_for(
        "GoToSocial to not be following", lambda: not gts.relationship(account_id)["following"]
    )

    step("Block a follower: GoToSocial drops the follow (it honours Block)")
    gts.follow(account_id)
    pending = wait_for("Scrobbler to list the request", lambda: alice.followers("pending"))
    alice.act(pending[0]["id"], "approve")
    wait_for("GoToSocial to be following", lambda: gts.relationship(account_id)["following"])
    alice.act(alice.followers()[0]["id"], "block")
    wait_for("GoToSocial to stop following", lambda: not gts.relationship(account_id)["following"])
    status, blocks = api("GET", f"{SCROBBLER_API}/federation/blocks", alice.token)
    if status != 200 or [b["handle"] for b in blocks] != ["@gtsuser@gts.test"]:
        fail(f"the block isn't listed: {status} {blocks}")
    wait_for("GoToSocial to see the block", lambda: gts.relationship(account_id)["blocked_by"])

    step("Unblock: GoToSocial sees the Undo of the Block")
    status, _ = api("DELETE", f"{SCROBBLER_API}/federation/blocks/{blocks[0]['id']}", alice.token)
    if status != 204:
        fail(f"unblocking: {status}")
    wait_for(
        "GoToSocial to see the unblock", lambda: not gts.relationship(account_id)["blocked_by"]
    )

    step("Decline a request: GoToSocial's request is withdrawn")
    gts.follow(account_id)
    pending = wait_for("Scrobbler to list the request", lambda: alice.followers("pending"))
    alice.act(pending[0]["id"], "decline")
    wait_for(
        "GoToSocial's request to go away",
        lambda: (
            not gts.relationship(account_id)["requested"]
            and not gts.relationship(account_id)["following"]
        ),
    )

    step("All interop checks passed")


if __name__ == "__main__":
    main()
