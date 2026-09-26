"""Safe post content: plain text and matching HTML, shared by every post kind so
they're formatted consistently and every track/artist name is escaped the same way.

Only the tags Mastodon keeps: p, strong, em, ol, li. No br is needed here since each
line of a post is its own paragraph.
"""

import html

HASHTAG = "Scrobbler"


def escape(text: str) -> str:
    """HTML-escape a name (artist, track, ...) for embedding in generated markup."""
    return html.escape(text, quote=False)


def paragraph(inner_html: str) -> str:
    return f"<p>{inner_html}</p>"


def numbered_list(items_html: list[str]) -> str:
    return "<ol>" + "".join(f"<li>{item}</li>" for item in items_html) + "</ol>"


def hashtag_line() -> str:
    return f"#{HASHTAG}"


def plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"
