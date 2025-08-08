"""Main entry point for the Equinix MCP Server."""

import asyncio
import logging
import os
from typing import Any, Dict, Optional, Union

import click
import httpx
from fastmcp import FastMCP

from .auth import AuthManager
from .config import Config
from .docs import DocsManager
from .spec_manager import SpecManager

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


class AuthenticatedClient:
    """HTTP client wrapper that adds authentication headers."""

    def __init__(
        self, auth_manager: AuthManager, base_url: str = "https://api.equinix.com"
    ):
        self.auth_manager = auth_manager
        self.base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=30.0,
            headers={
                "User-Agent": "Equinix-MCP-Server/1.0.0",
                "Content-Type": "application/json",
            },
        )

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        """Send a request with authentication (FastMCP compatibility method)."""
        # This is the method FastMCP's experimental parser likely uses
        logger.debug(f"FastMCP calling send() with {request.method} {request.url}")

        # Add authentication headers to the request
        service_name = self._get_service_from_url(str(request.url))
        try:
            auth_header = await self.auth_manager.get_auth_header(service_name)
            logger.debug(
                f"Adding auth headers for service {service_name}: {list(auth_header.keys())}"
            )

            # Add auth headers to the request
            for key, value in auth_header.items():
                request.headers[key] = value
                if key.lower() == "x-auth-token":
                    logger.debug(f"Added {key}: {value[:10]}...")
                else:
                    logger.debug(f"Added {key}: {value}")

        except Exception as e:
            logger.error(f"Auth error in send() for {service_name}: {e}")

        # Send the request using the underlying client
        response = await self._client.send(request, **kwargs)
        logger.debug(f"send() response status: {response.status_code}")
        return response

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Make an authenticated request."""
        # If URL is just a path, let the base_url handle it
        # If URL is absolute, use it as-is
        if url.startswith("http://") or url.startswith("https://"):
            # Absolute URL, use as-is
            request_url = url
        else:
            # Relative URL, let httpx handle with base_url
            request_url = url

        # Determine service from URL/operation
        service_name = self._get_service_from_url(str(request_url))
        logger.debug(f"Making {method} request to {request_url}")
        logger.debug(f"Detected service: {service_name}")

        # Get auth header
        try:
            auth_header = await self.auth_manager.get_auth_header(service_name)
            logger.debug(f"Auth header keys: {list(auth_header.keys())}")

            # Log auth header without exposing sensitive values
            for key, value in auth_header.items():
                if key.lower() == "authorization" and value.startswith("Bearer "):
                    logger.debug(f"Auth header {key}: Bearer {value[7:17]}...")
                elif key.lower() == "x-auth-token":
                    logger.debug(f"Auth header {key}: {value[:10]}...")
                else:
                    logger.debug(f"Auth header {key}: {value}")

            # Merge auth header with existing headers
            headers = kwargs.get("headers", {})
            headers.update(auth_header)
            kwargs["headers"] = headers

            logger.debug(f"Final request headers: {list(headers.keys())}")

        except Exception as e:
            logger.error(f"Auth error for {service_name}: {e}")
            print(f"Auth error for {service_name}: {e}")

        try:
            response = await self._client.request(method, request_url, **kwargs)
            logger.debug(f"Response status: {response.status_code}")

            if response.status_code >= 400:
                logger.error(f"Request failed with status {response.status_code}")
                logger.error(f"Response body: {response.text}")

                # Add specific logging for authentication-related errors
                if response.status_code == 401:
                    logger.error("🔑 Authentication failed! Possible causes:")
                    logger.error("   - Token expired (check token_timeout)")
                    logger.error("   - Invalid credentials")
                    logger.error("   - Incorrect auth type for service")
                    logger.error("   - Service requires different authentication")
                elif response.status_code == 403:
                    logger.error("🚫 Authorization failed! Possible causes:")
                    logger.error("   - Account lacks permissions for this resource")
                    logger.error(
                        "   - Service/endpoint requires additional permissions"
                    )
                    logger.error("   - API key scope limitations")

            return response
        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET request with authentication."""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """POST request with authentication."""
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        """PUT request with authentication."""
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        """PATCH request with authentication."""
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        """DELETE request with authentication."""
        return await self.request("DELETE", url, **kwargs)

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
        # Enable experimental OpenAPI parser
        import os

        os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

        # Load and merge API specs
        await self.spec_manager.update_specs()
        merged_spec = await self.spec_manager.get_merged_spec()

        # Create authenticated HTTP client for API calls
        client = AuthenticatedClient(
            self.auth_manager, base_url="https://api.equinix.com"
        )

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
            name="list_docs",
            description="List and filter Equinix documentation by topic, product, or keywords. Supports flexible word matching (e.g., 'Fabric providers' will find 'Fabric Provider Guide', 'Provider Management', etc.)",
        )
        async def list_docs(filter_term: Optional[str] = None) -> str:
            """List documentation with optional filtering by keywords.

            Supports flexible matching:
            - Multiple words: finds docs containing any of the words
            - Singular/plural variations: 'provider' matches 'providers' and vice versa
            - Partial phrases: 'Fabric providers' finds 'Fabric Provider Guide'
            - No filter: returns all available documentation
            """
            return await self.docs_manager.list_docs(filter_term)

        @self.mcp.tool(name="search_docs", description="Search Equinix documentation")
        async def search_docs(query: str) -> str:
            """Search documentation by query."""
            return await self.docs_manager.search_docs(query)

    async def run(self) -> None:
        """Run the MCP server."""
        await self.initialize()
        assert self.mcp is not None, "MCP server must be initialized first"

        # Use stdio_server for MCP transport to avoid asyncio loop conflicts
        await self.mcp.run_stdio_async(show_banner=True)


@click.command()
@click.option(
    "--config", "-c", default="config/apis.yaml", help="Configuration file path"
)
@click.option(
    "--test-update-specs",
    is_flag=True,
    help="Test API spec fetching and validation without starting server",
)
def main(config: str, test_update_specs: bool) -> None:
    """Start the Equinix MCP Server."""

    async def _main() -> None:
        server = EquinixMCPServer(config)

        if test_update_specs:
            await server.spec_manager.update_specs()
            click.echo(
                "✅ API spec fetching and validation test completed successfully"
            )
            return

        await server.run()

    asyncio.run(_main())


if __name__ == "__main__":
    main()
