"""Deprecation, sunset and retry notices for Equinix API tool calls.

Equinix APIs announce that an endpoint is going away with the
``Deprecation`` header and the date it stops working with ``Sunset``
(RFC 8594), and may link a successor with ``Link`` relations. Those headers
never reach the agent through the tool result, so they are collected from
each API response and appended to it.

A deprecation is mainly advice for whoever calls the API, here the agent.
It is described in full once per client (the server process over stdio,
the MCP session over HTTP) and API family, and in one line after that, so the agent can act on it (use the successor) without
repeating it to the user on every call.
"""

import contextvars
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional

import httpx2
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import ToolResult
from mcp_types import TextContent

from .rate_limits import retry_hint

logger = logging.getLogger(__name__)

# Link relations worth passing on: RFC 8594 (sunset), RFC 9745 (deprecation)
# and RFC 5829 (successor-version, latest-version)
NOTICE_LINK_RELS = (
    "successor-version",
    "latest-version",
    "alternate",
    "deprecation",
    "sunset",
)

# Distinct notices remembered per server before the oldest are forgotten
MAX_SEEN_NOTICES = 10_000

_LINK_RE = re.compile(r"<([^>]*)>([^,<]*)")
_REL_RE = re.compile(r'\brel\s*=\s*"?([^";]+)"?', re.IGNORECASE)

# Notices recorded by API responses during the current tool call
_notices: contextvars.ContextVar[Optional[list["ApiNotice"]]] = contextvars.ContextVar(
    "equinix_api_notices", default=None
)


def parse_http_date(value: Optional[str]) -> Optional[datetime]:
    """Parse an HTTP-date or an RFC 9745 ``@<epoch seconds>`` date."""
    if not value:
        return None
    value = value.strip()
    if value.startswith("@"):
        try:
            return datetime.fromtimestamp(int(value[1:]), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def parse_links(value: Optional[str]) -> list[tuple[str, str]]:
    """Return (rel, url) pairs for the notice relations in a Link header."""
    links: list[tuple[str, str]] = []
    for url, params in _LINK_RE.findall(value or ""):
        match = _REL_RE.search(params)
        if not match:
            continue
        for rel in match.group(1).lower().split():
            if rel in NOTICE_LINK_RELS:
                links.append((rel, url.strip()))
    return links


@dataclass(frozen=True)
class ApiNotice:
    """Deprecation and sunset details from one API response."""

    method: str
    url: str
    status_code: int
    deprecated: bool
    deprecated_since: Optional[datetime]
    sunset: Optional[datetime]
    links: tuple[tuple[str, str], ...]

    @property
    def sunset_passed(self) -> bool:
        return self.sunset is not None and self.sunset <= datetime.now(timezone.utc)

    def link(self, *rels: str) -> Optional[str]:
        for rel in rels:
            for link_rel, url in self.links:
                if link_rel == rel:
                    return url
        return None


def notice_from_response(response: httpx2.Response) -> Optional[ApiNotice]:
    """Build a notice when a response carries Deprecation or Sunset headers."""
    deprecation = response.headers.get("Deprecation")
    sunset = response.headers.get("Sunset")
    if not deprecation and not sunset:
        return None
    return ApiNotice(
        method=response.request.method,
        url=str(response.request.url.copy_with(query=None)),
        status_code=response.status_code,
        # A Sunset header alone is not a deprecation. Early drafts of the
        # Deprecation header used "true" rather than a date.
        deprecated=bool(deprecation),
        deprecated_since=parse_http_date(deprecation),
        sunset=parse_http_date(sunset),
        links=tuple(parse_links(response.headers.get("Link"))),
    )


async def record_response(response: httpx2.Response) -> None:
    """httpx response hook: note deprecation headers for the running tool call."""
    notices = _notices.get()
    if notices is None:
        return
    notice = notice_from_response(response)
    if notice is not None:
        notices.append(notice)


def _day(when: Optional[datetime]) -> str:
    return when.strftime("%Y-%m-%d") if when else ""


def describe_notice(notice: ApiNotice, successor_tool: Optional[str] = None) -> str:
    """The full notice, given once per client and API family."""
    path = httpx2.URL(notice.url).path
    what = f"{notice.method} {path}"
    parts = []
    if notice.deprecated:
        since = (
            f" since {_day(notice.deprecated_since)}" if notice.deprecated_since else ""
        )
        parts.append(f"is deprecated{since}")
    if notice.sunset_passed:
        parts.append(f"reached its sunset date on {_day(notice.sunset)}")
    elif notice.sunset:
        parts.append(f"is scheduled to stop working on {_day(notice.sunset)}")
    lines = [f"Equinix API notice: {what} {' and '.join(parts)}."]

    successor = notice.link("successor-version", "latest-version", "alternate")
    if successor:
        tool = f" (tool {successor_tool})" if successor_tool else ""
        lines.append(f"Successor: {successor}{tool}.")
    info = notice.link("deprecation", "sunset")
    if info:
        lines.append(f"More information: {info}")
    lines.append(
        "This is advice for the caller: use the successor, or a newer version "
        "of this API found with search_tools, where one exists. Tell the user "
        "only if it affects their resources or plans, not on every call."
    )
    return "\n".join(lines)


def describe_notice_briefly(notice: ApiNotice) -> str:
    """The one-line reminder for later calls from the same client and family."""
    path = httpx2.URL(notice.url).path
    when = f"; sunset {_day(notice.sunset)}" if notice.sunset else ""
    state = "deprecated" if notice.deprecated else "scheduled for retirement"
    return (
        f"Equinix API notice: {notice.method} {path} is {state}{when} "
        "(described earlier in this session)."
    )


def describe_sunset_failure(notice: ApiNotice) -> Optional[str]:
    """Explain a failure from an endpoint that is past its sunset or gone."""
    if notice.status_code == 410 or (
        notice.sunset_passed and notice.status_code == 404
    ):
        return (
            "This endpoint appears to have been retired, so retrying won't "
            "help. Use the successor if one is linked above, or search_tools "
            "for a newer version of this API."
        )
    return None


class _ToolIndex:
    """Find the tool for a URL, e.g. a successor-version link."""

    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern[str], str]] = []

    def add(self, method: str, path: str, tool_name: str) -> None:
        # Each {param} in the path template matches one path segment
        parts = re.split(r"\{[^}/]+\}", path)
        pattern = "[^/]+".join(re.escape(part) for part in parts)
        self._routes.append((method.upper(), re.compile(f"^{pattern}/?$"), tool_name))

    def find(self, url: str, method: str = "GET") -> Optional[str]:
        """The tool for a URL's path, preferring one with the same method."""
        path = httpx2.URL(url).path
        matches = [(m, name) for m, rx, name in self._routes if rx.match(path)]
        for m, name in matches:
            if m == method.upper():
                return name
        return matches[0][1] if matches else None


