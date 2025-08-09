#!/usr/bin/env python3
"""Quick test script to test FastMCP tool discovery without API calls."""

import asyncio
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def list_tools_simple():
    """List all available tools using simple inspection."""
    print("🔄 Initializing MCP server...")

    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    print("✅ MCP server initialized!")
    print("🔄 Discovering tools...")

    try:
        mcp_server = server.mcp

        # Try to access the server's internal structure
        print(f"📄 FastMCP server type: {type(mcp_server)}")

        # Look for tools in the server's attributes
        attrs = [attr for attr in dir(mcp_server) if not attr.startswith("__")]
        print(
            f"📄 Available attributes: {[attr for attr in attrs if 'tool' in attr.lower() or 'route' in attr.lower()]}"
        )

        # Try to access the underlying server
        if hasattr(mcp_server, "_server"):
            underlying_server = mcp_server._server
            print(f"📄 Underlying server type: {type(underlying_server)}")

            # Look for handlers
            if hasattr(underlying_server, "_handlers"):
                handlers = underlying_server._handlers
                print(f"📄 Handlers available: {list(handlers.keys())}")

                # Check for tools handler
                if hasattr(underlying_server, "_tool_handler"):
                    tool_handler = underlying_server._tool_handler
                    print(f"📄 Tool handler type: {type(tool_handler)}")
                    if hasattr(tool_handler, "_tools"):
                        tools = tool_handler._tools
                        print(f"📄 Found {len(tools)} tools!")

                        # Look for metal tools
                        metal_tools = [
                            name for name in tools.keys() if "metal" in name.lower()
                        ]
                        print(f"📄 Metal tools ({len(metal_tools)}):")
                        for tool_name in metal_tools[:10]:  # Show first 10
                            print(f"  - {tool_name}")

                        # Check if our target tool exists
                        target_tool = "equinix.metal_findProjects"
                        if target_tool in tools:
                            print(f"✅ Found target tool: {target_tool}")
                            tool_func = tools[target_tool]
                            print(f"📄 Tool function type: {type(tool_func)}")
                        else:
                            print(f"❌ Target tool not found: {target_tool}")
                            # Search for similar tools
                            similar = [
                                name
                                for name in tools.keys()
                                if "findProjects" in name or "projects" in name.lower()
                            ]
                            if similar:
                                print(f"📄 Similar tools: {similar}")

        # Try direct access to _tool_handlers if available
        if hasattr(mcp_server, "_tool_handlers"):
            tool_handlers = mcp_server._tool_handlers
            print(f"📄 Direct tool handlers: {len(tool_handlers)} found")
            print(f"📄 Tool handler names: {list(tool_handlers.keys())[:10]}")

        # Check if there's a tools attribute
        if hasattr(mcp_server, "tools"):
            tools = mcp_server.tools
            print(f"📄 Direct tools access: {type(tools)}")
            if hasattr(tools, "__len__"):
                print(f"📄 Number of tools: {len(tools)}")

    except Exception as e:
        print(f"❌ Error discovering tools: {e}")
        import traceback

        traceback.print_exc()


async def simulate_tool_call():
    """Simulate calling a tool without making HTTP requests."""
    print("\n" + "=" * 60)
    print("🔄 Simulating tool call (no HTTP)...")

    try:
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        mcp_server = server.mcp

        # Try to find and inspect the tool without calling it
        target_tool = "equinix.metal_findProjects"

        # Multiple ways to access tools
        tools_found = False
        tools = None

        # Method 1: Direct access
        if hasattr(mcp_server, "_tool_handlers"):
            tools = mcp_server._tool_handlers
            tools_found = True
            print("✅ Found tools via _tool_handlers")

        # Method 2: Through underlying server
        elif hasattr(mcp_server, "_server") and hasattr(
            mcp_server._server, "_tool_handler"
        ):
            if hasattr(mcp_server._server._tool_handler, "_tools"):
                tools = mcp_server._server._tool_handler._tools
                tools_found = True
                print("✅ Found tools via _server._tool_handler._tools")

        if tools_found and tools and target_tool in tools:
            tool_func = tools[target_tool]
            print(f"✅ Found target tool: {target_tool}")
            print(f"📄 Tool function: {tool_func}")
            print(
                f"📄 Tool signature: {tool_func.__name__ if hasattr(tool_func, '__name__') else 'N/A'}"
            )

            # Try to get function signature/parameters
            import inspect

            if inspect.isfunction(tool_func) or inspect.ismethod(tool_func):
                sig = inspect.signature(tool_func)
                print(f"📄 Parameters: {list(sig.parameters.keys())}")

            print("🔄 Tool ready for testing (but would need authentication)")

        else:
            print(f"❌ Could not access tools or target tool not found")
            if tools:
                available = list(tools.keys())[:5]
                print(f"📄 Some available tools: {available}")

    except Exception as e:
        print(f"❌ Error simulating tool call: {e}")
        import traceback

        traceback.print_exc()


async def main():
    """Run the tool discovery tests."""
    print("🚀 FastMCP Tool Discovery (No Auth Required)")
    print("=" * 60)

    # Test 1: Discover tool structure
    await list_tools_simple()

    # Test 2: Simulate tool access
    await simulate_tool_call()

    print("\n✅ Tool discovery completed!")
    print(
        "💡 To test with real API calls, set EQUINIX_METAL_TOKEN environment variable"
    )


if __name__ == "__main__":
    asyncio.run(main())
