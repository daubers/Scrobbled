"""/.well-known/host-meta (RFC 6415): points older clients at WebFinger."""

from xml.sax.saxutils import quoteattr

CONTENT_TYPE = "application/xrd+xml"


def xrd(base_url: str) -> str:
    template = quoteattr(f"{base_url}/.well-known/webfinger?resource={{uri}}")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<XRD xmlns="http://docs.oasis-open.org/ns/xri/xrd-1.0">\n'
        f'  <Link rel="lrdd" type="application/jrd+json" template={template}/>\n'
        "</XRD>\n"
    )
