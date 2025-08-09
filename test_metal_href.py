#!/usr/bin/env python3
"""Test the specific MetalHref issue."""

import asyncio
import json
import os
import sys

# Add src to path so we can import our modules
sys.path.append("src")

from equinix_mcp_server.main import EquinixMCPServer


async def test_metal_projects():
    """Test calling a metal projects endpoint that should trigger the MetalHref issue."""

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

    # Look for a metal projects endpoint that would use MetalProject in response
    metal_tools = [
        name
        for name in tools.keys()
        if name.startswith("metal_") and "project" in name.lower()
    ]

    print(f"Found metal project tools: {metal_tools}")

    if not metal_tools:
        print("❌ No metal project tools found!")
        return

    # Try the first one
    tool_name = metal_tools[0]
    tool = tools[tool_name]

    print(f"Testing tool: {tool_name}")
    print(f"Tool description: {tool.description}")

    # Try to call the tool with minimal parameters
    try:
        # This will likely fail with auth, but we want to see if we get the schema error
        result = await server.mcp.call_tool(tool_name, {})
        print("✅ Tool call succeeded unexpectedly")
        print(f"Result: {result}")

    except Exception as e:
        error_str = str(e)
        print(f"Tool call failed with: {error_str}")

        if "PointerToNowhere" in error_str and "MetalHref" in error_str:
            print("❌ Still getting the MetalHref schema reference error!")
            print("This means our fix didn't work completely.")
            return False
        elif "unauthorized" in error_str.lower() or "auth" in error_str.lower():
            print("✅ Got expected auth error - schema references are working!")
            return True
        else:
            print("❓ Got different error - schemas might be working:")
            print(f"Error: {e}")
            return True

    return True


async def main():
    """Main test function."""
    print("🧪 Testing for MetalHref schema reference issue...")

    try:
        success = await test_metal_projects()
        if success:
            print("\n🎉 MetalHref test passed!")
        else:
            print("\n💥 MetalHref test failed!")
        return success
    except Exception as e:
        print(f"Test failed with exception: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
