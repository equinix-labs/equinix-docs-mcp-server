"""Tests for the main Equinix MCP Server implementation."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from equinix_mcp_server.config import Config
from equinix_mcp_server.main import AuthenticatedClient, EquinixMCPServer


class TestAuthenticatedClient:
    """Test the AuthenticatedClient wrapper."""

    def test_get_service_from_url(self):
        """Test service detection from URL."""
        auth_manager = MagicMock()
        response_formatter = MagicMock()
        client = AuthenticatedClient(auth_manager, response_formatter)

        assert (
            client._get_service_from_url("https://api.equinix.com/metal/v1/projects")
            == "metal"
        )
        assert (
            client._get_service_from_url(
                "https://api.equinix.com/fabric/v4/connections"
            )
            == "fabric"
        )
        assert (
            client._get_service_from_url(
                "https://api.equinix.com/network-edge/api/v1/devices"
            )
            == "network-edge"
        )
        assert (
            client._get_service_from_url("https://api.equinix.com/billing/v1/invoices")
            == "billing"
        )
        assert (
            client._get_service_from_url("https://api.equinix.com/other/v1/something")
            == "unknown"
        )


class TestEquinixMCPServer:
    """Test the main server class."""

    def test_server_initialization(self):
        """Test server can be initialized."""
        with patch("equinix_mcp_server.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_load.return_value = mock_config

            server = EquinixMCPServer("test_config.yaml")

            assert server.config == mock_config
            assert server.auth_manager is not None
            assert server.spec_manager is not None
            assert server.docs_manager is not None
            assert server.mcp is None  # Not initialized until initialize() is called

    @pytest.mark.asyncio
    async def test_server_initialization_with_fastmcp(self):
        """Test server initialization uses FastMCP.from_openapi."""
        with patch("equinix_mcp_server.main.Config.load") as mock_config_load, patch(
            "equinix_mcp_server.main.SpecManager"
        ) as mock_spec_mgr, patch(
            "equinix_mcp_server.main.AuthManager"
        ) as mock_auth_mgr, patch(
            "equinix_mcp_server.main.DocsManager"
        ) as mock_docs_mgr, patch(
            "equinix_mcp_server.main.FastMCP"
        ) as mock_fastmcp_class:

            # Setup mocks
            mock_config = MagicMock()
            mock_config_load.return_value = mock_config

            mock_spec_instance = MagicMock()
            mock_spec_instance.update_specs = AsyncMock()
            mock_spec_instance.has_all_cached_specs = MagicMock(return_value=False)
            mock_spec_instance.get_merged_spec = MagicMock(
                return_value={
                    "openapi": "3.0.3",
                    "info": {"title": "Test", "version": "1.0.0"},
                    "paths": {},
                }
            )
            mock_spec_mgr.return_value = mock_spec_instance

            mock_auth_instance = MagicMock()
            mock_auth_mgr.return_value = mock_auth_instance

            mock_docs_instance = MagicMock()
            mock_docs_mgr.return_value = mock_docs_instance

            mock_mcp = MagicMock()
            mock_mcp.tool = MagicMock()
            mock_mcp.get_tools = AsyncMock(return_value={})

            # Create a different mock for the main FastMCP instance
            mock_main_mcp = MagicMock()

            # Configure the FastMCP class mock to return different instances
            mock_fastmcp_class.return_value = mock_main_mcp  # For FastMCP()
            mock_fastmcp_class.from_openapi.return_value = (
                mock_mcp  # For FastMCP.from_openapi()
            )

            # Test initialization - create server inside the patch context
            server = EquinixMCPServer("test_config.yaml")
            await server.initialize()

            # Verify FastMCP.from_openapi was called
            mock_fastmcp_class.from_openapi.assert_called_once()
            call_args = mock_fastmcp_class.from_openapi.call_args

            # Check that it was called with the right parameters
            assert "openapi_spec" in call_args[1]
            assert "client" in call_args[1]
            assert "name" in call_args[1]
            assert call_args[1]["name"] == "Temp"

            # Check that the client is an AuthenticatedClient
            client_arg = call_args[1]["client"]
            assert isinstance(client_arg, AuthenticatedClient)

            # Verify server components were updated
            mock_spec_instance.update_specs.assert_called_once()
            mock_spec_instance.get_merged_spec.assert_called_once()

            assert server.mcp == mock_main_mcp
