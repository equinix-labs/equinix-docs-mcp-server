#!/usr/bin/env python3
"""Quick test script for FastMCP tool calls using proper MCP protocol."""

import asyncio
import json
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def test_metal_find_projects():
    """Test the equinix.metal_findProjects tool using MCP call_tool method."""
    print("🔄 Initializing MCP server...")

    # Initialize the server
    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    print("✅ MCP server initialized!")
    print("🔄 Testing equinix.metal_findProjects tool...")

    try:
        # Get the FastMCP server instance
        mcp_server = server.mcp

        tool_name = "equinix.metal_findProjects"

        # Use the proper MCP call_tool method
        print(f"🔄 Calling tool '{tool_name}' with empty parameters...")

        # Create MCP tool call request
        from mcp.types import CallToolRequest

        request = CallToolRequest(name=tool_name, arguments={})

        # Call the tool using MCP protocol
        result = await mcp_server.call_tool(request)

        print("✅ Tool call completed successfully!")
        print(f"📄 Result type: {type(result)}")

        # Print the result content
        if hasattr(result, "content"):
            content = result.content
            if isinstance(content, list) and len(content) > 0:
                first_content = content[0]
                if hasattr(first_content, "text"):
                    text = first_content.text
                    if len(text) > 500:
                        print(f"📄 Result (first 500 chars): {text[:500]}...")
                    else:
                        print(f"📄 Result: {text}")
                else:
                    print(f"📄 Content: {first_content}")
            else:
                print(f"📄 Content: {content}")
        else:
            print(f"📄 Result: {result}")

    except Exception as e:
        print(f"❌ Error calling tool: {e}")
        import traceback

        traceback.print_exc()


async def test_with_parameters():
    """Test the tool with specific parameters."""
    print("\n" + "=" * 60)
    print("🔄 Testing with pagination parameters...")

    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    try:
        mcp_server = server.mcp
        tool_name = "equinix.metal_findProjects"

        # Test with pagination parameters
        params = {"page": 1, "per_page": 5}
        print(f"🔄 Calling tool '{tool_name}' with parameters: {params}")

        from mcp.types import CallToolRequest

        request = CallToolRequest(name=tool_name, arguments=params)

        result = await mcp_server.call_tool(request)

        print("✅ Tool call with parameters completed successfully!")

        # Print the result content
        if hasattr(result, "content"):
            content = result.content
            if isinstance(content, list) and len(content) > 0:
                first_content = content[0]
                if hasattr(first_content, "text"):
                    text = first_content.text
                    if len(text) > 1000:
                        print(f"📄 Result (first 1000 chars): {text[:1000]}...")
                    else:
                        print(f"📄 Result: {text}")
                else:
                    print(f"📄 Content: {first_content}")
            else:
                print(f"📄 Content: {content}")
        else:
            print(f"📄 Result: {result}")

    except Exception as e:
        print(f"❌ Error calling tool with parameters: {e}")
        import traceback

        traceback.print_exc()


async def list_available_tools():
    """List all available tools."""
    print("\n" + "=" * 60)
    print("🔄 Listing available tools...")

    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    try:
        mcp_server = server.mcp

        # Use MCP list_tools method
        from mcp.types import ListToolsRequest

        request = ListToolsRequest()
        result = await mcp_server.list_tools(request)

        print(f"✅ Found {len(result.tools)} tools:")

        # Look for metal-related tools
        metal_tools = []
        for tool in result.tools:
            print(f"  - {tool.name}: {tool.description[:100]}...")
            if "metal" in tool.name.lower():
                metal_tools.append(tool.name)

        print(f"\n🔍 Metal-related tools ({len(metal_tools)}):")
        for tool_name in metal_tools:
            print(f"  - {tool_name}")

    except Exception as e:
        print(f"❌ Error listing tools: {e}")
        import traceback

        traceback.print_exc()


async def main():
    """Run the tests."""
    print("🚀 FastMCP Tool Test Runner")
    print("=" * 60)

    # Test 1: List available tools
    await list_available_tools()

    # Test 2: Empty parameters
    await test_metal_find_projects()

    # Test 3: With parameters
    await test_with_parameters()

    print("\n✅ All tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
