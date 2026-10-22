"""Tests for the main Equinix MCP Server implementation."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
import yaml
from fastmcp import Client
from fastmcp.tools.base import ToolResult

from equinix_docs_mcp_server.config import APIConfig, AuthConfig, Config, DocsConfig
from equinix_docs_mcp_server.main import (
    API_BASE_URL,
    DOCS_TOOL_NAMES,
    FILTER_TOOL_NAME,
    EquinixAuth,
    EquinixMCPServer,
    ResponseFormattingMiddleware,
    _api_base_url,
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


class TestApiBaseUrl:
    """Test per-family host selection from a spec's servers list."""

    def test_uses_spec_host(self):
        """Families on their own hosts (access, sts) are not sent to api."""
        spec = {"servers": [{"url": "https://access.eqix.equinix.com"}]}
        assert _api_base_url(spec) == "https://access.eqix.equinix.com"

    def test_drops_base_path(self):
        """Spec paths already carry the base path, so only the host is kept."""
        spec = {"servers": [{"url": "https://api.equinix.com/fabric/v4"}]}
        assert _api_base_url(spec) == "https://api.equinix.com"

    def test_skips_non_equinix_servers(self):
        """SwaggerHub mock servers listed first are ignored."""
        spec = {
            "servers": [
                {"url": "https://virtserver.swaggerhub.com/equinix-api/x/1.0.0"},
                {"url": "https://api.equinix.com"},
            ]
        }
        assert _api_base_url(spec) == "https://api.equinix.com"

    def test_first_equinix_server_wins(self):
        """Production is listed before sandbox and is preferred."""
        spec = {
            "servers": [
                {"url": "https://api.equinix.com"},
                {"url": "https://sandboxapi.equinix.com"},
            ]
        }
        assert _api_base_url(spec) == "https://api.equinix.com"

    @pytest.mark.parametrize(
        "spec",
        [
            {},
            {"servers": []},
            {"servers": [{"url": "/relative"}]},
            {"servers": [{"url": "http://api.equinix.com"}]},
            {"servers": [{"url": "https://equinix.com.example.org"}]},
        ],
    )
    def test_falls_back_to_api_host(self, spec):
        """Missing, relative, plain-http or look-alike hosts use api.equinix.com."""
        assert _api_base_url(spec) == API_BASE_URL


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

    @pytest.mark.asyncio
    async def test_401_retries_once_with_new_token(self):
        """A rejected token is dropped and the request resent with a new one."""
        auth_manager = MagicMock()
        auth_manager.get_auth_header = AsyncMock(
            side_effect=[
                {"Authorization": "Bearer stale"},
                {"Authorization": "Bearer fresh"},
            ]
        )
        auth = EquinixAuth(auth_manager, "fabric")

        request = httpx2.Request("GET", "https://api.equinix.com/fabric/v4/ports")
        flow = auth.async_auth_flow(request)
        sent = await anext(flow)
        assert sent.headers["Authorization"] == "Bearer stale"

        retried = await flow.asend(httpx2.Response(401, request=sent))
        assert retried.headers["Authorization"] == "Bearer fresh"
        auth_manager.clear_token_cache.assert_called_once()

        # A second 401 is returned to the caller, not retried again
        with pytest.raises(StopAsyncIteration):
            await flow.asend(httpx2.Response(401, request=retried))

    @pytest.mark.parametrize("status", [200, 403])
    @pytest.mark.asyncio
    async def test_other_statuses_are_not_retried(self, status):
        auth_manager = MagicMock()
        auth_manager.get_auth_header = AsyncMock(
            return_value={"Authorization": "Bearer token123"}
        )
        auth = EquinixAuth(auth_manager, "fabric")

        request = httpx2.Request("GET", "https://api.equinix.com/fabric/v4/ports")
        flow = auth.async_auth_flow(request)
        sent = await anext(flow)
        with pytest.raises(StopAsyncIteration):
            await flow.asend(httpx2.Response(status, request=sent))
        auth_manager.clear_token_cache.assert_not_called()


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

        assert names == {
            "search_tools",
            "call_tool",
            FILTER_TOOL_NAME,
            *DOCS_TOOL_NAMES,
        }

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

    @pytest.mark.asyncio
    async def test_provider_client_uses_spec_host(self):
        """Each family's HTTP client targets the host from its own spec."""
        server = make_server(tool_catalog="full")
        server.spec_manager.get_provider_spec = MagicMock(
            return_value={
                **TINY_FABRIC_SPEC,
                "servers": [{"url": "https://access.eqix.equinix.com"}],
            }
        )
        await server.initialize()

        assert [str(c.base_url) for c in server._api_clients] == [
            "https://access.eqix.equinix.com"
        ]

    @pytest.mark.asyncio
    async def test_docs_tools_have_explicit_titles(self):
        """Docs tools set titles instead of fastmcp's name-derived default."""
        server = make_server(tool_catalog="search")
        await server.initialize()

        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            titles = {t.name: t.title for t in tools if t.name in DOCS_TOOL_NAMES}

        assert titles == {
            "search": "Search Equinix Docs",
            "fetch": "Fetch Equinix Doc",
            "list_docs": "List Equinix Docs",
            "find_docs": "Find Equinix Docs by Filename",
        }


