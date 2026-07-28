import asyncio
import logging
import click
from fastmcp import FastMCP

from ..config import Config
from ..discovery import DiscoveryManager
from ..env import load_project_env

# Set up logging
logger = logging.getLogger(__name__)

def _configure_logging(log_level: str):
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,
    )

class EquinixDiscoveryServer:
    """Dedicated Equinix OpenAPI Discovery MCP Server."""

    def __init__(self, config_path: str = "config/apis.yaml"):
        self.config = Config.load(config_path)
        self.discovery_manager = DiscoveryManager(self.config)
        self.mcp = FastMCP(
            name="Equinix Discovery Server",
            instructions="Provides tools to discover and inspect Equinix API specifications (tags, operations, paths)."
        )

    async def initialize(self):
        """Initialize the server and register tools."""
        # Ensure index is built on startup
        await self.discovery_manager.ensure_index()

        @self.mcp.tool(
            name="search_api",
            description="Search for API tags, operations, or paths. Returns a list of matching elements.",
        )
        async def search_api(query: str, limit: int = 10) -> str:
            """Search API elements."""
            return await self.discovery_manager.search_api(query, limit)

        @self.mcp.tool(
            name="fetch_api",
            description="Fetch details for a specific API element (tag name, operationId, or path). Returns the full schema/definition.",
        )
        async def fetch_api(target: str) -> str:
            """Fetch API element details."""
            return await self.discovery_manager.fetch_api(target)

    async def run(self):
        """Run the server."""
        await self.initialize()
        await self.mcp.run_stdio_async()

@click.command()
@click.option("--config", "-c", default="config/apis.yaml", help="Configuration file path")
@click.option("--log-level", default="INFO", help="Logging level")
def main(config: str, log_level: str):
    """Start the Equinix Discovery MCP Server."""
    _configure_logging(log_level)
    load_project_env()
    
    server = EquinixDiscoveryServer(config)
    asyncio.run(server.run())

if __name__ == "__main__":
    main()
