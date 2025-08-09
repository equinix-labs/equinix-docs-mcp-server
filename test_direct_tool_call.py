#!/usr/bin/env python3
"""
Direct tool call attempt - try to invoke the equinix.metal_findProjects tool directly
"""

import asyncio
import os

from mcp.types import CallToolRequest, CallToolRequestParams

# Set experimental parser
os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

from src.equinix_mcp_server.main import EquinixMCPServer


async def test_direct_tool_call():
    print("🚀 Direct Tool Call Test: equinix.metal_findProjects with {} arguments")
    print("=" * 80)

    try:
        # Initialize server
        print("✅ Experimental parser enabled")
        server = EquinixMCPServer()
        await server.initialize()
        print("✅ Server initialized with experimental parser!")

        # Get available tools first (just to confirm they exist)
        try:
            mcp_server = server.mcp  # Get the actual FastMCP instance
            if not mcp_server:
                print("❌ MCP server is None - initialization failed")
                return

            tools = await mcp_server.get_tools()  # Need to await the coroutine!
            print(f"📋 Found {len(tools)} tools")

            # Just confirm the target tool exists without listing all tools
            target_tool_name = "equinix.metal_findProjects"
            if isinstance(tools, dict):
                if target_tool_name in tools:
                    print(f"✅ Found target tool: {target_tool_name}")
                else:
                    # Try to find it with a different naming pattern
                    found_keys = [k for k in tools.keys() if "findProjects" in k]
                    if found_keys:
                        target_tool_name = found_keys[0]
                        print(
                            f"✅ Found target tool with different name: {target_tool_name}"
                        )
                    else:
                        print(
                            f"❌ Could not find findProjects tool in {len(tools)} tools"
                        )
                        # Show a few tool names for debugging
                        sample_keys = list(tools.keys())[:5]
                        print(f"Sample tool names: {sample_keys}")
            else:
                print(f"🔍 Tools format: {type(tools)}")

        except Exception as e:
            print(f"❌ Error getting tools: {e}")
            import traceback

            traceback.print_exc()
            return

        # Try to call the tool directly using FastMCP's internal tool system
        try:
            tool_name = (
                target_tool_name
                if "target_tool_name" in locals()
                else "equinix.metal_findProjects"
            )
            mcp_server = server.mcp  # Get the actual FastMCP instance
            if not mcp_server:
                print("❌ MCP server is None")
                return

            # Use the internal tools system to call the tool directly
            print(f"🔍 Attempting to call tool: {tool_name} with arguments: {{}}")

            # Access the tools registry directly
            if hasattr(mcp_server, "_tools") and hasattr(
                mcp_server._tools, "call_tool"
            ):
                print("✅ Found internal tools system, calling tool...")
                result = await mcp_server._tools.call_tool(tool_name, {})
                print(f"✅ Tool call successful!")
                print(f"📊 Result type: {type(result)}")
                print(f"📊 Result: {result}")

                # Check if it's a ToolResult and extract content
                if hasattr(result, "content"):
                    print(f"� Tool content: {result.content}")
                if hasattr(result, "is_error"):
                    print(f"🔍 Is error: {result.is_error}")

            else:
                print("❌ Could not find internal tools system")
                # Let's explore what's available
                print(
                    f"🔍 MCP server attributes: {[attr for attr in dir(mcp_server) if not attr.startswith('_')]}"
                )

                # Try alternative approaches
                attrs = [attr for attr in dir(mcp_server) if "tool" in attr.lower()]
                print(f"🔍 Tool-related attributes: {attrs}")

        except Exception as e:
            print(f"❌ Error calling tool: {e}")
            import traceback

            traceback.print_exc()

    except Exception as e:
        print(f"❌ Server initialization error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_direct_tool_call())
