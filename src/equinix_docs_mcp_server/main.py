"""Main entry point for the Equinix MCP Server."""

import asyncio
import logging
from typing import Any, AsyncGenerator, List, Literal, Optional

import click
import httpx2
import yaml
from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.openapi import OpenAPIProvider
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import ToolResult
from mcp_types import TextContent

from .arazzo_manager import ArazzoManager
from .auth import AuthManager
from .config import APIConfig, Config
from .docs import DocsManager
from .response_formatter import ResponseFormatter
from .spec_manager import SpecManager

logger = logging.getLogger(__name__)

# Cache hints for the tool catalog (MCP 2026-07-28 caching utility). The
# catalog is identical for every caller and only changes when specs are
# refreshed, so clients may share and reuse it for an hour.
CATALOG_CACHE_TTL_SECONDS = 3600
CATALOG_CACHE_SCOPE: Literal["public", "private"] = "public"

# Documentation tools stay directly visible in tools/list even when the API
# catalog is collapsed behind the search transform. The literal names
# "search" and "fetch" are required by the OpenAI MCP connector contract.
DOCS_TOOL_NAMES = ["search", "fetch", "list_docs", "find_docs"]

API_BASE_URL = "https://api.equinix.com"


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
    logging.getLogger("httpx2").setLevel(logging.WARNING)


class EquinixAuth(httpx2.Auth):
    """httpx auth hook that injects credentials for a single API family.

    Each OpenAPI provider is bound to exactly one API family, so the right
    credential type is known up front — no URL sniffing required.
    """

    def __init__(self, auth_manager: AuthManager, service_name: str):
        self._auth_manager = auth_manager
        self._service_name = service_name

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        try:
            headers = await self._auth_manager.get_auth_header(self._service_name)
        except Exception as e:
            logger.error(f"Auth error for {self._service_name}: {e}")
            headers = {}
        for key, value in headers.items():
            request.headers[key] = value
        yield request


class ResponseFormattingMiddleware(Middleware):
    """Applies configured JQ transforms and YAML rendering to API tool results.

    Replaces the pre-v4 ``tool_serializer`` hook: results from OpenAPI
    provider tools are rendered as YAML (JQ-trimmed first when the API config
    has a ``format:`` entry for the operation). Docs and workflow tools
    return plain text and pass through untouched.
    """

    def __init__(self, formatter: ResponseFormatter, api_names: List[str]):
        self._formatter = formatter
        self._api_prefixes = tuple(f"{name}_" for name in api_names)

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ) -> ToolResult:
        result = await call_next(context)

        tool_name = getattr(context.message, "name", "") or ""
        if not tool_name.startswith(self._api_prefixes):
            return result
        if result.is_error or result.structured_content is None:
            return result

        data: Any = result.structured_content
        formatted = self._formatter.format_response(tool_name, data)
        if formatted is None:
            formatted = data

        try:
            text = yaml.dump(
                formatted,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            )
        except Exception as e:
            logger.error(f"YAML rendering failed for {tool_name}: {e}")
            return result

        # Keep the original structured data so output-schema validation and
        # programmatic consumers are unaffected by the text rendering.
        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=data,
        )


