"""Send realistic traffic to a running Scrobbler, e.g. to see the Grafana dashboards move.

    uv run python scripts/generate_traffic.py --api http://localhost:5050 --minutes 5

Uses only the public APIs: registers (or logs in) a user, creates an API app, signs in
like a player, then loops scrobbling, updating now-playing and reading stats. A share
of requests are deliberately bad (ignored scrobbles, bad signatures, wrong passwords)
so the error panels have something to show.
"""

import argparse
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from scrobbler.lastfm.signature import sign

LIBRARY = [
    ("Radiohead", "In Rainbows", ["Reckoner", "Nude", "Weird Fishes/Arpeggi", "House of Cards"]),
    ("Björk", "Homogenic", ["Jóga", "Hunter", "Bachelorette", "All Is Full of Love"]),
    ("Portishead", "Dummy", ["Roads", "Glory Box", "Sour Times"]),
    ("Boards of Canada", "Music Has the Right to Children", ["Roygbiv", "Aquarius"]),
    ("Khruangbin", "Con Todo El Mundo", ["Maria También", "Friday Morning"]),
    ("Little Simz", "Sometimes I Might Be Introvert", ["Introvert", "Woman"]),
]


class Client:
    def __init__(self, api: str):
        self.api = api.rstrip("/")
        self.token = None
        self.key = self.secret = self.sk = None

    def rest(self, method, path, body=None):
        req = urllib.request.Request(
            f"{self.api}/api/v1{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"null")

    def lastfm(self, method, *, signed=True, session=False, bad_sig=False, **params):
        params = {"method": method, "api_key": self.key, **params}
        if session:
            params["sk"] = self.sk
        if signed:
            params["api_sig"] = "0" * 32 if bad_sig else sign(params, self.secret)
        data = urllib.parse.urlencode({**params, "format": "json"}).encode()
        try:
            with urllib.request.urlopen(f"{self.api}/2.0/", data, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            return json.load(err)


def setup(client: Client, username: str, password: str) -> None:
    status, body = client.rest(
        "POST",
        "/auth/register",
        {"username": username, "email": f"{username}@example.com", "password": password},
    )
    if status == 409:
        status, body = client.rest(
            "POST", "/auth/login", {"username": username, "password": password}
        )
    if status not in (200, 201):
        raise SystemExit(f"could not sign in as {username}: {body}")
    client.token = body["token"]
    _, apps = client.rest("GET", "/apps")
    app = apps[0] if apps else client.rest("POST", "/apps", {"name": "Traffic generator"})[1]
    client.key, client.secret = app["api_key"], app["shared_secret"]
    session = client.lastfm("auth.getMobileSession", username=username, password=password)
    client.sk = session["session"]["key"]


def random_track(offset_seconds: int) -> dict:
    artist, album, tracks = random.choice(LIBRARY)
    return {
        "artist": artist,
        "album": album,
        "track": random.choice(tracks),
        "duration": str(random.randint(150, 360)),
        "timestamp": str(int(time.time()) - offset_seconds),
    }


def batch_params(tracks: list[dict]) -> dict:
    return {f"{k}[{i}]": v for i, t in enumerate(tracks) for k, v in t.items()}


def tick(client: Client, username: str) -> None:
    roll = random.random()
    if roll < 0.35:
        track = random_track(0)
        client.lastfm(
            "track.updateNowPlaying",
            session=True,
            **{k: v for k, v in track.items() if k != "timestamp"},
        )
    elif roll < 0.65:
        tracks = [
            random_track(random.randint(60, 3600)) for _ in range(random.choice([1, 1, 2, 5]))
        ]
        client.lastfm("track.scrobble", session=True, **batch_params(tracks))
    elif roll < 0.70:
        # Offline cache flush: a big batch of older plays
        tracks = [
            random_track(random.randint(3600, 5 * 86400)) for _ in range(random.randint(10, 50))
        ]
        client.lastfm("track.scrobble", session=True, **batch_params(tracks))
    elif roll < 0.75:
        # Ignored scrobbles: too old, in the future, empty artist
        bad = random.choice(
            [random_track(20 * 86400), random_track(-7200), {**random_track(60), "artist": ""}]
        )
        client.lastfm("track.scrobble", session=True, **batch_params([bad]))
    elif roll < 0.78:
        client.lastfm(
            "track.scrobble", session=True, bad_sig=True, **batch_params([random_track(60)])
        )
    elif roll < 0.80:
        client.rest("POST", "/auth/login", {"username": username, "password": "wrong-password"})
    elif roll < 0.90:
        path = random.choice(["/me/summary", "/me/recent", "/me/now-playing", "/me/counts"])
        client.rest("GET", path)
        client.rest("GET", f"/me/top/{random.choice(['artists', 'albums', 'tracks'])}?period=7day")
    else:
        method = random.choice(["user.getRecentTracks", "user.getTopArtists", "user.getInfo"])
        client.lastfm(method, signed=False, user=username)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:5050")
    parser.add_argument("--username", default="traffic")
    parser.add_argument("--password", default="traffic-password")
    parser.add_argument("--minutes", type=float, default=5)
    parser.add_argument("--rate", type=float, default=5, help="requests per second")
    args = parser.parse_args()

    client = Client(args.api)
    setup(client, args.username, args.password)
    end = time.time() + args.minutes * 60
    sent = 0
    while time.time() < end:
        tick(client, args.username)
        sent += 1
        time.sleep(random.expovariate(args.rate))
    print(f"sent {sent} requests")


if __name__ == "__main__":
    main()