class TestCallApiFiltered:
    """Test the tool that applies a jq filter to an API tool's response."""

    METROS = {
        "pagination": {"total": 2},
        "data": [
            {"code": "SV", "name": "Silicon Valley", "connectedMetros": [{}] * 60},
            {"code": "DC", "name": "Ashburn", "connectedMetros": [{}] * 60},
        ],
    }

    async def _server(self) -> EquinixMCPServer:
        server = make_server(tool_catalog="search")
        await server.initialize()
        assert server.mcp is not None

        @server.mcp.tool(name="fabric_getMetros")
        def get_metros() -> dict:
            return self.METROS

        @server.mcp.tool(name="fabric_listCodes")
        def list_codes() -> list[dict]:
            return self.METROS["data"]

        return server

    async def _call(self, server, **arguments):
        async with Client(server.mcp) as client:
            return await client.call_tool(
                FILTER_TOOL_NAME, arguments, raise_on_error=False
            )

    @pytest.mark.asyncio
    async def test_query_selects_fields(self):
        """The query result comes back as YAML, without the full response."""
        server = await self._server()
        result = await self._call(
            server, name="fabric_getMetros", query="data[].{code: code, name: name}"
        )

        assert not result.is_error
        assert yaml.safe_load(result.content[0].text) == [
            {"code": "SV", "name": "Silicon Valley"},
            {"code": "DC", "name": "Ashburn"},
        ]
        assert "connectedMetros" not in result.content[0].text
        assert result.structured_content is None

    @pytest.mark.asyncio
    async def test_scalar_result(self):
        server = await self._server()
        result = await self._call(
            server, name="fabric_getMetros", query="pagination.total"
        )

        assert yaml.safe_load(result.content[0].text) == 2

    @pytest.mark.asyncio
    async def test_wrapped_non_object_response_is_unwrapped(self):
        """Array responses are queried as arrays, not as {"result": [...]}."""
        server = await self._server()
        result = await self._call(server, name="fabric_listCodes", query="[].code")

        assert yaml.safe_load(result.content[0].text) == ["SV", "DC"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name, query, message",
        [
            ("search", "@", "not an Equinix API tool"),
            ("fabric_getMetros", "data[", "Invalid JMESPath query"),
            ("fabric_getMetros", "length(pagination.total)", "JMESPath query failed"),
        ],
    )
    async def test_errors_are_reported(self, name, query, message):
        server = await self._server()
        result = await self._call(server, name=name, query=query)

        assert result.is_error
        assert message in result.content[0].text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query",
        [
            # jq syntax that reads the environment or files is not JMESPath
            "$ENV.EQUINIX_CLIENT_SECRET",
            "env.EQUINIX_CLIENT_SECRET",
            'import "data" as $d; $d',
        ],
    )
    async def test_environment_and_files_are_unreachable(self, query, monkeypatch):
        monkeypatch.setenv("EQUINIX_CLIENT_SECRET", "s3cr3t")
        server = await self._server()
        result = await self._call(server, name="fabric_getMetros", query=query)

        assert "s3cr3t" not in result.content[0].text
