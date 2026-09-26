"""ActivityPub media types and content negotiation."""

ACTIVITY_JSON = "application/activity+json"
AS_PROFILE = "https://www.w3.org/ns/activitystreams"
LD_JSON = f'application/ld+json; profile="{AS_PROFILE}"'
JRD_JSON = "application/jrd+json"


def _media_ranges(header: str):
    for part in (header or "").split(","):
        pieces = [p.strip() for p in part.split(";")]
        media = pieces[0].lower()
        params = {}
        for piece in pieces[1:]:
            name, _, value = piece.partition("=")
            params[name.strip().lower()] = value.strip().strip('"')
        try:
            quality = float(params.get("q", "1"))
        except ValueError:
            quality = 0.0
        yield media, params, quality


def is_activitypub(media: str, params: dict) -> bool:
    if media == ACTIVITY_JSON:
        return True
    # ld+json counts when it names the ActivityStreams profile (or no profile at all)
    return media == "application/ld+json" and params.get("profile", AS_PROFILE) == AS_PROFILE


def wants_activitypub(accept: str | None) -> bool:
    """True if an Accept header asks for ActivityPub JSON (rather than HTML)."""
    return any(q > 0 and is_activitypub(m, p) for m, p, q in _media_ranges(accept or ""))


def is_activitypub_content_type(content_type: str | None) -> bool:
    return any(is_activitypub(m, p) for m, p, _ in _media_ranges(content_type or ""))
