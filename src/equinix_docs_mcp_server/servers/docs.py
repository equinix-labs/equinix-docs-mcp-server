import asyncio
import logging
from typing import Optional

import click
from fastmcp import FastMCP

from ..config import Config
from ..docs import DocsManager
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

class EquinixDocsServer:
    """Dedicated Equinix Documentation MCP Server."""

    def __init__(self, config_path: str = "config/apis.yaml"):
        self.config = Config.load(config_path)
        self.docs_manager = DocsManager(self.config)
        self.mcp = FastMCP(
            name="Equinix Docs Server",
            instructions="Provides access to Equinix documentation via search and fetch tools."
        )

    async def initialize(self):
        """Initialize the server and register tools."""
        # Register tools
        @self.mcp.tool(
            name="search",
            description="Search Equinix documentation using full-text search. Returns URLs that can be fetched with the 'fetch' tool.",
        )
        async def search(query: str, limit: int = 8) -> str:
            """Search documentation."""
            return await self.docs_manager.search_docs(query, limit)

        @self.mcp.tool(
            name="fetch",
            description="Fetch the full markdown content of an Equinix documentation page by URL.",
        )
        async def fetch(url: str) -> str:
            """Fetch documentation content."""
            return await self.docs_manager.fetch_doc(url)

        @self.mcp.tool(
            name="list_docs",
            description="List and filter Equinix documentation by topic, product, or keywords.",
        )
        async def list_docs(filter_term: Optional[str] = None) -> str:
            """List documentation."""
            return await self.docs_manager.list_docs(filter_term)

        @self.mcp.tool(
            name="find_docs", 
            description="Find Equinix documentation by filename"
        )
        async def find_docs(query: str) -> str:
            """Find documentation by filename."""
            return await self.docs_manager.find_docs(query)

    async def run(self):
        """Run the server."""
        await self.initialize()
        await self.mcp.run_stdio_async()

@click.command()
@click.option("--config", "-c", default="config/apis.yaml", help="Configuration file path")
@click.option("--log-level", default="INFO", help="Logging level")
def main(config: str, log_level: str):
    """Start the Equinix Docs MCP Server."""
    _configure_logging(log_level)
    load_project_env()
    
    server = EquinixDocsServer(config)
    asyncio.run(server.run())

if __name__ == "__main__":
    main()
