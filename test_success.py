#!/usr/bin/env python3
"""
SUCCESS TEST: Call metal_findProjects with proper signatures
"""

import asyncio
import os

# Set experimental parser
os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

from src.equinix_mcp_server.main import EquinixMCPServer


async def test_success():
    print("🚀 SUCCESS TEST: Call metal_findProjects with corrected signatures")
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
        arguments = {}
        print(f"🎯 Calling tool: {tool_name} with arguments: {arguments}")

        # Try the corrected _mcp_call_tool signature
        try:
            result = await mcp_server._mcp_call_tool(tool_name, arguments)
            print(f"✅ Tool call successful via _mcp_call_tool!")
            print(f"📊 Result type: {type(result)}")
            print(f"📊 Result: {result}")

            # Check if there's an error in the result
            if hasattr(result, "content"):
                print(f"📋 Content: {result.content}")
            if hasattr(result, "isError"):
                print(f"🔍 Is Error: {result.isError}")

            # SUCCESS! We got the schema error we're looking for!
            print("🎉 SUCCESS! This is the error output you need for debugging!")
            return

        except Exception as e:
            print(f"❌ Error with _mcp_call_tool: {e}")
            import traceback

            traceback.print_exc()

            # Try using internal tool manager directly
            try:
                print(f"🔄 Trying internal tool manager...")
                if hasattr(mcp_server, "_tool_manager"):
                    tool_manager = mcp_server._tool_manager
                    if hasattr(tool_manager, "call_tool"):
                        result = await tool_manager.call_tool(tool_name, arguments)
                        print(f"✅ Tool call successful via tool manager!")
                        print(f"📊 Result type: {type(result)}")
                        print(f"📊 Result: {result}")

                        # SUCCESS! We got the schema error we're looking for!
                        print(
                            "🎉 SUCCESS! This is the error output you need for debugging!"
                        )
                        return
                    else:
                        print(f"❌ Tool manager has no call_tool method")
                else:
                    print(f"❌ No tool manager found")

            except Exception as e3:
                print(f"❌ Error with tool manager: {e3}")
                import traceback

                traceback.print_exc()

    except Exception as e:
        print(f"❌ Server initialization error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_success())
