#!/usr/bin/env python3
"""Test script to verify the fabric_searchConnections tool works."""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def test_fabric_connections():
    """Test the fabric connections search tool."""
    print("🔧 Testing Fabric Connections Search...")

    # Check credentials
    if not os.getenv("EQUINIX_CLIENT_ID") or not os.getenv("EQUINIX_CLIENT_SECRET"):
        print("❌ Missing credentials. Set EQUINIX_CLIENT_ID and EQUINIX_CLIENT_SECRET")
        return

    # Initialize server
    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    # Get available tools
    tools = await server.mcp.get_tools()

    # Find fabric connection search tools
    connection_tools = [
        name
        for name in tools.keys()
        if "fabric" in name.lower() and "connection" in name.lower()
    ]
    if connection_tools:
        print(f"✅ Found {len(connection_tools)} fabric connection tools:")
        for tool_name in connection_tools:
            print(f"   - {tool_name}")

    # Test the connections search tool if available
    search_tool_name = None
    for name in tools.keys():
        if name == "fabric_searchConnections":
            search_tool_name = name
            break

    if search_tool_name:
        print(f"\n🧪 Testing {search_tool_name}...")
        try:
            # Get the tool function
            tool_func = tools[search_tool_name]

            # Call the tool with a simple search
            result = await tool_func.run(
                {
                    "filter": {
                        "and": [
                            {
                                "property": "/direction",
                                "operator": "=",
                                "values": ["OUTGOING"],
                            }
                        ]
                    },
                    "pagination": {"limit": 5, "offset": 0, "total": 0},
                }
            )
            print(f"✅ Tool call successful!")
            print(f"Result: {result}")

        except Exception as e:
            print(f"❌ Tool call failed: {e}")
            import traceback

            traceback.print_exc()
    else:
        print("❌ fabric_searchConnections tool not found")


if __name__ == "__main__":
    asyncio.run(test_fabric_connections())
