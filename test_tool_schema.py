#!/usr/bin/env python3
"""Test the fabric_searchConnections tool schema."""

import asyncio
import os

from src.equinix_mcp_server.main import EquinixMCPServer


async def test_tool_schema():
    """Test that the fabric_searchConnections tool has proper schema."""

    # Set dummy environment variables to avoid auth errors
    os.environ["EQUINIX_CLIENT_ID"] = "dummy"
    os.environ["EQUINIX_CLIENT_SECRET"] = "dummy"

    # Initialize the server
    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    # Get the tools
    if server.mcp is None:
        print("❌ MCP server not initialized!")
        return

    tools = server.mcp._tool_manager._tools

    # Check if the fabric_searchConnections tool exists
    tool_name = "fabric_searchConnections"
    if tool_name not in tools:
        print(f"❌ Tool {tool_name} not found!")
        print(f"Available tools: {list(tools.keys())}")
        return

    tool = tools[tool_name]
    print(f"✅ Tool {tool_name} found!")
    print(f"Tool parameters schema:")
    print(f"Type: {tool.parameters.get('type', 'unknown')}")
    print(f"Properties: {list(tool.parameters.get('properties', {}).keys())}")
    print(f"Required: {tool.parameters.get('required', [])}")

    # Check if it has the expected properties
    properties = tool.parameters.get("properties", {})
    expected_props = ["filter", "pagination", "sort"]

    print(f"\nExpected properties: {expected_props}")
    print(f"Actual properties: {list(properties.keys())}")

    for prop in expected_props:
        if prop in properties:
            print(f"✅ {prop}: Found")
        else:
            print(f"❌ {prop}: Missing")

    if properties:
        print(f"\n🎉 Tool now has {len(properties)} parameters!")
    else:
        print(f"\n❌ Tool still has no parameters")


if __name__ == "__main__":
    asyncio.run(test_tool_schema())
