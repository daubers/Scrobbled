"""Safe post content (protocol/content.py): escaping, and the weekly summary template
built on top of it (publishing/weekly.py's build_content)."""

import pytest

from scrobbler.federation.protocol import content
from scrobbler.federation.publishing.weekly import build_content
from scrobbler.services.stats import TopItem


def test_escape_neutralises_markup():
    assert content.escape("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_escape_handles_ampersands_without_double_escaping():
    assert content.escape("Earth, Wind & Fire") == "Earth, Wind &amp; Fire"


def test_escape_passes_through_emoji_and_non_latin_text():
    # Emoji and non-Latin scripts aren't HTML-significant, so they pass through untouched.
    assert content.escape("Björk 🎵 坂本龍一") == "Björk 🎵 坂本龍一"


def test_escape_does_not_break_right_to_left_text():
    name = "محمد <عبده>"  # Arabic text with embedded angle brackets
    escaped = content.escape(name)
    assert "<" not in escaped and ">" not in escaped
    assert "محمد" in escaped  # the Arabic letters are untouched


def test_paragraph_and_numbered_list_only_use_tags_mastodon_keeps():
    assert content.paragraph("hi") == "<p>hi</p>"
    assert content.numbered_list(["a", "b"]) == "<ol><li>a</li><li>b</li></ol>"


@pytest.mark.parametrize(
    ("count", "word"), [(0, "plays"), (1, "play"), (2, "plays"), (312, "plays")]
)
def test_plural(count, word):
    assert content.plural(count, "play") == word


def test_hashtag_line_matches_the_vocab_hashtag_name():
    from scrobbler.federation.protocol import vocab

    assert content.hashtag_line() == vocab.hashtag(content.HASHTAG)["name"]


# --- The weekly summary template (uses the primitives above) --------------------------


def test_build_content_escapes_hostile_names_in_both_text_and_html():
    artists = [TopItem(1, "<script>alert(1)</script>", 5)]
    tracks = [TopItem(1, 'Earth, Wind & Fire\'s "Anthem"', 3, artist="A & B", duration=200)]
    text, html = build_content(8, artists, tracks)

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&amp; B" in html
    # The plain-text version is never escaped: it's not HTML, so escaping it would show
    # literal "&amp;" to a reader.
    assert "Earth, Wind & Fire" in text
    assert "&lt;" not in text and "&amp;" not in text


def test_build_content_handles_emoji_and_non_latin_names():
    artists = [TopItem(1, "坂本龍一", 10), TopItem(2, "Björk 🎵", 5)]
    text, html = build_content(15, artists, [])
    assert "坂本龍一" in text and "坂本龍一" in html
    assert "Björk 🎵" in text and "Björk 🎵" in html


def test_build_content_shape():
    artists = [TopItem(1, "Radiohead", 48), TopItem(2, "Björk", 31), TopItem(3, "Portishead", 22)]
    tracks = [TopItem(1, "Reckoner", 12, artist="Radiohead", duration=290)]
    text, html = build_content(312, artists, tracks)

    assert text.splitlines() == [
        "My week in music: 312 plays",
        "Top artists: 1. Radiohead (48) 2. Björk (31) 3. Portishead (22)",
        "Most played: Reckoner by Radiohead (12 plays)",
        "#Scrobbler",
    ]
    assert html.startswith("<p><strong>My week in music: 312 plays</strong></p>")
    assert "<ol><li>Radiohead" in html
    assert "<em>Reckoner</em> by Radiohead" in html
    assert html.endswith("<p>#Scrobbler</p>")
    assert "<script" not in html  # sanity: nothing unescaped ever reaches the markup


def test_build_content_without_any_plays_this_week():
    text, html = build_content(0, [], [])
    assert text == "My week in music: 0 plays\n#Scrobbler"
    assert "<ol>" not in html  # no top-artists list when there isn't one
    assert "Most played" not in html


def test_build_content_singular_play():
    text, _ = build_content(1, [TopItem(1, "Radiohead", 1)], [])
    assert "My week in music: 1 play" in text
    assert "Radiohead (1)" in text
