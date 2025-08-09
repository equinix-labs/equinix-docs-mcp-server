#!/usr/bin/env python3
"""Direct test of the equinix.metal_findProjects MCP tool."""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def test_metal_find_projects_tool():
    """Test the equinix.metal_findProjects tool directly via MCP."""
    print("🚀 Testing equinix.metal_findProjects MCP Tool")
    print("=" * 60)

    try:
        print("🔄 Initializing MCP server...")
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        print("✅ MCP server initialized!")

        # Get the FastMCP server instance
        mcp_server = server.mcp

        print("🔄 Calling equinix.metal_findProjects with {} arguments...")

        # Create the MCP tool call request using the proper MCP types
        from mcp.types import CallToolRequest, CallToolRequestParams

        # Create the request with the tool name and empty arguments
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="equinix.metal_findProjects", arguments={}
            ),
        )

        # Call the tool through the MCP protocol
        print("📡 Making MCP tool call...")
        result = await mcp_server.call_tool(request)

        print("✅ Tool call completed!")
        print(f"📄 Result type: {type(result)}")

        # Extract and display the result content
        if hasattr(result, "content") and result.content:
            print(f"📄 Content items: {len(result.content)}")

            for i, content_item in enumerate(result.content):
                print(f"📄 Content item {i+1}:")
                print(f"   Type: {type(content_item)}")

                if hasattr(content_item, "type"):
                    print(f"   Content type: {content_item.type}")

                if hasattr(content_item, "text"):
                    text = content_item.text
                    if len(text) > 1000:
                        print(f"   Text (first 1000 chars): {text[:1000]}...")
                    else:
                        print(f"   Text: {text}")

                # Try to parse as JSON if it looks like JSON
                if hasattr(
                    content_item, "text"
                ) and content_item.text.strip().startswith("{"):
                    try:
                        parsed = json.loads(content_item.text)
                        print(
                            f"   Parsed JSON keys: {list(parsed.keys()) if isinstance(parsed, dict) else 'not a dict'}"
                        )
                    except json.JSONDecodeError:
                        print("   Text is not valid JSON")
        else:
            print(f"📄 Raw result: {result}")

    except Exception as e:
        print(f"❌ Error calling tool: {e}")
        print(f"📄 Error type: {type(e)}")
        import traceback

        traceback.print_exc()

        # If it's a schema-related error, let's see if we can get more info
        if "schema" in str(e).lower() or "pointer" in str(e).lower():
            print("\n🔍 Schema-related error detected!")
            print("This is likely the issue you're trying to debug.")


async def test_tool_with_minimal_args():
    """Test with minimal arguments that might be required."""
    print("\n" + "=" * 60)
    print("🔄 Testing with minimal pagination arguments...")

    try:
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        mcp_server = server.mcp

        # Try with minimal pagination args
        from mcp.types import CallToolRequest, CallToolRequestParams

        args = {"page": 1, "per_page": 10}
        print(f"📡 Calling equinix.metal_findProjects with args: {args}")

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="equinix.metal_findProjects", arguments=args
            ),
        )

        result = await mcp_server.call_tool(request)

        print("✅ Tool call with args completed!")

        if hasattr(result, "content") and result.content:
            for i, content_item in enumerate(result.content):
                if hasattr(content_item, "text"):
                    text = content_item.text
                    if len(text) > 500:
                        print(f"📄 Result {i+1} (first 500 chars): {text[:500]}...")
                    else:
                        print(f"📄 Result {i+1}: {text}")

    except Exception as e:
        print(f"❌ Error with args: {e}")
        import traceback

        traceback.print_exc()


async def list_available_tools():
    """List all tools to confirm our target tool exists."""
    print("\n" + "=" * 60)
    print("🔍 Listing available tools to confirm equinix.metal_findProjects exists...")

    try:
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        mcp_server = server.mcp

        from mcp.types import ListToolsRequest

        request = ListToolsRequest(method="tools/list", params={})
        result = await mcp_server.list_tools(request)

        print(f"📄 Found {len(result.tools)} total tools")

        # Look for our target tool
        target_tool = "equinix.metal_findProjects"
        found_target = False

        metal_tools = []
        for tool in result.tools:
            if "metal" in tool.name.lower():
                metal_tools.append(tool.name)

            if tool.name == target_tool:
                found_target = True
                print(f"✅ Found target tool: {tool.name}")
                print(f"   Description: {tool.description}")
                if hasattr(tool, "inputSchema"):
                    print(f"   Input schema type: {type(tool.inputSchema)}")

        if not found_target:
            print(f"❌ Target tool '{target_tool}' not found!")

        print(f"📄 Metal-related tools ({len(metal_tools)}):")
        for tool_name in metal_tools[:10]:  # Show first 10
            print(f"   - {tool_name}")

    except Exception as e:
        print(f"❌ Error listing tools: {e}")
        import traceback

        traceback.print_exc()


async def main():
    """Run all tests."""
    # Test 1: List tools to confirm existence
    await list_available_tools()

    # Test 2: Call the tool with empty args
    await test_metal_find_projects_tool()

    # Test 3: Call with minimal args
    await test_tool_with_minimal_args()

    print("\n🏁 Tool testing completed!")


if __name__ == "__main__":
    asyncio.run(main())
