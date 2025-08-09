#!/usr/bin/env python3

import asyncio
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def main():
    """Test the MCP server initialization."""
    server = EquinixMCPServer("config/apis.yaml")

    try:
        print("🔄 Initializing MCP server...")
        await server.initialize()
        print("✅ MCP server initialized successfully!")

        # Don't run the server, just initialize to test spec processing
        print("✅ Test completed!")

    except Exception as e:
        print(f"❌ Error during MCP server initialization: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
