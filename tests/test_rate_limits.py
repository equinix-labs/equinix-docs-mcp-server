"""Tests for retrying rate-limited Equinix API requests."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx2
import pytest

from equinix_docs_mcp_server.rate_limits import (
    RetryTransport,
    backoff,
    parse_retry_after,
    retry_hint,
    retry_settings,
)

URL = "https://api.equinix.com/fabric/v4/ports"


class Recorder:
    """A mock API that returns the given responses in order."""

    def __init__(self, *responses: httpx2.Response):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def make_transport(api: Recorder, **kwargs):
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    transport = RetryTransport(
        httpx2.MockTransport(api), sleep=sleep, **{"max_retries": 5, **kwargs}
    )
    return transport, waits


async def send(transport, method="GET", content=None) -> httpx2.Response:
    async with httpx2.AsyncClient(transport=transport) as client:
        return await client.request(method, URL, content=content)


@pytest.mark.asyncio
async def test_waits_for_retry_after_then_succeeds():
    api = Recorder(
        httpx2.Response(429, headers={"Retry-After": "2"}),
        httpx2.Response(200, json={"data": []}),
    )
    transport, waits = make_transport(api, max_wait=15)

    response = await send(transport)

    assert response.status_code == 200
    assert waits == [2.0]
    assert len(api.requests) == 2


@pytest.mark.asyncio
async def test_post_body_is_resent_intact():
    api = Recorder(
        httpx2.Response(429, headers={"Retry-After": "1"}),
        httpx2.Response(201),
    )
    transport, _ = make_transport(api, max_wait=15)

    await send(transport, "POST", content=b'{"name": "conn"}')

    assert [r.content for r in api.requests] == [b'{"name": "conn"}'] * 2


@pytest.mark.asyncio
async def test_retry_after_over_budget_returns_without_waiting():
    """A long Retry-After isn't slept through; the agent is told instead."""
    api = Recorder(httpx2.Response(429, headers={"Retry-After": "60"}))
    transport, waits = make_transport(api, max_wait=15)

    response = await send(transport)

    assert response.status_code == 429
    assert waits == []


@pytest.mark.asyncio
async def test_waits_share_one_budget():
    api = Recorder(
        *[httpx2.Response(429, headers={"Retry-After": "6"}) for _ in range(3)]
    )
    transport, waits = make_transport(api, max_wait=15)

    response = await send(transport)

    assert response.status_code == 429
    assert waits == [6.0, 6.0]


@pytest.mark.asyncio
async def test_max_retries():
    api = Recorder(*[httpx2.Response(429, headers={"Retry-After": "0"})] * 3)
    transport, waits = make_transport(api, max_retries=2, max_wait=15)

    response = await send(transport)

    assert response.status_code == 429
    assert len(api.requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("method, retried", [("GET", True), ("POST", False)])
async def test_gateway_errors_retried_only_when_idempotent(method, retried):
    api = Recorder(
        httpx2.Response(502, headers={"Retry-After": "0"}), httpx2.Response(200)
    )
    transport, _ = make_transport(api, max_wait=15)

    response = await send(transport, method)

    assert response.status_code == (200 if retried else 502)


@pytest.mark.asyncio
async def test_backoff_without_retry_after():
    api = Recorder(httpx2.Response(429), httpx2.Response(429), httpx2.Response(200))
    transport, waits = make_transport(api, max_wait=15)

    await send(transport)

    assert len(waits) == 2
    assert 0.75 <= waits[0] <= 1.25 and 1.5 <= waits[1] <= 2.5


def test_backoff_grows():
    assert backoff(3) > backoff(0)


def test_parse_retry_after():
    soon = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert parse_retry_after("12") == 12.0
    assert 25 <= parse_retry_after(format_datetime(soon, usegmt=True)) <= 30
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


def test_retry_settings(monkeypatch):
    monkeypatch.setenv("EQUINIX_MCP_RETRY_MAX", "0")
    monkeypatch.setenv("EQUINIX_MCP_RETRY_WAIT", "60")
    assert retry_settings() == (0, 60.0)

    monkeypatch.setenv("EQUINIX_MCP_RETRY_MAX", "lots")
    monkeypatch.delenv("EQUINIX_MCP_RETRY_WAIT")
    assert retry_settings() == (5, 15.0)


def response(status: int, method: str = "GET", **headers) -> httpx2.Response:
    return httpx2.Response(status, headers=headers, request=httpx2.Request(method, URL))


@pytest.mark.parametrize(
    "status, method, headers, expected",
    [
        (
            429,
            "POST",
            {"Retry-After": "42"},
            "did not apply this request (HTTP 429). Retry in about 42 seconds",
        ),
        (429, "GET", {}, "Wait a little before retrying"),
        (503, "GET", {}, "this GET request is safe to retry"),
        (502, "POST", {}, "The outcome of this POST is unknown"),
    ],
)
def test_retry_hint(status, method, headers, expected):
    assert expected in retry_hint(method, response(status, method, **headers))


def test_no_hint_for_other_errors():
    assert retry_hint("GET", response(400)) is None
