"""The only way federation code talks to other servers.

Guards against server-side request forgery (a remote document or redirect pointing us
at internal services):
  * https only (http only with FEDERATION_INSECURE_TESTING, for CI on private networks);
  * a host's addresses are resolved once and the request is refused if any is loopback,
    private, link-local, multicast or otherwise not globally routable; the connection
    then goes to the address that was checked (SNI and Host keep the name), so the name
    can't be re-resolved somewhere else mid-request;
  * at most 3 redirects, each checked the same way; responses capped at 1 MB;
    10s to connect, 20s to read.
"""

import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpcore

from scrobbler.federation.protocol.media import ACTIVITY_JSON, LD_JSON

MAX_BYTES = 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUTS = {"connect": 10.0, "read": 20.0, "write": 20.0, "pool": 10.0}
_REDIRECTS = {301, 302, 303, 307, 308}

Resolver = Callable[[str, int], list[str]]


class FetchError(Exception):
    """A request we refused to make or that didn't succeed."""

    def __init__(self, reason: str, message: str, status: int | None = None):
        self.reason, self.status = (
            reason,
            status,
        )  # reason: blocked, http, too_large, network, invalid
        super().__init__(message)


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]  # lower-cased names
    body: bytes
    url: str  # after redirects

    def json(self) -> dict:
        try:
            doc = json.loads(self.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise FetchError("invalid", f"{self.url} didn't return JSON") from err
        if not isinstance(doc, dict):
            raise FetchError("invalid", f"{self.url} didn't return a JSON object")
        return doc


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as err:
        raise FetchError("network", f"can't resolve {host}") from err
    return list(dict.fromkeys(info[4][0] for info in infos))


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


class _GuardedBackend(httpcore.SyncBackend):
    def __init__(self, resolver: Resolver, allow_private: bool):
        super().__init__()
        self.resolver, self.allow_private = resolver, allow_private

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = self.resolver(host, port)
        if not addresses:
            raise FetchError("network", f"{host} has no addresses")
        if not self.allow_private:
            refused = [a for a in addresses if not is_public(a)]
            if refused:
                raise FetchError("blocked", f"{host} resolves to a non-public address")
        return super().connect_tcp(addresses[0], port, timeout, local_address, socket_options)


class Client:
    def __init__(
        self,
        user_agent: str,
        insecure_testing: bool = False,
        resolver: Resolver = system_resolver,
    ):
        self.user_agent = user_agent
        self.insecure_testing = insecure_testing
        self._pool = httpcore.ConnectionPool(
            network_backend=_GuardedBackend(resolver, allow_private=insecure_testing),
            max_connections=32,
            keepalive_expiry=30,
        )

    def _check_url(self, url: str) -> None:
        parts = urlsplit(url)
        schemes = ("https", "http") if self.insecure_testing else ("https",)
        if parts.scheme not in schemes or not parts.hostname:
            raise FetchError("blocked", f"refusing to fetch {url}")
        if parts.username or parts.password:
            raise FetchError("blocked", "credentials in URL")

    def request(
        self, method: str, url: str, headers: dict[str, str] | None = None, body: bytes = b""
    ) -> Response:
        """One request, following redirects for GET only. Doesn't raise for HTTP errors."""
        for _ in range(MAX_REDIRECTS + 1):
            self._check_url(url)
            response = self._send(method, url, headers or {}, body)
            location = response.headers.get("location")
            if method == "GET" and response.status in _REDIRECTS and location:
                url = urljoin(url, location)
                continue
            return response
        raise FetchError("blocked", "too many redirects")

    def _send(self, method: str, url: str, headers: dict[str, str], body: bytes) -> Response:
        sent = {"User-Agent": self.user_agent, **headers}
        try:
            with self._pool.stream(
                method,
                url,
                headers=list(sent.items()),
                content=body or None,
                extensions={"timeout": TIMEOUTS},
            ) as response:
                chunks, size = [], 0
                for chunk in response.iter_stream():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FetchError("too_large", f"{url} sent more than {MAX_BYTES} bytes")
                    chunks.append(chunk)
                return Response(
                    status=response.status,
                    headers={k.decode().lower(): v.decode("latin-1") for k, v in response.headers},
                    body=b"".join(chunks),
                    url=url,
                )
        except httpcore.TimeoutException as err:
            raise FetchError("network", f"{url} timed out") from err
        except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.UnsupportedProtocol) as err:
            raise FetchError("network", f"{url}: {err}") from err

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict:
        response = self.request(
            "GET", url, {"Accept": f"{ACTIVITY_JSON}, {LD_JSON}", **(headers or {})}
        )
        if response.status != 200:
            raise FetchError("http", f"{url} answered {response.status}", response.status)
        return response.json()

    def close(self) -> None:
        self._pool.close()
