"""HTTP for Arazzo workflows, behaving like the API tools' clients.

arazzo-runner sends its requests synchronously through a ``requests``
session, and the server runs it in a worker thread so the event loop stays
free. ``WorkflowSession`` gives those requests what the API tools' httpx
clients have:

* an OAuth token from the ``AuthManager`` on each request, renewed once
  after a 401;
* the retry policy and wait budget of ``rate_limits``;
* Deprecation and Sunset notices recorded for the running tool call;
* a timeout on each request.

It also enforces the workflow's time limit. A running thread can't be
stopped, so once the deadline passes (or the tool call is cancelled) the
session refuses to send anything more, and the runner's next step fails.
"""

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

import requests

from .api_notices import notice_from_headers, record_notice
from .rate_limits import _env_number, retry_settings, retry_wait

logger = logging.getLogger(__name__)

# Seconds one workflow run may take, from the first request to the last
DEFAULT_WORKFLOW_TIMEOUT = 60.0
WORKFLOW_TIMEOUT_ENV = "EQUINIX_MCP_WORKFLOW_TIMEOUT"
# Seconds one request may take, as for the API tools' clients
REQUEST_TIMEOUT = 30.0
USER_AGENT = "Equinix-MCP-Server/1.0.0"


def workflow_timeout() -> float:
    """The time limit for one workflow run, from the environment."""
    return _env_number(WORKFLOW_TIMEOUT_ENV, DEFAULT_WORKFLOW_TIMEOUT)


class WorkflowStopped(Exception):
    """The session refused a request: the run is out of time or cancelled."""


class WorkflowDeadlineExceeded(WorkflowStopped):
    pass


class WorkflowCancelled(WorkflowStopped):
    pass


