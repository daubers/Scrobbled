"""Registry of Last.fm API methods.

Each handler declares its name, parameters and auth requirements. The dispatcher
enforces the requirements and the OpenAPI document is generated from the same
entries, so the docs always list exactly the methods that exist.
"""

from collections.abc import Callable
from dataclasses import dataclass

from flask import Response

from scrobbler.lastfm.responses import El
from scrobbler.models import ApiApp, User


@dataclass(frozen=True)
class Param:
    name: str
    description: str
    required: bool = False
    example: str | None = None


@dataclass
class Call:
    """Everything a handler needs about the current request."""

    params: dict[str, str]
    api_app: ApiApp
    format: str
    user: User | None = None  # set for methods that require a session key

    def get(self, name: str, default: str | None = None) -> str | None:
        value = self.params.get(name)
        return value if value not in (None, "") else default


Handler = Callable[[Call], El | tuple[El, tuple[str, ...]] | Response]


@dataclass(frozen=True)
class LastfmMethod:
    name: str
    handler: Handler
    summary: str
    signed: bool = False
    session: bool = False
    params: tuple[Param, ...] = ()
    example_xml: str | None = None
    example_json: dict | None = None
    notes: str = ""


METHODS: dict[str, LastfmMethod] = {}


def lastfm_method(
    name: str,
    *,
    summary: str,
    signed: bool = False,
    session: bool = False,
    params: tuple[Param, ...] = (),
    example_xml: str | None = None,
    example_json: dict | None = None,
    notes: str = "",
):
    """Register a handler. Handlers return an `El` tree, or `(El, force_list_tags)`."""

    def decorator(handler: Handler) -> Handler:
        key = name.lower()
        if key in METHODS:
            raise ValueError(f"Last.fm method {name} registered twice")
        METHODS[key] = LastfmMethod(
            name=name,
            handler=handler,
            summary=summary,
            signed=signed,
            session=session,
            params=params,
            example_xml=example_xml,
            example_json=example_json,
            notes=notes,
        )
        return handler

    return decorator
