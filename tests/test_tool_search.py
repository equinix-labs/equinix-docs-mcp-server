"""Tests for compact tool search results and the get_tool_schema lookup."""

import pytest
import yaml
from fastmcp import Client, FastMCP
from fastmcp.tools.base import Tool
from mcp_types import ToolAnnotations

from equinix_docs_mcp_server.tool_search import (
    SCHEMA_TOOL_NAME,
    EquinixSearchTransform,
    parameter_summary,
    summarize_tools,
    tool_access,
    tool_summary,
)

FAMILY_BLURB = (
    "Part of the Equinix Network Edge API family (network-edge). Network Edge "
    "is a platform for deploying virtual network services."
)


def make_tool(**overrides) -> Tool:
    def fn() -> None:
        return None

    tool = Tool.from_function(fn, name=overrides.pop("name", "network-edge_create"))
    return tool.model_copy(update=overrides)


def make_server() -> FastMCP:
    mcp = FastMCP("test")

    @mcp.tool(
        name="network-edge_createVirtualDevice",
        title="Create Virtual Device",
        description=f"Creates a virtual device.\n\n{FAMILY_BLURB}",
        annotations=ToolAnnotations(read_only_hint=False),
    )
    def create_virtual_device(
        metroCode: str, virtualDeviceName: str, projectId: str | None = None
    ) -> dict:
        return {"uuid": "877a3aa2"}

    @mcp.tool(name="fabric_getPorts", title="Get All Ports")
    def get_ports() -> dict:
        return {"data": []}

    @mcp.tool(name="search", tags={"docs"})
    def search(query: str) -> str:
        return query

    mcp.add_transform(EquinixSearchTransform(always_visible=["search"]))
    return mcp


class TestSummaries:
    def test_summary_drops_family_paragraph(self):
        tool = make_tool(description=f"Creates a virtual device.\n\n{FAMILY_BLURB}")
        assert tool_summary(tool) == "Creates a virtual device."

    def test_summary_of_deprecated_tool(self):
        tool = make_tool(
            description=(
                "Deprecated: this operation may be removed. Prefer a "
                "non-deprecated alternative where one exists.\n\n"
                f"Gets port traffic metrics.\n\n{FAMILY_BLURB}"
            ),
            tags={"deprecated"},
        )
        assert tool_summary(tool) == "Deprecated. Gets port traffic metrics."

    def test_summary_strips_html_and_shortens(self):
        long = 'Beta <font color="red">feature</font>. ' + "word " * 100
        summary = tool_summary(make_tool(description=long))
        assert "<font" not in summary
        assert len(summary) <= 301
        assert summary.startswith("Beta feature.")

    def test_access_levels(self):
        def access(**hints):
            return tool_access(make_tool(annotations=ToolAnnotations(**hints)))

        assert access(read_only_hint=True) == "read-only"
        assert access(read_only_hint=False, destructive_hint=True) == "destructive"
        assert access(read_only_hint=False) == "write"
        assert tool_access(make_tool(annotations=None)) == "unknown"

    def test_parameters_required_typed_optional_named(self):
        tool = make_tool(
            parameters={
                "type": "object",
                "properties": {
                    "mode": {"$ref": "#/$defs/Mode"},
                    "emails": {"type": "array", "items": {"type": "string"}},
                    **{f"opt{i}": {"type": "string"} for i in range(14)},
                },
                "required": ["mode", "emails"],
                "$defs": {"Mode": {"type": "string", "enum": ["SUB", "BYOL"]}},
            }
        )
        required, optional = parameter_summary(tool)
        assert required == (
            "Required: `mode` (string, one of SUB/BYOL), `emails` (string[])"
        )
        assert optional.startswith("Optional: `opt0`, `opt1`")
        assert optional.endswith("and 2 more")

    def test_no_parameters(self):
        tool = make_tool(parameters={"type": "object", "properties": {}})
        assert parameter_summary(tool) == ["Parameters: none"]

    def test_no_matches_suggests_rewording(self):
        assert summarize_tools([]).startswith("No tools matched")


class TestSearchTransform:
    @pytest.mark.asyncio
    async def test_lists_schema_tool(self):
        async with Client(make_server()) as client:
            names = [t.name for t in await client.list_tools()]
        assert names == ["search", "search_tools", SCHEMA_TOOL_NAME, "call_tool"]

    @pytest.mark.asyncio
    async def test_search_returns_compact_text(self):
        async with Client(make_server()) as client:
            result = await client.call_tool(
                "search_tools", {"query": "create virtual device"}
            )
        text = result.content[0].text
        assert result.structured_content is None
        assert text.startswith(
            "### network-edge_createVirtualDevice — Create Virtual Device\n"
            "Access: write\n"
            "Creates a virtual device.\n"
            "Required: `metroCode` (string), `virtualDeviceName` (string)\n"
            "Optional: `projectId`"
        )
        assert "Part of the Equinix" not in text
        assert "outputSchema" not in text
        assert text.endswith("then run it with call_tool.")

    @pytest.mark.asyncio
    async def test_schema_returns_full_definition(self):
        async with Client(make_server()) as client:
            result = await client.call_tool(
                SCHEMA_TOOL_NAME, {"name": "network-edge_createVirtualDevice"}
            )
        definition = yaml.safe_load(result.content[0].text)
        assert definition["name"] == "network-edge_createVirtualDevice"
        assert FAMILY_BLURB in definition["description"]
        assert definition["inputSchema"]["required"] == [
            "metroCode",
            "virtualDeviceName",
        ]
        assert "outputSchema" not in definition

    @pytest.mark.asyncio
    async def test_schema_includes_output_schema_on_request(self):
        async with Client(make_server()) as client:
            result = await client.call_tool(
                SCHEMA_TOOL_NAME,
                {"name": "network-edge_createVirtualDevice", "output_schema": True},
            )
        definition = yaml.safe_load(result.content[0].text)
        assert definition["outputSchema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_schema_unknown_tool(self):
        async with Client(make_server()) as client:
            result = await client.call_tool(
                SCHEMA_TOOL_NAME, {"name": "nope"}, raise_on_error=False
            )
        assert result.is_error
        assert "Use search_tools" in result.content[0].text

    @pytest.mark.asyncio
    async def test_schema_skips_pinned_and_synthetic_tools(self):
        """Only the tools search can return are described."""
        async with Client(make_server()) as client:
            for name in ("search", "search_tools", "call_tool"):
                result = await client.call_tool(
                    SCHEMA_TOOL_NAME, {"name": name}, raise_on_error=False
                )
                assert result.is_error, name

    @pytest.mark.asyncio
    async def test_schema_tool_is_read_only(self):
        async with Client(make_server()) as client:
            tools = {t.name: t for t in await client.list_tools()}
        annotations = tools[SCHEMA_TOOL_NAME].annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
