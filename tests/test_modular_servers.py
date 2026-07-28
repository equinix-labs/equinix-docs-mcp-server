"""Tests for the dedicated docs, discovery, and execution server entry points."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from equinix_docs_mcp_server.main import EquinixMCPServer
from equinix_docs_mcp_server.servers.discovery import EquinixDiscoveryServer
from equinix_docs_mcp_server.servers.docs import EquinixDocsServer
from equinix_docs_mcp_server.servers.execution import EquinixExecutionServer


def _tool_decorator_collector(registry: list[dict[str, str]]):
    """Collect tool registrations without depending on FastMCP internals."""

    def decorator(*, name: str, description: str):
        def register(fn):
            registry.append({"name": name, "description": description, "fn": fn})
            return fn

        return register

    return decorator


@pytest.mark.asyncio
async def test_docs_server_registers_docs_tools():
    """Docs server should expose only documentation tools."""
    registered_tools = []

    with patch(
        "equinix_docs_mcp_server.servers.docs.Config.load", return_value=MagicMock()
    ), patch(
        "equinix_docs_mcp_server.servers.docs.DocsManager", return_value=MagicMock()
    ), patch("equinix_docs_mcp_server.servers.docs.FastMCP") as mock_fastmcp:
        mock_mcp = MagicMock()
        mock_mcp.tool = _tool_decorator_collector(registered_tools)
        mock_fastmcp.return_value = mock_mcp

        server = EquinixDocsServer("test_config.yaml")
        await server.initialize()

    assert [tool["name"] for tool in registered_tools] == [
        "search",
        "fetch",
        "list_docs",
        "find_docs",
    ]


@pytest.mark.asyncio
async def test_discovery_server_registers_discovery_tools():
    """Discovery server should expose only OpenAPI discovery tools."""
    registered_tools = []
    discovery_manager = MagicMock()
    discovery_manager.ensure_index = AsyncMock()

    with patch(
        "equinix_docs_mcp_server.servers.discovery.Config.load",
        return_value=MagicMock(),
    ), patch(
        "equinix_docs_mcp_server.servers.discovery.DiscoveryManager",
        return_value=discovery_manager,
    ), patch("equinix_docs_mcp_server.servers.discovery.FastMCP") as mock_fastmcp:
        mock_mcp = MagicMock()
        mock_mcp.tool = _tool_decorator_collector(registered_tools)
        mock_fastmcp.return_value = mock_mcp

        server = EquinixDiscoveryServer("test_config.yaml")
        await server.initialize()

    discovery_manager.ensure_index.assert_called_once()
    assert [tool["name"] for tool in registered_tools] == ["search_api", "fetch_api"]


def test_execution_server_scopes_delegate_to_execution_only():
    """Execution server should disable docs and discovery tool registration."""
    with patch("equinix_docs_mcp_server.servers.execution.EquinixMCPServer") as mock_cls:
        delegate = MagicMock()
        mock_cls.return_value = delegate

        server = EquinixExecutionServer("test_config.yaml")

    mock_cls.assert_called_once_with(
        "test_config.yaml",
        enable_docs=False,
        enable_discovery=False,
        enable_execution=True,
    )
    assert server.delegate is delegate


@pytest.mark.asyncio
async def test_execution_server_run_delegates_to_main_server():
    """Execution server should forward run options to the shared implementation."""
    with patch("equinix_docs_mcp_server.servers.execution.EquinixMCPServer") as mock_cls:
        delegate = MagicMock()
        delegate.run = AsyncMock()
        mock_cls.return_value = delegate

        server = EquinixExecutionServer("test_config.yaml")
        await server.run(force_update_specs=True)

    delegate.run.assert_called_once_with(True)


def test_mux_server_initializes_all_tool_groups_by_default():
    """Main server should keep the multiplexed server enabled by default."""
    with patch("equinix_docs_mcp_server.main.Config.load") as mock_load:
        mock_load.return_value = MagicMock()

        server = EquinixMCPServer("test_config.yaml")

    assert server.enable_docs is True
    assert server.enable_discovery is True
    assert server.enable_execution is True
