#!/usr/bin/env python3
"""Test equinix.metal_findProjects tool using experimental parser."""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))


async def test_metal_find_projects_experimental():
    """Test with experimental parser enabled."""
    print("🚀 Testing equinix.metal_findProjects with Experimental Parser")
    print("=" * 70)

    # Enable experimental parser
    os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"
    print("✅ Experimental parser enabled")

    # Import after setting environment variable
    from equinix_mcp_server.main import EquinixMCPServer

    try:
        print("🔄 Initializing MCP server...")
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        print("✅ MCP server initialized!")

        # Get the FastMCP server instance
        mcp_server = server.mcp
        print(f"📄 MCP server type: {type(mcp_server)}")

        # Check available attributes on the server
        attrs = [
            attr
            for attr in dir(mcp_server)
            if not attr.startswith("_") and "tool" in attr.lower()
        ]
        print(f"📄 Tool-related attributes: {attrs}")

        # Try to access the server's internal tool structure
        print("🔍 Exploring server structure...")

        # Check if we can access the underlying MCP server
        if hasattr(mcp_server, "server"):
            underlying_server = mcp_server.server
            print(f"📄 Underlying server type: {type(underlying_server)}")

            # Look for tool handlers
            if hasattr(underlying_server, "_handlers"):
                handlers = underlying_server._handlers
                print(f"📄 Available handlers: {list(handlers.keys())}")

                # Check for call_tool handler
                from mcp.types import CallToolRequest

                if CallToolRequest in handlers:
                    print("✅ Found CallToolRequest handler!")
                    call_tool_handler = handlers[CallToolRequest]
                    print(f"📄 Handler type: {type(call_tool_handler)}")

                    # Try to call the tool through the handler
                    try:
                        print("🔄 Attempting to call equinix.metal_findProjects...")

                        # Create proper MCP request
                        from mcp.types import CallToolRequestParams

                        params = CallToolRequestParams(
                            name="equinix.metal_findProjects", arguments={}
                        )

                        request = CallToolRequest(method="tools/call", params=params)

                        # Call the handler directly
                        print("📡 Making tool call...")
                        result = await call_tool_handler(request)

                        print("✅ Tool call completed!")
                        print(f"📄 Result type: {type(result)}")
                        print(f"📄 Result: {result}")

                        # Try to extract content
                        if hasattr(result, "content") and result.content:
                            print(f"📄 Content items: {len(result.content)}")
                            for i, item in enumerate(result.content):
                                if hasattr(item, "text"):
                                    text = item.text
                                    if len(text) > 1000:
                                        print(
                                            f"📄 Content {i+1} (first 1000 chars): {text[:1000]}..."
                                        )
                                    else:
                                        print(f"📄 Content {i+1}: {text}")

                    except Exception as e:
                        print(f"❌ Error calling tool handler: {e}")
                        print(f"📄 Error type: {type(e)}")
                        import traceback

                        traceback.print_exc()

                        # This is the schema error we're looking for!
                        if any(
                            keyword in str(e).lower()
                            for keyword in ["schema", "pointer", "validation", "$defs"]
                        ):
                            print("\n🎯 FOUND THE SCHEMA ISSUE!")
                            print("This is exactly what you're trying to debug.")

        # Also try to list tools to see what's available
        print("\n🔍 Checking available tools...")
        if hasattr(underlying_server, "_handlers"):
            from mcp.types import ListToolsRequest

            if ListToolsRequest in handlers:
                print("✅ Found ListToolsRequest handler")
                list_handler = handlers[ListToolsRequest]

                try:
                    list_request = ListToolsRequest(method="tools/list")
                    tools_result = await list_handler(list_request)

                    print(f"📄 Found {len(tools_result.tools)} tools")

                    # Look for our target tool
                    target_found = False
                    for tool in tools_result.tools:
                        if tool.name == "equinix.metal_findProjects":
                            print(f"✅ Found target tool: {tool.name}")
                            print(f"   Description: {tool.description}")
                            target_found = True

                            # Check input schema
                            if hasattr(tool, "inputSchema"):
                                schema = tool.inputSchema
                                print(f"   Input schema: {schema}")

                    if not target_found:
                        print("❌ Target tool not found in list")
                        metal_tools = [
                            t.name
                            for t in tools_result.tools
                            if "metal" in t.name.lower()
                        ]
                        print(f"📄 Metal tools found: {metal_tools[:5]}")

                except Exception as e:
                    print(f"❌ Error listing tools: {e}")
                    import traceback

                    traceback.print_exc()

    except Exception as e:
        print(f"❌ Error in main test: {e}")
        import traceback

        traceback.print_exc()


async def test_simple_tool_call():
    """Try a simpler approach to call the tool."""
    print("\n" + "=" * 70)
    print("🔄 Trying simple tool call approach...")

    os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

    from equinix_mcp_server.main import EquinixMCPServer

    try:
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        mcp_server = server.mcp

        # Check if we can find tools another way
        if hasattr(mcp_server, "_tool_registry") or hasattr(mcp_server, "tools"):
            print("🔍 Looking for tool registry...")

            # Try different possible attributes
            possible_attrs = ["_tool_registry", "tools", "_tools", "tool_handlers"]
            for attr_name in possible_attrs:
                if hasattr(mcp_server, attr_name):
                    attr_value = getattr(mcp_server, attr_name)
                    print(f"📄 Found {attr_name}: {type(attr_value)}")

                    # If it's a dict-like with our tool
                    if hasattr(attr_value, "get") and callable(attr_value.get):
                        tool_func = attr_value.get("equinix.metal_findProjects")
                        if tool_func:
                            print(f"✅ Found tool function: {type(tool_func)}")

                            # Try to call it
                            try:
                                print("🔄 Calling tool function with {}...")
                                result = await tool_func()
                                print(f"✅ Success! Result: {result}")
                                return
                            except Exception as e:
                                print(f"❌ Tool function error: {e}")
                                import traceback

                                traceback.print_exc()

                                # This might be our schema error!
                                if any(
                                    keyword in str(e).lower()
                                    for keyword in [
                                        "schema",
                                        "pointer",
                                        "validation",
                                        "$defs",
                                    ]
                                ):
                                    print(
                                        "\n🎯 FOUND THE SCHEMA ISSUE IN TOOL FUNCTION!"
                                    )
                                    return

        print("❌ Could not find direct tool access method")

    except Exception as e:
        print(f"❌ Error in simple test: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_metal_find_projects_experimental())
    asyncio.run(test_simple_tool_call())
