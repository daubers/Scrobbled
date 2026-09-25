"""Render Last.fm responses as XML (the default) or Last.fm-style JSON.

Handlers build a small element tree with `El`; this module turns it into either
`<lfm status="ok">...</lfm>` or the JSON shape Last.fm uses, where:
  * a text element with attributes becomes {"attr": ..., "#text": text}
  * a container element's attributes go under "@attr"
  * repeated child tags become a list
"""

from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

from flask import Response, jsonify

from scrobbler.lastfm.errors import LastFMError


@dataclass
class El:
    tag: str
    text: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list["El"] = field(default_factory=list)

    @classmethod
    def of(cls, tag: str, *children: "El", **attrs: Any) -> "El":
        return cls(tag, attrs=attrs, children=list(children))

    @classmethod
    def text_el(cls, tag: str, text: Any, **attrs: Any) -> "El":
        return cls(tag, text="" if text is None else str(text), attrs=attrs)


def _to_xml(el: El) -> ET.Element:
    node = ET.Element(el.tag, {k: str(v) for k, v in el.attrs.items()})
    if el.text is not None:
        node.text = el.text
    for child in el.children:
        node.append(_to_xml(child))
    return node


def _to_json(el: El) -> Any:
    attrs = {k: str(v) for k, v in el.attrs.items()}
    if el.text is not None:
        return {**attrs, "#text": el.text} if attrs else el.text
    body: dict[str, Any] = {}
    for child in el.children:
        value = _to_json(child)
        if child.tag in body:
            existing = body[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                body[child.tag] = [existing, value]
        else:
            body[child.tag] = value
    if attrs:
        body["@attr"] = attrs
    return body


def render(el: El, fmt: str, *, force_list: tuple[str, ...] = ()) -> Response:
    """Render a successful response. `force_list` names child tags that must be JSON
    lists even when there is only one (e.g. "track" in a recent-tracks page)."""
    if fmt == "json":
        body = _to_json(el)
        for tag in force_list:
            if isinstance(body, dict) and tag in body and not isinstance(body[tag], list):
                body[tag] = [body[tag]]
        return jsonify({el.tag: body})
    root = ET.Element("lfm", {"status": "ok"})
    root.append(_to_xml(el))
    return _xml_response(root, 200)


def render_error(err: LastFMError, fmt: str) -> Response:
    if fmt == "json":
        response = jsonify({"error": err.code, "message": err.message})
        response.status_code = err.http_status
        return response
    root = ET.Element("lfm", {"status": "failed"})
    error = ET.SubElement(root, "error", {"code": str(err.code)})
    error.text = err.message
    return _xml_response(root, err.http_status)


def _xml_response(root: ET.Element, status: int) -> Response:
    body = ET.tostring(root, encoding="unicode", xml_declaration=False)
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n' + body,
        status=status,
        mimetype="application/xml",
    )
