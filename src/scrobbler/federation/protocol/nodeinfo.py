"""NodeInfo 2.1 (https://nodeinfo.diaspora.software/): what software a server runs."""

SCHEMA_21 = "http://nodeinfo.diaspora.software/ns/schema/2.1"


def discovery(base_url: str) -> dict:
    return {"links": [{"rel": SCHEMA_21, "href": f"{base_url}/nodeinfo/2.1"}]}


def document(*, version: str, users_total: int, open_registrations: bool) -> dict:
    return {
        "version": "2.1",
        "software": {"name": "scrobbler", "version": version},
        "protocols": ["activitypub"],
        "services": {"inbound": [], "outbound": []},
        "openRegistrations": open_registrations,
        "usage": {"users": {"total": users_total}, "localPosts": 0},
        "metadata": {},
    }
