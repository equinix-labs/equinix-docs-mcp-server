"""Tests for deprecation, sunset and retry notices on API tool calls."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastmcp import Client
from test_main import make_server

from equinix_docs_mcp_server.api_notices import (
    describe_notice,
    notice_from_response,
    parse_http_date,
    parse_links,
)

DEPRECATED = {
    "Deprecation": "Fri, 31 Dec 2021 23:59:59 GMT",
    "Sunset": "Sat, 31 Dec 2099 23:59:59 GMT",
    "Link": (
        '<https://api.equinix.com/fabric/v5/ports>; rel="successor-version", '
        '<https://docs.equinix.com/fabric/deprecations>; rel="deprecation"; '
        'type="text/html"'
    ),
}
PAST_SUNSET = {"Sunset": "Sat, 31 Dec 2022 23:59:59 GMT"}

NOTICE_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Fabric", "version": "1.0.0"},
    "paths": {
        "/fabric/v4/ports": {
            "get": {
                "operationId": "getPorts",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/fabric/v4/ports/{portId}": {
            "get": {
                "parameters": [
                    {
                        "name": "portId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "operationId": "getPort",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/fabric/v5/ports": {
            "get": {
                "operationId": "listPorts",
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


def make_response(status=200, headers=None, method="GET"):
    request = httpx2.Request(method, "https://api.equinix.com/fabric/v4/ports?limit=5")
    return httpx2.Response(status, headers=headers or {}, request=request)


def test_parse_http_date():
    expected = datetime(2021, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert parse_http_date("Fri, 31 Dec 2021 23:59:59 GMT") == expected
    # RFC 9745 structured date
    assert parse_http_date(f"@{int(expected.timestamp())}") == expected
    assert parse_http_date("true") is None
    assert parse_http_date(None) is None


def test_parse_links():
    assert parse_links(DEPRECATED["Link"]) == [
        ("successor-version", "https://api.equinix.com/fabric/v5/ports"),
        ("deprecation", "https://docs.equinix.com/fabric/deprecations"),
    ]
    assert parse_links('<https://x>; rel="next"') == []


def test_notice_from_response():
    notice = notice_from_response(make_response(headers=DEPRECATED))

    assert notice.deprecated
    assert notice.deprecated_since.year == 2021
    assert notice.sunset.year == 2099 and not notice.sunset_passed
    assert notice.url == "https://api.equinix.com/fabric/v4/ports"
    assert notice_from_response(make_response()) is None


def test_sunset_alone_is_not_a_deprecation():
    notice = notice_from_response(make_response(headers=PAST_SUNSET))
    assert not notice.deprecated and notice.sunset_passed


def test_deprecation_true_from_early_drafts():
    notice = notice_from_response(make_response(headers={"Deprecation": "true"}))
    assert notice.deprecated and notice.deprecated_since is None


def test_describe_notice():
    notice = notice_from_response(make_response(headers=DEPRECATED))
    text = describe_notice(notice, "fabric_listPorts")

    assert "GET /fabric/v4/ports is deprecated since 2021-12-31" in text
    assert "scheduled to stop working on 2099-12-31" in text
    assert "(tool fabric_listPorts)" in text
    assert "https://docs.equinix.com/fabric/deprecations" in text
    assert "not on every call" in text


class FakeApi:
    """Answer each request with the next (status, headers) pair."""

    def __init__(self, *responses):
        self.responses = list(responses)

    def __call__(self, request):
        status, headers = self.responses.pop(0) if self.responses else (200, {})
        return httpx2.Response(status, headers=headers, json={"data": []})


async def notice_server(api: FakeApi, tool_catalog="full"):
    server = make_server(tool_catalog=tool_catalog)
    server.spec_manager.get_provider_spec.return_value = NOTICE_SPEC
    server.auth_manager.get_auth_header = AsyncMock(return_value={})
    await server.initialize()
    for client in server._api_clients:
        client._transport._transport = httpx2.MockTransport(api)
        client._transport._sleep = AsyncMock()
    return server


def texts(result) -> str:
    return "\n".join(c.text for c in result.content)


@pytest.mark.asyncio
async def test_deprecation_notice_once_per_session_then_brief():
    api = FakeApi((200, DEPRECATED), (200, DEPRECATED), (200, {}))
    server = await notice_server(api)

    async with Client(server.mcp) as client:
        first = await client.call_tool("fabric_getPorts", {})
        second = await client.call_tool("fabric_getPort", {"portId": "p1"})
        plain = await client.call_tool("fabric_getPorts", {})

    assert (
        "Successor: https://api.equinix.com/fabric/v5/ports (tool fabric_listPorts)"
        in texts(first)
    )
    # The YAML rendering of the response is kept
    assert first.content[0].text == "data: []\n"
    assert first.structured_content == {"data": []}
    assert "described earlier in this session" in texts(second)
    assert "Successor" not in texts(second)
    assert "notice" not in texts(plain)


@pytest.mark.asyncio
async def test_notice_reaches_nested_calls():
    """call_tool from the search catalog and call_api_filtered keep notices."""
    api = FakeApi((200, DEPRECATED), (200, DEPRECATED))
    server = await notice_server(api, tool_catalog="search")

    async with Client(server.mcp) as client:
        via_search = await client.call_tool(
            "call_tool", {"name": "fabric_getPorts", "arguments": {}}
        )
        filtered = await client.call_tool(
            "call_api_filtered", {"name": "fabric_getPorts", "query": "data"}
        )

    assert texts(via_search).count("Equinix API notice") == 1
    assert "Successor" in texts(via_search)
    assert "described earlier" in texts(filtered)


@pytest.mark.asyncio
async def test_rate_limit_error_says_not_applied():
    api = FakeApi((429, {"Retry-After": "120"}))
    server = await notice_server(api)

    async with Client(server.mcp) as client:
        result = await client.call_tool("fabric_getPorts", {}, raise_on_error=False)

    assert result.is_error
    text = texts(result)
    assert "did not apply this request (HTTP 429). Retry in about 120 seconds" in text
    assert text.count("did not apply") == 1


@pytest.mark.asyncio
async def test_rate_limit_retried_within_budget():
    api = FakeApi((429, {"Retry-After": "1"}), (200, {}))
    server = await notice_server(api)

    async with Client(server.mcp) as client:
        result = await client.call_tool("fabric_getPorts", {})

    assert not result.is_error


@pytest.mark.asyncio
async def test_past_sunset_failure_says_retired():
    api = FakeApi((410, PAST_SUNSET))
    server = await notice_server(api)

    async with Client(server.mcp) as client:
        result = await client.call_tool("fabric_getPorts", {}, raise_on_error=False)

    text = texts(result)
    assert "reached its sunset date on 2022-12-31" in text
    assert "appears to have been retired" in text


@pytest.mark.asyncio
async def test_other_errors_unchanged():
    api = FakeApi((400, {}))
    server = await notice_server(api)

    async with Client(server.mcp) as client:
        result = await client.call_tool("fabric_getPorts", {}, raise_on_error=False)

    assert result.is_error
    assert "notice" not in texts(result) and "retry" not in texts(result).lower()
