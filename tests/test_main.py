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

TINY_METAL_SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Metal", "version": "1.0.0"},
    "paths": {
        "/metal/v1/plans": {
            "get": {
                "operationId": "findPlans",
                "summary": "List all server plans available in Equinix Metal",
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


def make_config() -> Config:
    """Build a minimal real Config without touching the filesystem."""
    return Config(
        apis={
            "metal": APIConfig(
                name="metal",
                auth_type="metal_token",
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
    server.spec_manager.get_provider_spec = MagicMock(return_value=TINY_METAL_SPEC)
    return server


class TestEquinixAuth:
    """Test the per-family httpx auth hook."""

    @pytest.mark.asyncio
    async def test_injects_auth_headers(self):
        auth_manager = MagicMock()
        auth_manager.get_auth_header = AsyncMock(
            return_value={"X-Auth-Token": "token123"}
        )
        auth = EquinixAuth(auth_manager, "metal")

        request = httpx2.Request("GET", "https://api.equinix.com/metal/v1/plans")
        flow = auth.async_auth_flow(request)
        sent = await anext(flow)

        assert sent.headers["X-Auth-Token"] == "token123"
        auth_manager.get_auth_header.assert_awaited_once_with("metal")

    @pytest.mark.asyncio
    async def test_auth_failure_sends_request_unauthenticated(self):
        auth_manager = MagicMock()
        auth_manager.get_auth_header = AsyncMock(side_effect=ValueError("no creds"))
        auth = EquinixAuth(auth_manager, "metal")

        request = httpx2.Request("GET", "https://api.equinix.com/metal/v1/plans")
        sent = await anext(auth.async_auth_flow(request))

        assert "X-Auth-Token" not in sent.headers


class TestResponseFormattingMiddleware:
    """Test JQ/YAML formatting of API tool results."""

    def _context(self, tool_name: str) -> MagicMock:
        context = MagicMock()
        context.message.name = tool_name
        return context

    @pytest.mark.asyncio
    async def test_api_tool_result_rendered_as_yaml(self):
        formatter = MagicMock()
        formatter.format_response = MagicMock(return_value={"plans": ["c3.small.x86"]})
        middleware = ResponseFormattingMiddleware(formatter, ["metal"])

        original = ToolResult(structured_content={"plans": ["c3.small.x86"]})
        call_next = AsyncMock(return_value=original)

        result = await middleware.on_call_tool(
            self._context("metal_findPlans"), call_next
        )

        formatter.format_response.assert_called_once()
        assert "c3.small.x86" in result.content[0].text
        assert result.structured_content == {"plans": ["c3.small.x86"]}

    @pytest.mark.asyncio
    async def test_non_api_tool_passes_through(self):
        formatter = MagicMock()
        middleware = ResponseFormattingMiddleware(formatter, ["metal"])

        original = ToolResult(content="plain docs text")
        call_next = AsyncMock(return_value=original)

        result = await middleware.on_call_tool(self._context("search"), call_next)

        assert result is original
        formatter.format_response.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_result_passes_through(self):
        formatter = MagicMock()
        middleware = ResponseFormattingMiddleware(formatter, ["metal"])

        original = ToolResult(content="boom", is_error=True)
        call_next = AsyncMock(return_value=original)

        result = await middleware.on_call_tool(
            self._context("metal_findPlans"), call_next
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
            result = await client.call_tool("search_tools", {"query": "server plans"})

        assert "metal_findPlans" in str(result)

    @pytest.mark.asyncio
    async def test_full_catalog_lists_namespaced_api_tools(self):
        """With --tool-catalog full, provider tools appear with family prefixes."""
        server = make_server(tool_catalog="full")
        await server.initialize()

        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}

        assert "metal_findPlans" in names
        assert set(DOCS_TOOL_NAMES) <= names

    @pytest.mark.asyncio
    async def test_catalog_carries_public_cache_hints(self):
        """tools/list responses advertise ttlMs/cacheScope per MCP 2026-07-28."""
        server = make_server(tool_catalog="search")
        await server.initialize()

        hint = server.mcp._mcp_server.cache_hints["tools/list"]
        assert hint.ttl_ms == 3600000
        assert hint.scope == "public"


class TestTransport:
    """Test stdio/HTTP transport selection."""

    @pytest.mark.asyncio
    async def test_run_defaults_to_stdio(self):
        server = make_server()
        with (
            patch("fastmcp.FastMCP.run_stdio_async", new=AsyncMock()) as stdio,
            patch("fastmcp.FastMCP.run_http_async", new=AsyncMock()) as http,
        ):
            await server.run()

        stdio.assert_awaited_once()
        http.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_http_binds_host_and_port(self):
        server = make_server()
        with (
            patch("fastmcp.FastMCP.run_stdio_async", new=AsyncMock()) as stdio,
            patch("fastmcp.FastMCP.run_http_async", new=AsyncMock()) as http,
        ):
            await server.run(transport="http", host="0.0.0.0", port=9001)

        stdio.assert_not_awaited()
        http.assert_awaited_once()
        kwargs = http.await_args.kwargs
        assert (kwargs["transport"], kwargs["host"], kwargs["port"]) == (
            "http",
            "0.0.0.0",
            9001,
        )

    @pytest.mark.parametrize(
        "args, env, expected",
        [
            ([], {}, ("stdio", "127.0.0.1", 8000)),
            (
                ["--transport", "http", "--host", "0.0.0.0", "--port", "9000"],
                {},
                ("http", "0.0.0.0", 9000),
            ),
            (
                [],
                {"EQUINIX_MCP_TRANSPORT": "http", "PORT": "8081"},
                ("http", "127.0.0.1", 8081),
            ),
        ],
    )
    def test_cli_transport_options(self, args, env, expected, monkeypatch):
        from click.testing import CliRunner

        from equinix_docs_mcp_server.main import main

        for name in ("EQUINIX_MCP_TRANSPORT", "EQUINIX_MCP_HOST", "EQUINIX_MCP_PORT"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("PORT", raising=False)

        with patch("equinix_docs_mcp_server.main.EquinixMCPServer") as server_cls:
            server_cls.return_value.run = AsyncMock()
            result = CliRunner().invoke(main, args, env=env)

        assert result.exit_code == 0, result.output
        kwargs = server_cls.return_value.run.await_args.kwargs
        assert (kwargs["transport"], kwargs["host"], kwargs["port"]) == expected