def _without_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class WorkflowSession(requests.Session):
    """A ``requests`` session for one workflow run, used from a worker thread.

    ``loop`` is the server's event loop: the ``AuthManager`` is async, so
    tokens are fetched by scheduling it there and waiting in this thread.

    An ``Authorization`` header the workflow sets itself (from a
    ``bearerToken`` input the caller passed) is left alone. One the server
    supplied, including a ``bearerToken`` it filled in (``managed_tokens``),
    is replaced with the current token and renewed after a 401.
    """

    def __init__(
        self,
        *,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        auth_manager: Any = None,
        timeout: Optional[float] = None,
        managed_tokens: Iterable[str] = (),
        request_timeout: float = REQUEST_TIMEOUT,
        max_retries: Optional[int] = None,
        max_wait: Optional[float] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__()
        self.headers["User-Agent"] = USER_AGENT
        env_retries, env_wait = retry_settings()
        self.max_retries = env_retries if max_retries is None else max_retries
        self.max_wait = env_wait if max_wait is None else max_wait
        self.request_timeout = request_timeout
        self.time_limit = workflow_timeout() if timeout is None else timeout
        self._loop = loop
        self._auth_manager = auth_manager
        self._managed = {f"Bearer {t}" for t in managed_tokens if t}
        self._clock = clock
        self._cancelled = threading.Event()
        self._sleep = sleep or self._cancellable_sleep
        self.deadline = clock() + self.time_limit if self.time_limit else None
        # Why the session stopped sending, once it has
        self.stopped: Optional[WorkflowStopped] = None
        # (method, path, status) of the last response received
        self.last_response: Optional[tuple[str, str, int]] = None

    # Deadline and cancellation

    def remaining(self) -> Optional[float]:
        """Seconds left before the deadline, or None without one."""
        if self.deadline is None:
            return None
        return self.deadline - self._clock()

    def cancel(self) -> None:
        """Refuse further requests, e.g. because the tool call was cancelled."""
        self._cancelled.set()

    def check(self) -> None:
        """Raise if the run may not send any more requests."""
        if self.stopped is not None:
            raise self.stopped
        if self._cancelled.is_set():
            self.stopped = WorkflowCancelled("The workflow run was cancelled")
            raise self.stopped
        remaining = self.remaining()
        if remaining is not None and remaining <= 0:
            self.stopped = WorkflowDeadlineExceeded(
                f"The workflow ran past its {self.time_limit:g}s time limit"
            )
            raise self.stopped

    def pause(self, seconds: float) -> None:
        """Wait between steps, stopping the run if the wait would outlast it."""
        self.check()
        remaining = self.remaining()
        if remaining is not None and seconds >= remaining:
            self.stopped = WorkflowDeadlineExceeded(
                f"Waiting {seconds:g}s would run past the workflow's "
                f"{self.time_limit:g}s time limit"
            )
            raise self.stopped
        if seconds > 0:
            self._sleep(seconds)
        self.check()

    def _cancellable_sleep(self, seconds: float) -> None:
        self._cancelled.wait(seconds)

    def _time_left(self, limit: float) -> float:
        remaining = self.remaining()
        return limit if remaining is None else max(min(limit, remaining), 0.001)

    # Authentication

    def _auth_header(self, refresh: bool = False) -> Optional[str]:
        """The current Authorization value from the AuthManager, if any."""
        manager, loop = self._auth_manager, self._loop
        if manager is None or loop is None:
            return None

        async def fetch() -> dict:
            if refresh:
                manager.clear_token_cache()
            return await manager._get_client_credentials_auth_header()

        future = asyncio.run_coroutine_threadsafe(fetch(), loop)
        try:
            headers = future.result(timeout=self._time_left(self.request_timeout))
        except Exception as e:
            future.cancel()
            logger.error(f"Auth error for workflow request: {e}")
            return None
        value = headers.get("Authorization")
        if value:
            self._managed.add(value)
        return value

    # Requests

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        self.check()
        headers = dict(kwargs.pop("headers", None) or {})
        auth_key = next((k for k in headers if k.lower() == "authorization"), None)
        managed = auth_key is None or headers[auth_key] in self._managed
        if managed:
            if auth_key is not None:
                del headers[auth_key]
            value = self._auth_header()
            if value:
                headers["Authorization"] = value

        response = self._send(method, url, headers, args, kwargs)
        if response.status_code == 401 and managed and "Authorization" in headers:
            # The token may have been revoked or expired early: fetch a new
            # one and retry once
            logger.info(f"401 from {method} {url}; retrying with a new token")
            value = self._auth_header(refresh=True)
            if value:
                response.close()
                headers["Authorization"] = value
                response = self._send(method, url, headers, args, kwargs)
        return response

    def _send(self, method, url, headers, args, kwargs):
        """Send, resending rate-limited responses within the wait budget."""
        waited = 0.0
        attempt = 0
        caller_timeout = kwargs.pop("timeout", None)
        limit = (
            caller_timeout
            if isinstance(caller_timeout, (int, float))
            else self.request_timeout
        )
        while True:
            self.check()
            response = super().request(
                method,
                url,
                *args,
                headers=headers,
                timeout=self._time_left(limit),
                **kwargs,
            )
            self._note(method, response)
            wait = retry_wait(
                method,
                response.status_code,
                response.headers.get("Retry-After"),
                attempt,
                waited,
                self.max_retries,
                self.max_wait,
            )
            remaining = self.remaining()
            if wait is None or (remaining is not None and wait >= remaining):
                return response
            response.close()
            logger.info(
                f"{method} {urlsplit(url).path} returned {response.status_code}; "
                f"retrying in {wait:.1f}s"
            )
            self._sleep(wait)
            waited += wait
            attempt += 1

    def _note(self, method: str, response: requests.Response) -> None:
        request_url = getattr(response.request, "url", None) or response.url
        url = _without_query(str(request_url))
        self.last_response = (method.upper(), urlsplit(url).path, response.status_code)
        record_notice(
            notice_from_headers(
                method.upper(), url, response.status_code, response.headers
            )
        )
