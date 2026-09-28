"""Test MCP tool annotations derived from OpenAPI HTTP methods."""

import httpx2
import pytest
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import OpenAPIProvider
from fastmcp.utilities.openapi import HTTPRoute
from mcp_types import ToolAnnotations

from equinix_docs_mcp_server.openapi_annotations import (
    annotate_openapi_component,
    derive_tool_annotations,
)


def _op(operation_id, summary=None, **extra):
    op = {"operationId": operation_id, "responses": {"204": {"description": "ok"}}}
    if summary:
        op["summary"] = summary
    op.update(extra)
    return op


SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Widgets", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/widgets": {
            "get": _op("listWidgets", "List widgets"),
            "post": _op("createWidget"),
        },
        "/widgets/{id}": {
            "parameters": [
                {
                    "name": "id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "put": _op("replaceWidget"),
            "patch": _op("updateWidget"),
            "delete": _op("deleteWidget"),
        },
        "/widgets/{id}/search": {
            "parameters": [
                {
                    "name": "id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "post": _op(
                "searchWidget",
                **{
                    "x-mcp-annotations": {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                    }
                },
            ),
        },
    },
}


@pytest.fixture
async def tools():
    """List annotated tools from a provider built on the inline spec."""
    mcp = FastMCP("test")
    provider = OpenAPIProvider(
        openapi_spec=SPEC,
        client=httpx2.AsyncClient(base_url="https://api.example.com"),
        mcp_component_fn=annotate_openapi_component,
    )
    mcp.add_provider(provider)
    listed = await mcp.list_tools()
    return {tool.name: tool.annotations for tool in listed}


async def test_get_is_read_only(tools):
    ann = tools["listWidgets"]
    assert ann.read_only_hint is True
    assert ann.destructive_hint is False
    assert ann.idempotent_hint is True
    assert ann.open_world_hint is True
    assert ann.title == "List widgets"


async def test_post_is_not_read_only_or_idempotent(tools):
    ann = tools["createWidget"]
    assert ann.read_only_hint is False
    assert ann.idempotent_hint is False
    # Left unset so the MCP default (destructive) applies.
    assert ann.destructive_hint is None
    assert ann.open_world_hint is True
    assert ann.title is None


async def test_put_is_destructive_and_idempotent(tools):
    ann = tools["replaceWidget"]
    assert ann.read_only_hint is False
    assert ann.destructive_hint is True
    assert ann.idempotent_hint is True


async def test_patch_is_not_assumed_idempotent(tools):
    ann = tools["updateWidget"]
    assert ann.read_only_hint is False
    assert ann.idempotent_hint is False
    assert ann.destructive_hint is None


async def test_delete_is_destructive_and_idempotent(tools):
    ann = tools["deleteWidget"]
    assert ann.read_only_hint is False
    assert ann.destructive_hint is True
    assert ann.idempotent_hint is True
    assert ann.open_world_hint is True


async def test_vendor_extension_overrides_method_defaults(tools):
    ann = tools["searchWidget"]
    assert ann.read_only_hint is True
    assert ann.destructive_hint is False
    assert ann.idempotent_hint is False  # method default kept where not overridden


def test_annotations_serialize_with_camel_case_aliases():
    route = HTTPRoute(path="/widgets", method="GET")
    dumped = derive_tool_annotations(route).model_dump(by_alias=True, exclude_none=True)
    assert dumped == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }


def test_existing_annotations_are_preserved():
    route = HTTPRoute(path="/widgets", method="DELETE", summary="Delete widget")
    existing = ToolAnnotations(title="Custom", destructive_hint=False)
    ann = derive_tool_annotations(route, existing)
    assert ann.title == "Custom"
    assert ann.destructive_hint is False
    assert ann.idempotent_hint is True
    assert ann.open_world_hint is True


def test_invalid_vendor_extension_is_ignored():
    route = HTTPRoute(
        path="/widgets",
        method="POST",
        extensions={"x-mcp-annotations": {"readOnlyHint": "not-a-bool"}},
    )
    ann = derive_tool_annotations(route)
    assert ann.read_only_hint is False
