import asyncio
import logging
import click
from fastmcp import FastMCP

from ..config import Config
from ..spec_manager import SpecManager
from ..auth import AuthManager
from ..response_formatter import ResponseFormatter
from ..env import load_project_env
from ..main import AuthenticatedClient, EquinixMCPServer

# Set up logging
logger = logging.getLogger(__name__)

def _configure_logging(log_level: str):
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,
    )

class EquinixExecutionServer:
    """Dedicated Equinix API Execution MCP Server."""

    def __init__(self, config_path: str = "config/apis.yaml"):
        # Reuse the logic from the main server class but scoped to execution
        self.delegate = EquinixMCPServer(
            config_path,
            enable_docs=False,
            enable_discovery=False,
            enable_execution=True
        )
        
    async def run(self, force_update_specs: bool = False):
        """Run the server."""
        # The main EquinixMCPServer implementation already does exactly what we want
        # for the execution server: loads specs, creates client, registers tools.
        # We just need to run it.
        await self.delegate.run(force_update_specs)

@click.command()
@click.option("--config", "-c", default="config/apis.yaml", help="Configuration file path")
@click.option("--update-specs", is_flag=True, help="Force update API specs")
@click.option("--log-level", default="INFO", help="Logging level")
def main(config: str, update_specs: bool, log_level: str):
    """Start the Equinix Execution MCP Server."""
    _configure_logging(log_level)
    load_project_env()
    
    server = EquinixExecutionServer(config)
    asyncio.run(server.run(update_specs))

if __name__ == "__main__":
    main()
