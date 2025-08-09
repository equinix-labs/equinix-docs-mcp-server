#!/usr/bin/env python3
"""
Final test - call the metal_findProjects tool using the _mcp_call_tool method
"""

import asyncio
import os

from mcp.types import CallToolRequest, CallToolRequestParams

# Set experimental parser
os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

from src.equinix_mcp_server.main import EquinixMCPServer


async def test_metal_findprojects():
    print("🚀 FINAL TEST: Call metal_findProjects with {} arguments")
    print("=" * 80)

    try:
        # Initialize server
        print("✅ Experimental parser enabled")
        server = EquinixMCPServer()
        await server.initialize()
        print("✅ Server initialized with experimental parser!")

        mcp_server = server.mcp
        if not mcp_server:
            print("❌ MCP server is None - initialization failed")
            return

        tool_name = "metal_findProjects"
        print(f"🎯 Calling tool: {tool_name} with arguments: {{}}")

        # Try using _mcp_call_tool which should handle MCP protocol
        try:
            request = CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(name=tool_name, arguments={}),
            )

            result = await mcp_server._mcp_call_tool(request)
            print(f"✅ Tool call successful via _mcp_call_tool!")
            print(f"📊 Result type: {type(result)}")
            print(f"📊 Result: {result}")

            # Check if there's an error in the result
            if hasattr(result, "content"):
                print(f"📋 Content: {result.content}")
            if hasattr(result, "isError"):
                print(f"🔍 Is Error: {result.isError}")

        except Exception as e:
            print(f"❌ Error with _mcp_call_tool: {e}")
            import traceback

            traceback.print_exc()

            # Try the other _call_tool method
            try:
                print(f"🔄 Trying _call_tool method...")
                result = await mcp_server._call_tool(tool_name, {})
                print(f"✅ Tool call successful via _call_tool!")
                print(f"📊 Result type: {type(result)}")
                print(f"📊 Result: {result}")

            except Exception as e2:
                print(f"❌ Error with _call_tool: {e2}")
                import traceback

                traceback.print_exc()

    except Exception as e:
        print(f"❌ Server initialization error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_metal_findprojects())
