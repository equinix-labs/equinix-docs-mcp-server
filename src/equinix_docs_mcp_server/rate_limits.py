"""Retry rate-limited and briefly unavailable Equinix API requests.

A 429 (or 408) means the API did not act on the request, so it is safe to
send again on any method. A 502/503/504 may follow a request the API already
acted on, so it is resent only for idempotent methods.

Waits are kept short: an MCP tool call holds up the agent that made it, so
rather than sleeping through a long ``Retry-After`` the last response is
returned and the agent is told when to try again (see ``retry_hint``).
"""

import asyncio
import logging
import os
import random
import time
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable, Optional

import httpx2

logger = logging.getLogger(__name__)

# The API rejected these before acting on them: safe to resend on any method
RETRY_ANY_METHOD = {408, 429}
# Gateway errors: the request may have been applied, so only idempotent
# methods are resent
RETRY_IDEMPOTENT = {502, 503, 504}
IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}

DEFAULT_MAX_RETRIES = 5
# Seconds all waits for one request may add up to
DEFAULT_MAX_WAIT = 15.0


def _env_number(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        number = float(value)
    except ValueError:
        logger.warning(f"Ignoring {name}={value!r}: not a number")
        return default
    return max(number, 0.0)


def retry_settings() -> tuple[int, float]:
    """Read the retry count and wait budget from the environment."""
    max_retries = int(_env_number("EQUINIX_MCP_RETRY_MAX", DEFAULT_MAX_RETRIES))
    max_wait = _env_number("EQUINIX_MCP_RETRY_WAIT", DEFAULT_MAX_WAIT)
    return max_retries, max_wait


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Seconds to wait from a Retry-After header (delay-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(when.timestamp() - time.time(), 0.0)


def is_retryable(method: str, status_code: int) -> bool:
    if status_code in RETRY_ANY_METHOD:
        return True
    return status_code in RETRY_IDEMPOTENT and method.upper() in IDEMPOTENT_METHODS


def backoff(attempt: int) -> float:
    """Exponential backoff with jitter: about 1s, 2s, 4s, ..."""
    return (2**attempt) * random.uniform(0.75, 1.25)


class RetryTransport(httpx2.AsyncBaseTransport):
    """Resend retryable responses within a small total wait budget.

    It sits below the client's auth flow, so a resent request keeps the
    token it was first sent with.
    """

    def __init__(
        self,
        transport: Optional[httpx2.AsyncBaseTransport] = None,
        max_retries: Optional[int] = None,
        max_wait: Optional[float] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        env_retries, env_wait = retry_settings()
        self._transport = transport or httpx2.AsyncHTTPTransport()
        self.max_retries = env_retries if max_retries is None else max_retries
        self.max_wait = env_wait if max_wait is None else max_wait
        self._sleep = sleep

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        # Buffer the body so it can be sent again
        await request.aread()
        waited = 0.0
        attempt = 0
        while True:
            response = await self._transport.handle_async_request(request)
            if attempt >= self.max_retries or not is_retryable(
                request.method, response.status_code
            ):
                return response

            wait = parse_retry_after(response.headers.get("Retry-After"))
            if wait is None:
                wait = backoff(attempt)
            if waited + wait > self.max_wait:
                return response

            await response.aread()
            await response.aclose()
            logger.info(
                f"{request.method} {request.url.path} returned "
                f"{response.status_code}; retrying in {wait:.1f}s"
            )
            # asyncio.sleep, so cancelling the MCP request interrupts the wait
            await self._sleep(wait)
            waited += wait
            attempt += 1

    async def aclose(self) -> None:
        await self._transport.aclose()


def retry_hint(method: str, response: httpx2.Response) -> Optional[str]:
    """Tell the agent whether a failed request was applied and when to retry."""
    status = response.status_code
    method = method.upper()
    if status in RETRY_ANY_METHOD:
        wait = parse_retry_after(response.headers.get("Retry-After"))
        when = (
            f"Retry in about {max(round(wait), 1)} seconds."
            if wait is not None
            else "Wait a little before retrying."
        )
        return f"The Equinix API did not apply this request (HTTP {status}). {when}"
    if status in RETRY_IDEMPOTENT:
        if method in IDEMPOTENT_METHODS:
            return (
                f"HTTP {status} is usually temporary, and this {method} request "
                "is safe to retry."
            )
        return (
            f"The outcome of this {method} is unknown: the API may have applied "
            f"it before returning HTTP {status}. Check the resource's current "
            "state before retrying."
        )
    return None