def _http_error_response(error: BaseException) -> Optional[httpx2.Response]:
    """The upstream response behind a tool error, if any."""
    seen: set[int] = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx2.HTTPStatusError):
            return current.response
        current = current.__cause__ or current.__context__
    return None


class ApiNoticeMiddleware(Middleware):
    """Append deprecation, sunset and retry advice to API tool results.

    Only the outermost tool call adds notices: ``call_tool`` from the search
    transform and ``call_api_filtered`` run the API tool as a nested call,
    and the notice belongs on the result the agent receives.

    Add it before ``ResponseFormattingMiddleware`` so it runs outside it and
    the text it appends isn't replaced by the YAML rendering.
    """

    def __init__(self, api_names: list[str]):
        self._api_prefixes = tuple(f"{name}_" for name in api_names)
        self._seen: dict[tuple[Any, ...], None] = {}
        self.tools = _ToolIndex()

    def component_hook(self, namespace: str) -> Callable[[Any, Any], None]:
        """An ``mcp_component_fn`` that indexes tools by path for successors."""

        def hook(route: Any, component: Any) -> None:
            if isinstance(component, OpenAPITool):
                self.tools.add(
                    route.method, route.path, f"{namespace}_{component.name}"
                )

        return hook

    def _family(self, tool_name: str) -> str:
        for prefix in self._api_prefixes:
            if tool_name.startswith(prefix):
                return prefix[:-1]
        return tool_name

    @staticmethod
    def _api_tool_name(context: MiddlewareContext) -> str:
        name = getattr(context.message, "name", "") or ""
        arguments = getattr(context.message, "arguments", None) or {}
        # call_tool and call_api_filtered name the API tool they run
        inner = arguments.get("name") if isinstance(arguments, dict) else None
        return inner if isinstance(inner, str) and inner else name

    @staticmethod
    def _session_id() -> Optional[str]:
        """The HTTP session, if any. Over stdio a server has one client."""
        # Context.session_id isn't stable across requests on every transport
        return get_http_headers(include={"mcp-session-id"}).get("mcp-session-id")

    def _first_time(self, key: tuple[Any, ...]) -> bool:
        if key in self._seen:
            return False
        if len(self._seen) >= MAX_SEEN_NOTICES:
            self._seen.pop(next(iter(self._seen)))
        self._seen[key] = None
        return True

    def _describe(
        self, context: MiddlewareContext, notices: list[ApiNotice], always: bool
    ) -> list[str]:
        family = self._family(self._api_tool_name(context))
        session = self._session_id()
        lines: list[str] = []
        shown: set[tuple[Any, ...]] = set()
        for notice in notices:
            key = (
                session,
                family,
                notice.deprecated,
                notice.deprecated_since,
                notice.sunset,
            )
            if key in shown:
                continue
            shown.add(key)
            if self._first_time(key) or always:
                successor = notice.link(
                    "successor-version", "latest-version", "alternate"
                )
                tool = self.tools.find(successor, notice.method) if successor else None
                lines.append(describe_notice(notice, tool))
            else:
                lines.append(describe_notice_briefly(notice))
        return lines

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ) -> ToolResult:
        if _notices.get() is not None:
            return await call_next(context)

        notices: list[ApiNotice] = []
        token = _notices.set(notices)
        try:
            result = await call_next(context)
        except Exception as e:
            response = _http_error_response(e)
            if response is None and not notices:
                raise
            # Errors always get the full notice: the failure may be the
            # endpoint going away
            extra = self._describe(context, notices, always=True)
            if response is not None:
                hint = retry_hint(response.request.method, response)
                if hint:
                    extra.append(hint)
            for notice in notices:
                failure = describe_sunset_failure(notice)
                if failure:
                    extra.append(failure)
                    break
            if not extra:
                raise
            raise ToolError("\n\n".join([str(e), *extra])) from e
        finally:
            _notices.reset(token)

        if not notices:
            return result
        lines = self._describe(context, notices, always=False)
        content = list(result.content or [])
        content.append(TextContent(type="text", text="\n\n".join(lines)))
        return ToolResult(
            content=content,
            structured_content=result.structured_content,
            meta=result.meta,
        )