class EquinixMCPServer:
    """Equinix MCP Server: per-family OpenAPI providers behind a searchable catalog."""

    def __init__(
        self,
        config_path: Optional[str] = None,
        tool_catalog: str = "search",
    ):
        """Initialize the server with configuration.

        Args:
            config_path: Path to the apis.yaml configuration file; defaults
                to the configuration bundled with the package.
            tool_catalog: How to expose the tool catalog — "search" (BM25
                search transform, default), "code-mode" (experimental
                sandboxed code execution), or "full" (every tool listed).
        """
        self.config = Config.load(config_path)
        self.auth_manager = AuthManager(self.config)
        self.spec_manager = SpecManager(self.config)
        self.docs_manager = DocsManager(self.config)
        self.response_formatter = ResponseFormatter(self.config)
        self.arazzo_manager = ArazzoManager(self.config, auth_manager=self.auth_manager)
        self.tool_catalog = tool_catalog
        self.mcp: Optional[FastMCP] = None
        self._api_clients: List[httpx2.AsyncClient] = []

    async def initialize(self, force_update_specs: bool = False) -> None:
        """Initialize the server components."""
        # Load API specs - only update if forced or no cached specs exist
        needs_update = (
            force_update_specs or not self.spec_manager.has_all_cached_specs()
        )

        if needs_update:
            logger.info("Updating API specifications from remote sources...")
            await self.spec_manager.update_specs()
        else:
            logger.info("Using cached API specifications for faster startup")

        self.mcp = FastMCP(
            name="Equinix API Server",
            instructions=(
                "This server provides unified access to Equinix's API ecosystem "
                "including Metal, Fabric, Network Edge, and Billing services, "
                "plus full-text search over docs.equinix.com. Use 'search' and "
                "'fetch' for documentation. API operations are grouped per "
                "service and named '<service>_<operationId>'. API responses "
                "are formatted as YAML for better readability."
            ),
            cache_ttl=CATALOG_CACHE_TTL_SECONDS,
            cache_scope=CATALOG_CACHE_SCOPE,
        )

        self._register_api_providers()
        await self._register_docs_tools()

        # Load and register Arazzo workflows (after API tools exist)
        await self.arazzo_manager.load()
        await self.arazzo_manager.register_with_fastmcp(self.mcp)

        self.mcp.add_middleware(
            ResponseFormattingMiddleware(
                self.response_formatter, list(self.config.apis)
            )
        )
        self._apply_catalog_transform()

    def _register_api_providers(self) -> None:
        """Register one OpenAPI provider per enabled API family.

        A family whose spec fails to parse is skipped (and recorded in
        ``failed_providers``) rather than aborting server startup.
        """
        assert self.mcp is not None
        self.failed_providers: dict[str, str] = {}

        for api_name, api_config in self.config.apis.items():
            if not api_config.enabled:
                continue

            spec = self.spec_manager.get_provider_spec(api_name)
            if not spec:
                logger.warning(f"No cached spec for API '{api_name}'; skipping")
                self.failed_providers[api_name] = "no cached spec"
                continue

            try:
                provider = OpenAPIProvider(
                    openapi_spec=spec,
                    client=self._build_api_client(api_config),
                    tags={"equinix", api_name, *api_config.tags},
                )
            except Exception as e:
                logger.error(f"Failed to build provider for '{api_name}': {e}")
                self.failed_providers[api_name] = str(e)
                continue
            # The namespace yields spec-conformant tool names like
            # "metal_findPlans" and guarantees per-server uniqueness across
            # API families.
            self.mcp.add_provider(provider, namespace=api_name)
            logger.info(f"Registered OpenAPI provider for '{api_name}'")

    def _build_api_client(self, api_config: APIConfig) -> httpx2.AsyncClient:
        """Build the HTTP client for one API family.

        The client's base URL is host-only: spec paths already carry each
        API's base path (prepended by SpecManager from the spec's servers
        entry).
        """
        auth: Optional[httpx2.Auth] = None
        if api_config.auth_type and api_config.name:
            auth = EquinixAuth(self.auth_manager, api_config.name)

        client = httpx2.AsyncClient(
            base_url=API_BASE_URL,
            timeout=30.0,
            auth=auth,
            headers={"User-Agent": "Equinix-MCP-Server/1.0.0"},
        )
        self._api_clients.append(client)
        return client

    def _apply_catalog_transform(self) -> None:
        """Collapse the tool catalog behind a discovery mechanism."""
        assert self.mcp is not None

        if self.tool_catalog == "full":
            return

        if self.tool_catalog == "code-mode":
            try:
                from fastmcp.experimental.transforms.code_mode import (
                    CodeMode,
                    GetSchemas,
                    GetTags,
                    Search,
                )
            except ImportError as e:
                raise RuntimeError(
                    "Code mode requires the optional dependency: "
                    "pip install 'fastmcp[code-mode]'"
                ) from e
            self.mcp.add_transform(
                CodeMode(discovery_tools=[GetTags(), Search(), GetSchemas()])
            )
            logger.info("Tool catalog exposed via experimental code mode")
            return

        self.mcp.add_transform(
            BM25SearchTransform(
                max_results=15,
                always_visible=DOCS_TOOL_NAMES,
            )
        )
        logger.info("Tool catalog exposed via BM25 search transform")

    async def _register_docs_tools(self) -> None:
        """Register documentation tools on the FastMCP server."""
        assert self.mcp is not None, "MCP server must be initialized first"

        @self.mcp.tool(
            name="search",
            description="Search Equinix documentation using full-text search. Returns URLs that can be fetched with the 'fetch' tool to retrieve full content.",
            tags={"docs"},
        )
        async def search(query: str, limit: int = 8) -> str:
            """Search documentation using lunr search against indexed content.

            Args:
                query: The search query string
                limit: Maximum number of results to return (default: 8)

            Returns:
                Search results with titles and URLs
            """
            return await self.docs_manager.search_docs(query, limit)

        @self.mcp.tool(
            name="fetch",
            description="Fetch the full markdown content of an Equinix documentation page by URL. Use URLs returned from the 'search' tool.",
            tags={"docs"},
        )
        async def fetch(url: str) -> str:
            """Fetch the markdown content of a documentation page.

            Args:
                url: The URL of the documentation page (e.g., from search results)

            Returns:
                The full markdown content of the documentation page
            """
            return await self.docs_manager.fetch_doc(url)

        @self.mcp.tool(
            name="list_docs",
            description="List and filter Equinix documentation by topic, product, or keywords. Supports flexible word matching (e.g., 'Fabric providers' will find 'Fabric Provider Guide', 'Provider Management', etc.)",
            tags={"docs"},
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

        @self.mcp.tool(
            name="find_docs",
            description="Find Equinix documentation by filename",
            tags={"docs"},
        )
        async def find_docs(query: str) -> str:
            """Find documentation by filename-based search."""
            return await self.docs_manager.find_docs(query)

    async def run(self, force_update_specs: bool = False) -> None:
        """Run the MCP server."""
        await self.initialize(force_update_specs)
        assert self.mcp is not None, "MCP server must be initialized first"

        # Use stdio_server for MCP transport to avoid asyncio loop conflicts
        await self.mcp.run_stdio_async(show_banner=True)


@click.command()
@click.option(
    "--config",
    "-c",
    default=None,
    help="Configuration file path (defaults to the packaged config/apis.yaml)",
)
@click.option(
    "--update-specs",
    is_flag=True,
    help="Force update API specs from remote sources (otherwise uses cached specs)",
)
@click.option(
    "--discover-apis",
    is_flag=True,
    help=(
        "Scan docs.equinix.com/api-catalog for API specs not yet in the "
        "configuration, print proposed apis.yaml entries, and exit"
    ),
)
@click.option(
    "--write",
    "write_discovered",
    is_flag=True,
    help=(
        "With --discover-apis: append the proposed entries to the "
        "configuration file instead of only printing them"
    ),
)
@click.option(
    "--export-openrpc",
    "export_openrpc_dir",
    default=None,
    metavar="DIR",
    help=(
        "Refresh specs, write the full tool catalog as DIR/openrpc.json and "
        "an HTML rendering as DIR/index.html, and exit"
    ),
)
@click.option(
    "--tool-catalog",
    type=click.Choice(["search", "code-mode", "full"], case_sensitive=False),
    default="search",
    help=(
        "How to expose the tool catalog: 'search' lists only docs tools plus "
        "search_tools/call_tool (default), 'code-mode' uses experimental "
        "sandboxed code execution, 'full' lists every generated tool"
    ),
)
@click.option(
    "--log-level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    help="Set the logging level (default: INFO)",
)
def main(
    config: Optional[str],
    update_specs: bool,
    discover_apis: bool,
    write_discovered: bool,
    export_openrpc_dir: Optional[str],
    tool_catalog: str,
    log_level: str,
) -> None:
    """Start the Equinix MCP Server."""

    # Configure logging based on the provided level
    _configure_logging(log_level.upper())

    async def _main() -> None:
        if export_openrpc_dir:
            from .openrpc import export_openrpc

            json_path, html_path, count = await export_openrpc(
                config, export_openrpc_dir
            )
            click.echo(f"Wrote {count} methods to {json_path} and {html_path}")
            return

        server = EquinixMCPServer(config, tool_catalog=tool_catalog.lower())

        if discover_apis:
            from .catalog_discovery import (
                apply_config_entries,
                discover_catalog_apis,
                propose_config_entries,
            )

            entries = await discover_catalog_apis()
            click.echo(propose_config_entries(server.config, entries))
            if write_discovered:
                added = apply_config_entries(server.config, entries)
                if added:
                    click.echo(
                        f"\nAdded {len(added)} new entries to "
                        f"{server.config.config_path}: {', '.join(added)}"
                    )
                else:
                    click.echo("\nNothing to write.")
            return

        if update_specs:
            await server.spec_manager.update_specs()
            click.echo("✅ API spec fetching and validation completed successfully")
            return

        await server.run(force_update_specs=False)  # Normal startup uses cached specs

    asyncio.run(_main())


if __name__ == "__main__":
    main()
