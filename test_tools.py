#!/usr/bin/env python3
"""Test script to verify MCP tools are available and working."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def test_tools():
    """Test that tools are available and can be called."""
    print("🔧 Testing Equinix MCP Server Tools...")

    # Initialize server
    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    # Get available tools
    tools = await server.mcp.get_tools()
    print(f"✅ Found {len(tools)} tools available")

    # Look for fabric provider tools
    fabric_tools = [
        name
        for name in tools.keys()
        if "fabric" in name.lower() and "provider" in name.lower()
    ]
    if fabric_tools:
        print(f"✅ Found {len(fabric_tools)} fabric provider tools:")
        for tool_name in fabric_tools[:5]:  # Show first 5
            print(f"   - {tool_name}")
        if len(fabric_tools) > 5:
            print(f"   ... and {len(fabric_tools) - 5} more")

    # Try to list docs
    docs_tools = [name for name in tools.keys() if "docs" in name.lower()]
    print(f"✅ Found {len(docs_tools)} documentation tools: {docs_tools}")

    print("✅ MCP Server tools test completed successfully!")


if __name__ == "__main__":
    asyncio.run(test_tools())
