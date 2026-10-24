"""SSRF defences for URL ingestion. DNS and HTTP are faked; nothing touches the network."""

import httpx
import pytest

from app.ingestion.url_fetch import (
    UrlFetchError,
    UrlRejectedError,
    fetch_url,
    is_public_address,
    validate_url,
)

PUBLIC_IP = "93.184.215.14"


def resolver_for(mapping: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        return mapping[host]

    return resolve


async def fetch(  # type: ignore[no-untyped-def]
    url: str, handler, mapping: dict[str, list[str]] | None = None, max_bytes: int = 10_000
):
    return await fetch_url(
        url,
        max_bytes=max_bytes,
        timeout=5.0,
        max_redirects=2,
        allowed_hosts=[],
        resolver=resolver_for(mapping or {"docs.example.com": [PUBLIC_IP]}),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "240.0.0.1",
    ],
)
def test_non_public_addresses_are_detected(address: str) -> None:
    assert is_public_address(address) is False


def test_public_address_is_allowed() -> None:
    assert is_public_address(PUBLIC_IP) is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "http://user:pw@example.com/",
        "http://localhost/admin",
        "http://metadata.google.internal/",
        "http://printer.local/",
        "http://example.com:8080/",
        "http:///nohost",
    ],
)
def test_disallowed_urls_are_rejected(url: str) -> None:
    with pytest.raises(UrlRejectedError):
        validate_url(url, [])


def test_allowlist_restricts_hosts() -> None:
    validate_url("https://docs.example.com/page", ["example.com"])
    with pytest.raises(UrlRejectedError, match="allowlist"):
        validate_url("https://evil.test/page", ["example.com"])


async def test_hostname_resolving_to_private_ip_is_rejected() -> None:
    with pytest.raises(UrlRejectedError, match="private"):
        await fetch(
            "https://rebind.example.com/",
            lambda r: httpx.Response(200),
            {"rebind.example.com": ["10.1.2.3"]},
        )


async def test_mixed_public_and_private_resolution_is_rejected() -> None:
    with pytest.raises(UrlRejectedError):
        await fetch(
            "https://mixed.example.com/",
            lambda r: httpx.Response(200),
            {"mixed.example.com": [PUBLIC_IP, "127.0.0.1"]},
        )


async def test_ip_literal_url_to_metadata_service_is_rejected() -> None:
    with pytest.raises(UrlRejectedError):
        await fetch("http://169.254.169.254/latest/meta-data/", lambda r: httpx.Response(200))


async def test_connection_is_pinned_to_validated_ip_with_original_host() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>hi</p>")

    result = await fetch("https://docs.example.com/guide?x=1", handler)
    assert result.content == b"<p>hi</p>"
    assert seen[0].url.host == PUBLIC_IP
    assert seen[0].headers["host"] == "docs.example.com"
    assert seen[0].extensions["sni_hostname"] == "docs.example.com"
    assert seen[0].url.query == b"x=1"


async def test_redirect_to_private_address_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.example.com/secret"})

    with pytest.raises(UrlRejectedError):
        await fetch(
            "https://docs.example.com/",
            handler,
            {"docs.example.com": [PUBLIC_IP], "internal.example.com": ["192.168.0.10"]},
        )


async def test_redirect_loop_is_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "https://docs.example.com/again"})

    with pytest.raises(UrlFetchError, match="Too many redirects"):
        await fetch("https://docs.example.com/", handler)


async def test_oversized_body_is_rejected_while_streaming() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x" * 20_000)

    with pytest.raises(UrlFetchError, match="maximum"):
        await fetch("https://docs.example.com/big", handler)


async def test_unsupported_content_type_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b".."
        )

    with pytest.raises(UrlFetchError, match="content type"):
        await fetch("https://docs.example.com/file.bin", handler)


async def test_http_error_status_is_reported() -> None:
    with pytest.raises(UrlFetchError, match="404"):
        await fetch("https://docs.example.com/missing", lambda r: httpx.Response(404))
