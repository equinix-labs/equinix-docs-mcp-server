"""Main entry point for the Equinix MCP Server."""

import asyncio
import logging
import os
from typing import Any, Dict, Optional, Union

import click
import httpx
from fastmcp import FastMCP

from .arazzo_manager import ArazzoManager
from .auth import AuthManager
from .config import Config
from .docs import DocsManager
from .response_formatter import ResponseFormatter
from .spec_manager import SpecManager

# Set up logging
logger = logging.getLogger(__name__)


def _configure_logging(log_level: str):
    """Configure logging with the specified level and suppress third-party library noise"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,
    )

    # Set specific levels for noisy third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


class AuthenticatedClient:
    """HTTP client wrapper that adds authentication headers and response formatting."""

    def __init__(
        self,
        auth_manager: AuthManager,
        response_formatter: ResponseFormatter,
        base_url: str = "https://api.equinix.com",
    ):
        self.auth_manager = auth_manager
        self.response_formatter = response_formatter
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
        elif "/ne/" in url:
            return "network-edge"
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
        self.response_formatter = ResponseFormatter(self.config)
        self.arazzo_manager = ArazzoManager(self.config, auth_manager=self.auth_manager)
        self.mcp: Optional[Any] = None  # Will be initialized in initialize()

    async def initialize(self, force_update_specs: bool = False) -> None:
        """Initialize the server components using FastMCP's OpenAPI integration."""
        # Enable experimental OpenAPI parser
        import os

        os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

        # Load and merge API specs - only update if forced or no cached specs exist
        needs_update = (
            force_update_specs or not self.spec_manager.has_all_cached_specs()
        )

        if needs_update:
            logger.info("Updating API specifications from remote sources...")
            await self.spec_manager.update_specs()
        else:
            logger.info("Using cached API specifications for faster startup")

        merged_spec = self.spec_manager.get_merged_spec()

        # Create authenticated HTTP client for API calls
        client = AuthenticatedClient(
            self.auth_manager,
            self.response_formatter,
            base_url="https://api.equinix.com",
        )

        # Create FastMCP instance with tool_serializer
        self.mcp = FastMCP(
            name="Equinix API Server",
            tool_serializer=self.response_formatter,
            instructions=(
                "This server provides unified access to Equinix's API ecosystem "
                "including Metal, Fabric, Network Edge, and Billing services. "
                "Responses are formatted as YAML for better readability."
            ),
        )

        # Create a temporary FastMCP instance to get the tools, then apply transformations
        temp_mcp = FastMCP.from_openapi(
            openapi_spec=merged_spec,
            client=client,  # type: ignore[arg-type]
            name="Temp",
        )

        # Apply tool transformations for formatting and transfer to main instance
        await self._apply_tool_transformations(temp_mcp)
        # Local workaround: ensure tool output schemas include required $defs
        await self._attach_defs_to_tool_schemas(merged_spec)

        # Set up context-aware serialization by decorating tools
        # await self._setup_context_aware_tools()  # Replaced by tool transformations

        # Register additional documentation tools that aren't part of the API
        await self._register_docs_tools()
        # Load and register Arazzo workflows (after API tools exist)
        await self.arazzo_manager.load()
        await self.arazzo_manager.register_with_fastmcp(self.mcp)

    async def _apply_tool_transformations(self, temp_mcp: Any) -> None:
        """Apply tool transformations for formatting and transfer tools to main instance."""
        from fastmcp.tools import Tool
        from fastmcp.tools.tool_transform import forward

        if not self.mcp:
            return

        temp_tools = await temp_mcp.get_tools()

        for tool_name, tool in temp_tools.items():
            logger.info(f"Discovered tool from FastMCP: '{tool_name}'")
            # Normalize tool name to use double-underscore separator for API__operation
            normalized_tool_name = tool_name
            if "__" not in tool_name and "_" in tool_name:
                # Replace the last single underscore with double underscore to preserve API prefix
                parts = tool_name.rsplit("_", 1)
                normalized_tool_name = f"{parts[0]}__{parts[1]}"
            logger.info(
                f"Normalized tool name: '{normalized_tool_name}' (from '{tool_name}')"
            )

            # Check if this tool has formatting configuration (use normalized name)
            format_config = self.response_formatter._get_format_config(
                normalized_tool_name
            )

            if format_config:
                logger.info(
                    f"Creating transformed tool with formatting for {tool_name}"
                )
                logger.info(
                    f"Format config found for normalized name '{normalized_tool_name}': {bool(format_config)}"
                )

                # Create a transformation that applies JQ formatting
                def create_format_wrapper(operation_id: str):
                    async def format_transform(**kwargs):
                        logger.debug(
                            f"Format transform called for {operation_id} with kwargs: {list(kwargs.keys())}"
                        )

                        # Call the original tool
                        result = await forward(**kwargs)
                        logger.debug(f"Forward result type: {type(result)}")
                        logger.debug(f"Forward result attributes: {dir(result)}")

                        if hasattr(result, "content") and result.content:
                            logger.debug(
                                f"Forward result has content with {len(result.content)} items"
                            )
                            for i, item in enumerate(result.content):
                                logger.debug(
                                    f"Content item {i}: type={type(item)}, attributes={dir(item)}"
                                )

                        if hasattr(result, "structured_content"):
                            logger.debug(
                                f"Forward result structured_content type: {type(result.structured_content)}"
                            )

                        # Extract the actual data from ToolResult
                        actual_data = None

                        if (
                            hasattr(result, "structured_content")
                            and result.structured_content is not None
                        ):
                            # Prefer structured content - this is the parsed JSON data
                            actual_data = result.structured_content
                            logger.debug(
                                f"Using structured content: {type(actual_data)}"
                            )

                        elif hasattr(result, "content") and result.content:
                            # Fallback: try to extract from content if no structured content
                            for i, content_item in enumerate(result.content):
                                try:
                                    # Try to access text attribute safely - only TextContent has this
                                    text_content = getattr(content_item, "text", None)
                                    if text_content:
                                        logger.debug(
                                            f"Found text content in item {i}: {len(text_content) if text_content else 0} chars"
                                        )
                                        import json

                                        actual_data = json.loads(text_content)
                                        logger.debug(
                                            f"Parsed JSON from TextContent for {operation_id}"
                                        )
                                        break
                                except Exception as e:
                                    logger.debug(
                                        f"Failed to parse JSON from content item {i}: {e}"
                                    )
                                    continue

                        if actual_data is None:
                            logger.error(
                                f"❌ Could not extract data from {operation_id} result, returning as-is"
                            )
                            logger.error(
                                f"Result type: {type(result)}, attributes: {[attr for attr in dir(result) if not attr.startswith('_')]}"
                            )
                            return result

                        # Apply JQ formatting transformation
                        try:
                            # Use normalized operation id for formatting lookup
                            formatted_result = self.response_formatter.format_response(
                                operation_id, actual_data
                            )
                            logger.debug(
                                f"JQ formatting returned: {type(formatted_result)} - {repr(formatted_result)[:200] if formatted_result else 'None'}"
                            )

                            # Import the required types
                            from fastmcp.tools.tool import ToolResult
                            from mcp.types import TextContent

                            # Ensure we have a valid string for the TextContent
                            formatted_text = None

                            if (
                                isinstance(formatted_result, str)
                                and formatted_result.strip()
                            ):
                                formatted_text = formatted_result
                                logger.debug(
                                    f"Using JQ formatted result: {len(formatted_text)} characters"
                                )
                            elif formatted_result is None:
                                # JQ filter returned null - use YAML fallback
                                logger.warning(
                                    f"JQ filter returned null for {operation_id}, using YAML fallback"
                                )
                                import yaml

                                formatted_text = yaml.dump(
                                    actual_data,
                                    sort_keys=False,
                                    default_flow_style=False,
                                    allow_unicode=True,
                                )
                                logger.debug(
                                    f"YAML fallback generated: {len(formatted_text)} characters"
                                )
                            else:
                                # Fallback: serialize as YAML if it's not a valid string
                                logger.debug(
                                    f"JQ returned non-string type {type(formatted_result)}, using YAML fallback"
                                )
                                import yaml

                                formatted_text = yaml.dump(
                                    (
                                        formatted_result
                                        if formatted_result is not None
                                        else actual_data
                                    ),
                                    sort_keys=False,
                                    default_flow_style=False,
                                    allow_unicode=True,
                                )
                                logger.debug(
                                    f"YAML fallback for non-string: {len(formatted_text)} characters"
                                )

                            # Validate we have a non-None string before creating ToolResult
                            if formatted_text is None or not isinstance(
                                formatted_text, str
                            ):
                                logger.error(
                                    f"CRITICAL: formatted_text is {type(formatted_text)} with value {repr(formatted_text)}"
                                )
                                # Emergency fallback - create a simple YAML dump
                                import yaml

                                formatted_text = yaml.dump(
                                    actual_data,
                                    sort_keys=False,
                                    default_flow_style=False,
                                    allow_unicode=True,
                                )
                                logger.error(
                                    f"Emergency YAML fallback: {len(formatted_text)} characters"
                                )

                            logger.debug(
                                f"Final formatted_text type: {type(formatted_text)}, length: {len(formatted_text) if formatted_text else 0}"
                            )

                            # Return a proper ToolResult with both text and structured content
                            # The structured content preserves the original data for validation
                            # while the text content provides the formatted output
                            tool_result = ToolResult(
                                content=[TextContent(type="text", text=formatted_text)],
                                structured_content=actual_data,  # Keep original structured data for schema validation
                            )

                            logger.debug(
                                f"Created ToolResult with content: {len(tool_result.content)} items, structured_content type: {type(tool_result.structured_content)}"
                            )
                            return tool_result
                        except Exception as e:
                            logger.error(
                                f"JQ formatting failed for {operation_id}: {e}"
                            )
                            # Return the original result if formatting fails
                            return result

                    return format_transform

                # Create transformed tool with formatting; pass the normalized name
                transform_fn = create_format_wrapper(normalized_tool_name)
                transformed_tool = Tool.from_tool(
                    tool,
                    name=tool_name,  # Keep the same name
                    transform_fn=transform_fn,
                    # No serializer - we return formatted text directly
                )

                self.mcp.add_tool(transformed_tool)
                logger.debug(f"Added transformed tool: {tool_name}")
            else:
                # No formatting config, add tool as-is but with YAML serialization
                plain_tool = Tool.from_tool(
                    tool,
                    name=tool_name,
                    serializer=self.response_formatter,  # YAML serialization only
                )
                self.mcp.add_tool(plain_tool)
                logger.debug(f"Added plain tool with YAML serialization: {tool_name}")

    async def _setup_context_aware_tools(self) -> None:
        """Set up context awareness for tools so ResponseFormatter knows which operation is being called."""
        if not self.mcp:
            return

        try:
            tools = await self.mcp.get_tools()

            # Wrap each tool's handler to set operation context
            for tool_name, tool in tools.items():
                if hasattr(tool, "handler") and callable(tool.handler):
                    # Store the original handler
                    original_handler = tool.handler

                    # Create a wrapper that sets context before calling the original
                    async def context_wrapper(
                        operation_id=tool_name, original=original_handler
                    ):
                        async def wrapper(*args, **kwargs):
                            # Set the operation context in the formatter
                            self.response_formatter.set_operation_context(operation_id)
                            try:
                                # Call the original tool handler
                                result = await original(*args, **kwargs)
                                return result
                            finally:
                                # Clear the context
                                self.response_formatter.set_operation_context(None)

                        return wrapper

                    # Replace the handler with our context-aware wrapper
                    tool.handler = await context_wrapper()

        except Exception as e:
            logger.warning(f"Could not set up context-aware tools: {e}")
            # Continue without context awareness - formatter will still work as YAML serializer

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

        @self.mcp.tool(name="find_docs", description="Find Equinix documentation by filename")
        async def find_docs(query: str) -> str:
            """Find documentation by filename-based search."""
            return await self.docs_manager.find_docs(query)

        @self.mcp.tool(name="search_docs", description="Search Equinix documentation using full-text search")
        async def search_docs(query: str) -> str:
            """Search documentation using lunr search against indexed content."""
            return await self.docs_manager.search_docs(query)

    async def run(self, force_update_specs: bool = False) -> None:
        """Run the MCP server."""
        await self.initialize(force_update_specs)
        assert self.mcp is not None, "MCP server must be initialized first"

        # Use stdio_server for MCP transport to avoid asyncio loop conflicts
        await self.mcp.run_stdio_async(show_banner=True)

    def _collect_defs_references(self, obj: Any) -> set[str]:
        """Collect names referenced via '#/$defs/<Name>' within a schema-like object."""
        refs: set[str] = set()
        if isinstance(obj, dict):
            ref = obj.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                refs.add(ref.split("/")[-1])
            for v in obj.values():
                refs |= self._collect_defs_references(v)
        elif isinstance(obj, list):
            for item in obj:
                refs |= self._collect_defs_references(item)
        return refs

    def _closure_defs(self, start: set[str], defs: Dict[str, Any]) -> Dict[str, Any]:
        """Compute transitive closure of referenced $defs starting from provided names."""
        used = set(start)
        changed = True
        while changed:
            changed = False
            for name in list(used):
                schema = defs.get(name)
                if isinstance(schema, dict):
                    nested = self._collect_defs_references(schema)
                    for n in nested:
                        if n not in used:
                            used.add(n)
                            changed = True
        return {n: defs[n] for n in used if n in defs}

    async def _attach_defs_to_tool_schemas(self, merged_spec: Dict[str, Any]) -> None:
        """Attach necessary $defs to each tool's output_schema to avoid unresolved refs.

        Some consumers validate only against the tool-level schema and won't have
        the full OpenAPI doc context. Adding the minimal $defs subset prevents
        PointerToNowhere errors like '#/$defs/MetalMeta'.
        """
        if not self.mcp:
            return
        try:
            tools = await self.mcp.get_tools()
        except Exception:
            return

        all_defs = merged_spec.get("$defs", {})
        if not isinstance(all_defs, dict) or not all_defs:
            return

        for tool in tools.values():
            output_schema = getattr(tool, "output_schema", None)
            if not isinstance(output_schema, dict):
                continue

            referenced = self._collect_defs_references(output_schema)
            # If wrapped result, also inspect the inner schema
            if output_schema.get("x-fastmcp-wrap-result") and isinstance(
                output_schema.get("properties"), dict
            ):
                inner = output_schema["properties"].get("result")
                if isinstance(inner, dict):
                    referenced |= self._collect_defs_references(inner)

            if not referenced:
                continue

            subset = self._closure_defs(referenced, all_defs)
            existing = output_schema.get("$defs")
            if isinstance(existing, dict) and existing:
                merged = existing.copy()
                merged.update(subset)
                output_schema["$defs"] = merged
            else:
                output_schema["$defs"] = subset


@click.command()
@click.option(
    "--config", "-c", default="config/apis.yaml", help="Configuration file path"
)
@click.option(
    "--update-specs",
    is_flag=True,
    help="Force update API specs from remote sources (otherwise uses cached specs)",
)
@click.option(
    "--log-level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    help="Set the logging level (default: INFO)",
)
def main(config: str, update_specs: bool, log_level: str) -> None:
    """Start the Equinix MCP Server."""

    # Configure logging based on the provided level
    _configure_logging(log_level.upper())

    async def _main() -> None:
        server = EquinixMCPServer(config)

        if update_specs:
            await server.spec_manager.update_specs()
            click.echo("✅ API spec fetching and validation completed successfully")
            return

        await server.run(force_update_specs=False)  # Normal startup uses cached specs

    asyncio.run(_main())


if __name__ == "__main__":
    main()
