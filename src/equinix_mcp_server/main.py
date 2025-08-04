"""Main entry point for the Equinix MCP Server."""

import asyncio
import os
from typing import Any, Dict, Optional, Union

import click
import httpx
from fastmcp import FastMCP

from .auth import AuthManager
from .config import Config
from .docs import DocsManager
from .spec_manager import SpecManager


class AuthenticatedClient:
    """HTTP client wrapper that adds authentication headers."""

    def __init__(self, auth_manager: AuthManager):
        self.auth_manager = auth_manager
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "Equinix-MCP-Server/1.0.0",
                "Content-Type": "application/json",
            },
        )

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Make an authenticated request."""
        # Determine service from URL/operation
        service_name = self._get_service_from_url(str(url))

        # Get auth header
        try:
            auth_header = await self.auth_manager.get_auth_header(service_name)
            # Merge auth header with existing headers
            headers = kwargs.get("headers", {})
            headers.update(auth_header)
            kwargs["headers"] = headers
        except Exception as e:
            print(f"Auth error for {service_name}: {e}")

        return await self._client.request(method, url, **kwargs)

    def _get_service_from_url(self, url: str) -> str:
        """Determine service name from URL."""
        if "/metal/" in url:
            return "metal"
        elif "/fabric/" in url:
            return "fabric"
        elif "/network-edge/" in url:
            return "network-edge"
        elif "/billing/" in url:
            return "billing"
        return "unknown"

    async def __aenter__(self) -> "AuthenticatedClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.__aexit__(*args)

    def __getattr__(self, name: str) -> Any:
        """Delegate other methods to the underlying client."""
        return getattr(self._client, name)


class EquinixMCPServer:
    """Main Equinix MCP Server class leveraging FastMCP's OpenAPI integration."""

    def __init__(self, config_path: str = "config/apis.yaml"):
        """Initialize the server with configuration."""
        self.config = Config.load(config_path)
        self.auth_manager = AuthManager(self.config)
        self.spec_manager = SpecManager(self.config)
        self.docs_manager = DocsManager(self.config)
        self.mcp: Optional[Any] = None  # Will be initialized in initialize()

    async def initialize(self) -> None:
        """Initialize the server components using FastMCP's OpenAPI integration."""
        # Load and merge API specs
        await self.spec_manager.update_specs()
        merged_spec = await self.spec_manager.get_merged_spec()

        # Create authenticated HTTP client for API calls
        client = AuthenticatedClient(self.auth_manager)

        # Use FastMCP's built-in OpenAPI integration instead of manual tool creation
        # Note: client expects AsyncClient interface, our wrapper provides compatibility
        self.mcp = FastMCP.from_openapi(
            openapi_spec=merged_spec,
            client=client,  # type: ignore[arg-type]
            name="Equinix API Server",
            instructions=(
                "This server provides unified access to Equinix's API ecosystem "
                "including Metal, Fabric, Network Edge, and Billing services."
            ),
        )

        # Register additional documentation tools that aren't part of the API
        await self._register_docs_tools()

    async def _register_docs_tools(self) -> None:
        """Register documentation tools on the FastMCP server."""
        assert self.mcp is not None, "MCP server must be initialized first"

        @self.mcp.tool(
            name="list_docs", description="List and filter Equinix documentation"
        )
        async def list_docs(filter_term: Optional[str] = None) -> str:
            """List documentation with optional filtering."""
            return await self.docs_manager.list_docs(filter_term)

        @self.mcp.tool(name="search_docs", description="Search Equinix documentation")
        async def search_docs(query: str) -> str:
            """Search documentation by query."""
            return await self.docs_manager.search_docs(query)

    async def run(self) -> None:
        """Run the MCP server."""
        await self.initialize()
        assert self.mcp is not None, "MCP server must be initialized first"
        await self.mcp.run()


@click.command()
@click.option(
    "--config", "-c", default="config/apis.yaml", help="Configuration file path"
)
@click.option("--test-update-specs", is_flag=True, help="Test API spec fetching and validation without starting server")
def main(config: str, test_update_specs: bool) -> None:
    """Start the Equinix MCP Server."""

    async def _main() -> None:
        server = EquinixMCPServer(config)

        if test_update_specs:
            await server.spec_manager.update_specs()
            click.echo("✅ API spec fetching and validation test completed successfully")
            return

        await server.run()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
