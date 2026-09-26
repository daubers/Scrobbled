"""to/cc addressing for our posts, by visibility."""

from scrobbler.federation.protocol.vocab import PUBLIC

VISIBILITIES = ("followers", "unlisted", "public")


def address(visibility: str, followers_url: str) -> tuple[list[str], list[str]]:
    """(to, cc) for a Note/Create/Delete with this visibility.

    followers: to followers, no cc (never reaches a public timeline or search).
    unlisted: to followers, cc Public (visible to anyone who looks, not pushed to feeds).
    public: to Public, cc followers (the normal public-post shape).
    """
    if visibility not in VISIBILITIES:
        raise ValueError(f"unknown visibility: {visibility!r}")
    if visibility == "public":
        return [PUBLIC], [followers_url]
    if visibility == "unlisted":
        return [followers_url], [PUBLIC]
    return [followers_url], []


def is_public_or_unlisted(visibility: str) -> bool:
    """Whether a post at this visibility may be listed in the public outbox."""
    return visibility in ("public", "unlisted")
