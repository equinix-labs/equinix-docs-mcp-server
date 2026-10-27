"""Main entry point for the Equinix MCP Server."""

import asyncio
import logging
from typing import Annotated, Any, AsyncGenerator, List, Literal, Optional
from urllib.parse import urlparse

import click
import httpx2
import jmespath
import yaml
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.openapi import OpenAPIProvider
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import ToolResult
from jmespath.exceptions import JMESPathError
from mcp_types import TextContent
from pydantic import Field

from .api_notices import ApiNoticeMiddleware, record_response
from .arazzo_manager import ArazzoManager
from .auth import AuthManager
from .config import APIConfig, Config
from .docs import DocsManager
from .openapi_annotations import annotate_openapi_component
from .rate_limits import RetryTransport
from .response_formatter import ResponseFormatter
from .spec_manager import SpecManager, equinix_server_url

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

# Runs an API tool and applies a JMESPath query to its response. Listed next to
# search_tools/call_tool so models can trim large responses.
FILTER_TOOL_NAME = "call_api_filtered"

API_BASE_URL = "https://api.equinix.com"


def _api_base_url(spec: dict[str, Any]) -> str:
    """Pick the host an API family's requests should go to.

    Most families live behind api.equinix.com, but some (access, sts) are
    served from their own hosts. Only the scheme and host are used: spec
    paths already carry the base path. Non-Equinix servers such as
    SwaggerHub mocks are skipped.
    """
    url = equinix_server_url(spec)
    if url:
        return f"https://{urlparse(url).netloc}"
    return API_BASE_URL


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
        await self._add_auth_headers(request)
        response = yield request
        if response.status_code == 401 and "Authorization" in request.headers:
            # The token may have been revoked or expired early: fetch a new
            # one and retry once
            logger.info(f"401 from {self._service_name}; retrying with a new token")
            self._auth_manager.clear_token_cache()
            await self._add_auth_headers(request)
            yield request

    async def _add_auth_headers(self, request: httpx2.Request) -> None:
        try:
            headers = await self._auth_manager.get_auth_header(self._service_name)
        except Exception as e:
            logger.error(f"Auth error for {self._service_name}: {e}")
            headers = {}
        for key, value in headers.items():
            request.headers[key] = value


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
        self.api_notices = ApiNoticeMiddleware(list(self.config.apis))

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
                "including Fabric, Network Edge, and Billing services, "
                "plus full-text search over docs.equinix.com. Use 'search' and "
                "'fetch' for documentation. API operations are grouped per "
                "service and named '<service>_<operationId>'. API responses "
                "are formatted as YAML for better readability."
            ),
            cache_ttl=CATALOG_CACHE_TTL_SECONDS,
            cache_scope=CATALOG_CACHE_SCOPE,
        )

        self._register_api_providers()
        self._register_filter_tool()
        await self._register_docs_tools()

        # Load and register Arazzo workflows (after API tools exist)
        await self.arazzo_manager.load()
        await self.arazzo_manager.register_with_fastmcp(self.mcp)

        # Added first so it runs outside the formatter and its notices are
        # kept
        self.mcp.add_middleware(self.api_notices)
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

            index_tool = self.api_notices.component_hook(api_name)

            def component_fn(route, component, index_tool=index_tool):
                annotate_openapi_component(route, component)
                index_tool(route, component)

            try:
                provider = OpenAPIProvider(
                    openapi_spec=spec,
                    client=self._build_api_client(api_config, spec),
                    tags={"equinix", api_name, *api_config.tags},
                    mcp_component_fn=component_fn,
                )
            except Exception as e:
                logger.error(f"Failed to build provider for '{api_name}': {e}")
                self.failed_providers[api_name] = str(e)
                continue
            # The namespace yields spec-conformant tool names like
            # "fabric_searchConnections" and guarantees per-server uniqueness across
            # API families.
            self.mcp.add_provider(provider, namespace=api_name)
            logger.info(f"Registered OpenAPI provider for '{api_name}'")

    def _register_filter_tool(self) -> None:
        """Register the tool that runs an API tool and filters its response."""
        assert self.mcp is not None
        mcp = self.mcp
        api_prefixes = tuple(f"{name}_" for name in self.config.apis)

        @mcp.tool(
            name=FILTER_TOOL_NAME,
            title="Call API Tool with JMESPath Query",
            description=(
                "Call an Equinix API tool and return only what a JMESPath query "
                "selects from its response. Use this instead of call_tool when "
                "a response would be large and only some fields are needed. "
                "The query runs on the API response body, whose shape is the "
                "tool's output schema. Query syntax: "
                "https://jmespath.org/specification.html\n\n"
                "Examples:\n"
                '- name "fabric_getMetros", arguments {"limit": 200}, '
                'query "data[].{code: code, name: name, region: region}"\n'
                '- name "fabric_searchConnections", query '
                '"data[].{uuid: uuid, name: name, state: operation.equinixStatus}"\n'
                "- query \"data[?region=='EMEA'].code\" to filter\n"
                '- query "pagination.total" to count results'
            ),
            output_schema=None,
            tags={"api"},
        )
        async def call_api_filtered(
            name: Annotated[
                str,
                Field(
                    description=(
                        "Name of the API tool to call, as returned by "
                        'search_tools, e.g. "fabric_getMetros"'
                    )
                ),
            ],
            query: Annotated[
                str,
                Field(
                    description=(
                        "JMESPath query applied to the API response body, "
                        'e.g. "data[].{code: code, name: name}"'
                    )
                ),
            ],
            arguments: Annotated[
                Optional[dict[str, Any]],
                Field(description="Arguments for the API tool, as for call_tool"),
            ] = None,
        ) -> ToolResult:
            # JMESPath rather than jq: model-written queries must not reach the
            # environment or files, loop, or run unbounded
            if not name.startswith(api_prefixes):
                raise ToolError(f"'{name}' is not an Equinix API tool")
            try:
                expression = jmespath.compile(query)
            except JMESPathError as e:
                raise ToolError(f"Invalid JMESPath query: {e}") from e

            result = await mcp.call_tool(name, arguments or {})
            if result.is_error or result.structured_content is None:
                return result

            data: Any = result.structured_content
            # Responses that aren't objects are wrapped as {"result": ...}
            tool = await mcp.get_tool(name)
            if tool and (tool.output_schema or {}).get("x-fastmcp-wrap-result"):
                data = data.get("result", data)
            try:
                filtered = expression.search(data)
            except JMESPathError as e:
                raise ToolError(f"JMESPath query failed: {e}") from e
            text = (
                filtered
                if isinstance(filtered, str)
                else yaml.dump(
                    filtered,
                    sort_keys=False,
                    default_flow_style=False,
                    allow_unicode=True,
                )
            )
            return ToolResult(content=[TextContent(type="text", text=text)])

    def _build_api_client(
        self, api_config: APIConfig, spec: dict[str, Any]
    ) -> httpx2.AsyncClient:
        """Build the HTTP client for one API family.

        The client's base URL is host-only: spec paths already carry each
        API's base path (prepended by SpecManager from the spec's servers
        entry). Rate-limited requests are retried briefly, and deprecation
        headers are recorded for ApiNoticeMiddleware.
        """
        auth: Optional[httpx2.Auth] = None
        if api_config.auth_type and api_config.name:
            auth = EquinixAuth(self.auth_manager, api_config.name)

        client = httpx2.AsyncClient(
            base_url=_api_base_url(spec),
            timeout=30.0,
            auth=auth,
            headers={"User-Agent": "Equinix-MCP-Server/1.0.0"},
            transport=RetryTransport(),
            event_hooks={"response": [record_response]},
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
                always_visible=[*DOCS_TOOL_NAMES, FILTER_TOOL_NAME],
            )
        )
        logger.info("Tool catalog exposed via BM25 search transform")

    async def _register_docs_tools(self) -> None:
        """Register documentation tools on the FastMCP server."""
        assert self.mcp is not None, "MCP server must be initialized first"

        @self.mcp.tool(
            name="search",
            title="Search Equinix Docs",
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
            title="Fetch Equinix Doc",
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
            title="List Equinix Docs",
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
            title="Find Equinix Docs by Filename",
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
        "Read the docs.equinix.com RFC 9727 API catalog for specs not yet "
        "in the configuration, print proposed apis.yaml entries, and exit"
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
