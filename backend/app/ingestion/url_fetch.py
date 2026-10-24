"""SSRF-resistant fetching of public web pages for URL ingestion.

Defences:
1. Only ``http``/``https`` on ports 80/443; no credentials in the URL; optional host allowlist.
2. The hostname is resolved once and **every** resolved address must be globally routable
   (rejects loopback, private, link-local incl. cloud metadata 169.254.169.254, CGNAT,
   multicast, reserved, and IPv4-mapped variants).
3. The connection is pinned to the validated IP (URL rewritten to the IP, ``Host`` header and
   TLS SNI set to the hostname, certificate still verified against the hostname), so a DNS
   rebinding between check and connect cannot redirect the request.
4. Redirects are followed manually, at most ``max_redirects`` times, re-validating each hop.
5. Response size is capped while streaming, and only text/document content types are accepted.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
}
ALLOWED_PORTS = {None, 80, 443}
USER_AGENT = "AtlasAI-Ingest/0.1 (+https://github.com/; document ingestion)"

Resolver = Callable[[str, int], Awaitable[list[str]]]


class UrlRejectedError(Exception):
    """The URL is not allowed (scheme, host, port, or resolved address)."""


class UrlFetchError(Exception):
    """The URL was allowed but could not be fetched successfully."""


@dataclass
class FetchedResource:
    url: str
    final_url: str
    content: bytes
    content_type: str


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", maxsplit=1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback)


def validate_url(url: str, allowed_hosts: list[str]) -> tuple[str, str, int]:
    """Return ``(scheme, hostname, port)`` or raise ``UrlRejectedError``."""
    if len(url) > 2048:
        raise UrlRejectedError("URL is too long")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise UrlRejectedError("Only http and https URLs are allowed")
    if parts.username or parts.password:
        raise UrlRejectedError("URLs with embedded credentials are not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UrlRejectedError("URL has no host")
    try:
        port = parts.port
    except ValueError as exc:
        raise UrlRejectedError("URL has an invalid port") from exc
    if port not in ALLOWED_PORTS:
        raise UrlRejectedError("Only the default ports 80 and 443 are allowed")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UrlRejectedError("Internal hostnames are not allowed")
    if allowed_hosts and not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        raise UrlRejectedError("Host is not on the URL allowlist")
    return scheme, host, port or (443 if scheme == "https" else 80)


async def resolve_public(host: str, port: int, resolver: Resolver) -> str:
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            addresses = await resolver(host, port)
        except OSError as exc:
            raise UrlFetchError(f"Could not resolve host {host}") from exc
    if not addresses:
        raise UrlFetchError(f"Could not resolve host {host}")
    blocked = [a for a in addresses if not is_public_address(a)]
    if blocked:
        raise UrlRejectedError("URL resolves to a private or internal network address")
    return addresses[0]


def _pinned_url(url: str, ip: str) -> str:
    parts = urlsplit(url)
    netloc = f"[{ip}]" if ":" in ip else ip
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


async def fetch_url(
    url: str,
    *,
    max_bytes: int,
    timeout: float,  # noqa: ASYNC109 - per-request httpx timeout, not a cancel scope
    max_redirects: int,
    allowed_hosts: list[str],
    resolver: Resolver = system_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchedResource:
    current = url
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), follow_redirects=False, transport=transport
    ) as client:
        for _ in range(max_redirects + 1):
            scheme, host, port = validate_url(current, allowed_hosts)
            ip = await resolve_public(host, port, resolver)
            request = client.build_request(
                "GET",
                _pinned_url(current, ip),
                headers={"Host": host, "User-Agent": USER_AGENT, "Accept": "*/*"},
                extensions={"sni_hostname": host} if scheme == "https" else {},
            )
            try:
                response = await client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise UrlFetchError("The page took too long to respond") from exc
            except httpx.TransportError as exc:
                raise UrlFetchError(f"Could not connect ({type(exc).__name__})") from exc
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise UrlFetchError("Redirect without a Location header")
                    current = urljoin(current, location)
                    continue
                if not response.is_success:
                    raise UrlFetchError(f"The page returned HTTP {response.status_code}")
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise UrlFetchError(f"Unsupported content type {content_type or 'unknown'}")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise UrlFetchError("The page exceeds the maximum ingestion size")
                body = bytearray()
                async for piece in response.aiter_bytes():
                    body.extend(piece)
                    if len(body) > max_bytes:
                        raise UrlFetchError("The page exceeds the maximum ingestion size")
                return FetchedResource(url, current, bytes(body), content_type)
            finally:
                await response.aclose()
    raise UrlFetchError("Too many redirects")
