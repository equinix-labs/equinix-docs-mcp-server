#!/usr/bin/env python3
"""Quick test script for FastMCP tool calls without LLM processing."""

import asyncio
import json
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def test_metal_find_projects():
    """Test the equinix.metal_findProjects tool directly."""
    print("🔄 Initializing MCP server...")

    # Initialize the server
    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    print("✅ MCP server initialized!")
    print("🔄 Testing equinix.metal_findProjects tool...")

    try:
        # Get the FastMCP server instance
        mcp_server = server.mcp

        # Find the tool by name
        tool_name = "equinix.metal_findProjects"

        # Get all available tools to verify our tool exists
        # FastMCP stores tools in different ways, let's try several approaches
        tools = None
        if hasattr(mcp_server, "_tools"):
            tools = mcp_server._tools
        elif hasattr(mcp_server, "tools"):
            tools = mcp_server.tools
        elif hasattr(mcp_server, "_handlers"):
            # Check if tools are in handlers
            handlers = mcp_server._handlers
            if "tools" in handlers:
                tools = handlers["tools"]

        if tools is None:
            print("❌ Could not find tools collection in FastMCP server")
            # Let's inspect the mcp_server object
            print(f"MCP server type: {type(mcp_server)}")
            print(
                f"MCP server attributes: {[attr for attr in dir(mcp_server) if not attr.startswith('_')]}"
            )
            return

        if tool_name not in tools:
            print(f"❌ Tool '{tool_name}' not found!")
            available_tools = (
                list(tools.keys()) if hasattr(tools, "keys") else str(tools)
            )
            print(
                f"Available tools: {available_tools[:10] if isinstance(available_tools, list) else available_tools}..."
            )
            return

        print(f"✅ Found tool: {tool_name}")

        # Call the tool directly with empty parameters
        print("🔄 Calling tool with parameters: {}")
        tool_func = tools[tool_name]
        result = await tool_func()

        print("✅ Tool call completed successfully!")
        print(f"📄 Result type: {type(result)}")

        # Print the result (truncated if too long)
        if isinstance(result, str):
            if len(result) > 500:
                print(f"📄 Result (first 500 chars): {result[:500]}...")
            else:
                print(f"📄 Result: {result}")
        else:
            print(f"📄 Result: {result}")

    except Exception as e:
        print(f"❌ Error calling tool: {e}")
        import traceback

        traceback.print_exc()


async def test_with_parameters():
    """Test the tool with specific parameters."""
    print("\n" + "=" * 60)
    print("🔄 Testing with specific parameters...")

    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    try:
        mcp_server = server.mcp
        tool_name = "equinix.metal_findProjects"

        # Try to find tools using different methods
        tools = None
        if hasattr(mcp_server, "_tools"):
            tools = mcp_server._tools
        elif hasattr(mcp_server, "tools"):
            tools = mcp_server.tools
        elif hasattr(mcp_server, "_handlers"):
            handlers = mcp_server._handlers
            if "tools" in handlers:
                tools = handlers["tools"]

        if tools is None:
            print("❌ Could not find tools collection")
            return

        # Test with pagination parameters
        params = {"page": 1, "per_page": 100}
        print(f"🔄 Calling tool with parameters: {params}")

        tool_func = tools[tool_name]
        result = await tool_func(**params)

        print("✅ Tool call with parameters completed successfully!")
        if isinstance(result, str):
            if len(result) > 500:
                print(f"📄 Result (first 500 chars): {result[:500]}...")
            else:
                print(f"📄 Result: {result}")
        else:
            print(f"📄 Result: {result}")

    except Exception as e:
        print(f"❌ Error calling tool with parameters: {e}")
        import traceback

        traceback.print_exc()


async def main():
    """Run the tests."""
    print("🚀 FastMCP Tool Test Runner")
    print("=" * 60)

    # Test 1: Empty parameters
    await test_metal_find_projects()

    # Test 2: With parameters
    await test_with_parameters()

    print("\n✅ All tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
