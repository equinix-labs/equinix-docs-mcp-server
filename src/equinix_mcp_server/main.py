"""Main entry point for the Equinix MCP Server."""
import asyncio
import os
from typing import Any, Dict, Optional

import click
from fastmcp import FastMCP

from .auth import AuthManager
from .config import Config
from .spec_manager import SpecManager
from .docs import DocsManager


class EquinixMCPServer:
    """Main Equinix MCP Server class."""
    
    def __init__(self, config_path: str = "config/apis.yaml"):
        """Initialize the server with configuration."""
        self.config = Config.load(config_path)
        self.auth_manager = AuthManager(self.config)
        self.spec_manager = SpecManager(self.config)
        self.docs_manager = DocsManager(self.config)
        self.mcp = FastMCP("Equinix API Server")
        
    async def initialize(self) -> None:
        """Initialize the server components."""
        # Load and merge API specs
        await self.spec_manager.update_specs()
        merged_spec = await self.spec_manager.get_merged_spec()
        
        # Register tools from the merged spec
        await self._register_api_tools(merged_spec)
        
        # Register documentation tools
        await self._register_docs_tools()
        
    async def _register_api_tools(self, spec: Dict[str, Any]) -> None:
        """Register API tools from the merged OpenAPI spec."""
        # This will dynamically create MCP tools from OpenAPI operations
        paths = spec.get("paths", {})
        
        for path, methods in paths.items():
            for method, operation in methods.items():
                if method.upper() in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
                    await self._register_operation_tool(path, method, operation)
    
    async def _register_operation_tool(self, path: str, method: str, operation: Dict[str, Any]) -> None:
        """Register a single API operation as an MCP tool."""
        operation_id = operation.get("operationId")
        if not operation_id:
            return
            
        summary = operation.get("summary", "")
        description = operation.get("description", summary)
        
        # Create a closure to capture the current values
        async def create_api_tool(op_id: str, op_path: str, op_method: str, op_operation: Dict[str, Any]):
            @self.mcp.tool(name=op_id, description=description)
            async def api_tool(**kwargs) -> str:
                """Dynamically created API tool."""
                return await self._execute_api_call(op_path, op_method, op_operation, kwargs)
            return api_tool
        
        await create_api_tool(operation_id, path, method, operation)
    
    async def _execute_api_call(self, path: str, method: str, operation: Dict[str, Any], params: Dict[str, Any]) -> str:
        """Execute an actual API call."""
        # Determine which API this operation belongs to
        service_name = self._get_service_from_path(path)
        
        # Get appropriate authentication
        auth_header = await self.auth_manager.get_auth_header(service_name)
        
        # Build the request URL
        base_url = self._get_base_url(service_name)
        full_url = f"{base_url}{path}"
        
        # Execute the request (placeholder for now)
        return f"Would execute {method.upper()} {full_url} with auth: {auth_header}"
    
    def _get_service_from_path(self, path: str) -> str:
        """Determine service name from API path."""
        # This is a simplified version - will need more sophisticated logic
        if path.startswith("/metal"):
            return "metal"
        elif "fabric" in path:
            return "fabric"
        elif "network-edge" in path:
            return "network-edge"
        elif "billing" in path:
            return "billing"
        return "unknown"
    
    def _get_base_url(self, service_name: str) -> str:
        """Get base URL for a service."""
        base_urls = {
            "metal": "https://api.equinix.com",
            "fabric": "https://api.equinix.com",
            "network-edge": "https://api.equinix.com",
            "billing": "https://api.equinix.com",
        }
        return base_urls.get(service_name, "https://api.equinix.com")
    
    async def _register_docs_tools(self) -> None:
        """Register documentation tools."""
        @self.mcp.tool(name="list_docs", description="List and filter Equinix documentation")
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
        await self.mcp.run()


@click.command()
@click.option("--config", "-c", default="config/apis.yaml", help="Configuration file path")
@click.option("--update-specs", is_flag=True, help="Update API specs before starting")
def main(config: str, update_specs: bool) -> None:
    """Start the Equinix MCP Server."""
    async def _main():
        server = EquinixMCPServer(config)
        
        if update_specs:
            await server.spec_manager.update_specs()
            click.echo("API specs updated successfully")
            return
            
        await server.run()
    
    asyncio.run(_main())


if __name__ == "__main__":
    main()