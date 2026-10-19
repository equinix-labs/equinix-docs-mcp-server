"""Tests for the main Equinix MCP Server implementation."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from fastmcp import Client
from fastmcp.tools.base import ToolResult

from equinix_docs_mcp_server.config import APIConfig, AuthConfig, Config, DocsConfig
from equinix_docs_mcp_server.main import (
    DOCS_TOOL_NAMES,
    EquinixAuth,
    EquinixMCPServer,
    ResponseFormattingMiddleware,
)

TINY_FABRIC_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Fabric", "version": "1.0.0"},
    "paths": {
        "/fabric/v4/ports": {
            "get": {
                "operationId": "getPorts",
                "summary": "List all ports available in Equinix Fabric",
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


def make_config() -> Config:
    """Build a minimal real Config without touching the filesystem."""
    return Config(
        apis={
            "fabric": APIConfig(
                name="fabric",
                auth_type="client_credentials",
                enabled=True,
            )
        },
        auth=AuthConfig(),
        docs=DocsConfig(),
    )


def make_server(tool_catalog: str = "search") -> EquinixMCPServer:
    """Build a server against the minimal config with spec IO stubbed out."""
    with patch("equinix_docs_mcp_server.main.Config.load", return_value=make_config()):
        server = EquinixMCPServer("test_config.yaml", tool_catalog=tool_catalog)
    server.spec_manager.has_all_cached_specs = MagicMock(return_value=True)
    server.spec_manager.get_provider_spec = MagicMock(return_value=TINY_FABRIC_SPEC)
    return server


class TestEquinixAuth:
    """Test the per-family httpx auth hook."""

    @pytest.mark.asyncio
    async def test_injects_auth_headers(self):
        auth_manager = MagicMock()
        auth_manager.get_auth_header = AsyncMock(
            return_value={"Authorization": "Bearer token123"}
        )
        auth = EquinixAuth(auth_manager, "fabric")

        request = httpx2.Request("GET", "https://api.equinix.com/fabric/v4/ports")
        flow = auth.async_auth_flow(request)
        sent = await anext(flow)

        assert sent.headers["Authorization"] == "Bearer token123"
        auth_manager.get_auth_header.assert_awaited_once_with("fabric")

    @pytest.mark.asyncio
    async def test_auth_failure_sends_request_unauthenticated(self):
        auth_manager = MagicMock()
        auth_manager.get_auth_header = AsyncMock(side_effect=ValueError("no creds"))
        auth = EquinixAuth(auth_manager, "fabric")

        request = httpx2.Request("GET", "https://api.equinix.com/fabric/v4/ports")
        sent = await anext(auth.async_auth_flow(request))

        assert "Authorization" not in sent.headers


class TestResponseFormattingMiddleware:
    """Test JQ/YAML formatting of API tool results."""

    def _context(self, tool_name: str) -> MagicMock:
        context = MagicMock()
        context.message.name = tool_name
        return context

    @pytest.mark.asyncio
    async def test_api_tool_result_rendered_as_yaml(self):
        formatter = MagicMock()
        formatter.format_response = MagicMock(return_value={"ports": ["DC-1"]})
        middleware = ResponseFormattingMiddleware(formatter, ["fabric"])

        original = ToolResult(structured_content={"ports": ["DC-1"]})
        call_next = AsyncMock(return_value=original)

        result = await middleware.on_call_tool(
            self._context("fabric_getPorts"), call_next
        )

        formatter.format_response.assert_called_once()
        assert "DC-1" in result.content[0].text
        assert result.structured_content == {"ports": ["DC-1"]}

    @pytest.mark.asyncio
    async def test_non_api_tool_passes_through(self):
        formatter = MagicMock()
        middleware = ResponseFormattingMiddleware(formatter, ["fabric"])

        original = ToolResult(content="plain docs text")
        call_next = AsyncMock(return_value=original)

        result = await middleware.on_call_tool(self._context("search"), call_next)

        assert result is original
        formatter.format_response.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_result_passes_through(self):
        formatter = MagicMock()
        middleware = ResponseFormattingMiddleware(formatter, ["fabric"])

        original = ToolResult(content="boom", is_error=True)
        call_next = AsyncMock(return_value=original)

        result = await middleware.on_call_tool(
            self._context("fabric_getPorts"), call_next
        )

        assert result is original
        formatter.format_response.assert_not_called()


class TestEquinixMCPServer:
    """Test the main server class."""

    def test_server_initialization(self):
        """Test server can be initialized."""
        with patch("equinix_docs_mcp_server.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_load.return_value = mock_config

            server = EquinixMCPServer("test_config.yaml")

            assert server.config == mock_config
            assert server.auth_manager is not None
            assert server.spec_manager is not None
            assert server.docs_manager is not None
            assert server.mcp is None  # Not initialized until initialize() is called

    @pytest.mark.asyncio
    async def test_search_catalog_hides_api_tools(self):
        """Default catalog: only docs tools plus search_tools/call_tool are listed."""
        server = make_server(tool_catalog="search")
        await server.initialize()

        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}

        assert names == {"search_tools", "call_tool", *DOCS_TOOL_NAMES}

    @pytest.mark.asyncio
    async def test_search_tools_finds_api_operations(self):
        """Hidden API tools are discoverable through search_tools."""
        server = make_server(tool_catalog="search")
        await server.initialize()

        async with Client(server.mcp) as client:
            result = await client.call_tool("search_tools", {"query": "fabric ports"})

        assert "fabric_getPorts" in str(result)

    @pytest.mark.asyncio
    async def test_full_catalog_lists_namespaced_api_tools(self):
        """With --tool-catalog full, provider tools appear with family prefixes."""
        server = make_server(tool_catalog="full")
        await server.initialize()

        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}

        assert "fabric_getPorts" in names
        assert set(DOCS_TOOL_NAMES) <= names

    @pytest.mark.asyncio
    async def test_catalog_carries_public_cache_hints(self):
        """tools/list responses advertise ttlMs/cacheScope per MCP 2026-07-28."""
        server = make_server(tool_catalog="search")
        await server.initialize()

        hint = server.mcp._mcp_server.cache_hints["tools/list"]
        assert hint.ttl_ms == 3600000
        assert hint.scope == "public"
