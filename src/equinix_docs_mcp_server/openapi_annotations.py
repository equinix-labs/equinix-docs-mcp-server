"""Derive MCP tool annotations from OpenAPI operations.

Used as the ``mcp_component_fn`` hook of FastMCP's ``OpenAPIProvider`` so
every generated API tool advertises behavior hints (read-only, destructive,
idempotent, open-world) that clients can use for approval and safety UX.

Policy (conservative — hints only ever err toward "may modify state"):

* ``GET``/``HEAD``/``OPTIONS``: read-only (not destructive, idempotent).
* ``PUT``: replaces state — destructive and idempotent.
* ``DELETE``: destructive and idempotent.
* ``POST``: not read-only, not idempotent; destructiveness left unset so the
  MCP default (``true``) applies, since POST is often used for actions.
* ``PATCH``: not read-only, not idempotent (JSON Patch and similar are not
  guaranteed idempotent); destructiveness left unset (MCP default ``true``).
* Every tool: ``openWorldHint`` is true — all of them call external Equinix
  APIs.

Precedence, lowest to highest: method-derived defaults, then an operation's
``x-mcp-annotations`` vendor extension (camelCase or snake_case keys), then
any annotations already set on the component.
"""

import logging
from typing import Any, Dict

from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.utilities.openapi import HTTPRoute
from mcp_types import ToolAnnotations

logger = logging.getLogger(__name__)

ANNOTATIONS_EXTENSION = "x-mcp-annotations"

_READ_ONLY: Dict[str, Any] = {
    "read_only_hint": True,
    "destructive_hint": False,
    "idempotent_hint": True,
}

METHOD_ANNOTATIONS: Dict[str, Dict[str, Any]] = {
    "GET": _READ_ONLY,
    "HEAD": _READ_ONLY,
    "OPTIONS": _READ_ONLY,
    "PUT": {
        "read_only_hint": False,
        "destructive_hint": True,
        "idempotent_hint": True,
    },
    "DELETE": {
        "read_only_hint": False,
        "destructive_hint": True,
        "idempotent_hint": True,
    },
    "POST": {"read_only_hint": False, "idempotent_hint": False},
    "PATCH": {"read_only_hint": False, "idempotent_hint": False},
}


def _hints(annotations: Any) -> Dict[str, Any]:
    """Return the explicitly set (non-None) fields of a ToolAnnotations."""
    if annotations is None:
        return {}
    if isinstance(annotations, ToolAnnotations):
        return annotations.model_dump(exclude_none=True)
    # Vendor extension: a plain mapping with camelCase or snake_case keys.
    return ToolAnnotations.model_validate(annotations).model_dump(exclude_none=True)


def derive_tool_annotations(
    route: HTTPRoute, existing: ToolAnnotations | None = None
) -> ToolAnnotations:
    """Build annotations for an OpenAPI route, merged with existing ones."""
    merged: Dict[str, Any] = {"open_world_hint": True}
    merged.update(METHOD_ANNOTATIONS.get(route.method.upper(), {}))

    override = route.extensions.get(ANNOTATIONS_EXTENSION)
    if override is not None:
        try:
            merged.update(_hints(override))
        except Exception as e:
            logger.warning(
                f"Ignoring invalid {ANNOTATIONS_EXTENSION} on "
                f"{route.method} {route.path}: {e}"
            )

    merged.update(_hints(existing))

    if "title" not in merged and route.summary:
        merged["title"] = route.summary

    return ToolAnnotations(**merged)


def annotate_openapi_component(route: HTTPRoute, component: Any) -> None:
    """``mcp_component_fn`` hook: attach derived annotations to API tools.

    Resources and resource templates are left untouched.
    """
    if not isinstance(component, OpenAPITool):
        return
    component.annotations = derive_tool_annotations(route, component.annotations)
