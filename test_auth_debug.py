#!/usr/bin/env python3
"""
Auth Debug Test - Check what's happening with authentication in FastMCP calls
"""

import asyncio
import logging
import os

# Set experimental parser
os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from src.equinix_mcp_server.main import EquinixMCPServer


async def test_auth_debug():
    print("🔍 AUTH DEBUG TEST: Check authentication flow in FastMCP")
    print("=" * 80)

    # Check environment variable
    token = os.getenv("EQUINIX_METAL_TOKEN")
    if token:
        print(f"✅ EQUINIX_METAL_TOKEN found (length: {len(token)})")
    else:
        print("❌ EQUINIX_METAL_TOKEN not found in environment")
        return

    try:
        # Initialize server with debug logging
        server = EquinixMCPServer()
        await server.initialize()
        print("✅ Server initialized")

        # Test our auth manager directly first
        print("\n🔧 Testing AuthManager directly:")
        auth_header = await server.auth_manager.get_auth_header("metal")
        print(f"   Auth header: {auth_header}")

        # Test our authenticated client directly
        print("\n🔧 Testing AuthenticatedClient directly:")
        from src.equinix_mcp_server.main import AuthenticatedClient

        auth_client = AuthenticatedClient(
            server.auth_manager, "https://api.equinix.com"
        )

        async with auth_client:
            response = await auth_client.request("GET", "/metal/v1/projects")
            print(f"   Direct client status: {response.status_code}")
            if response.status_code == 200:
                print("   ✅ Direct client auth works!")
            else:
                print(f"   ❌ Direct client failed: {response.text}")

        # Test through FastMCP
        print("\n🔧 Testing through FastMCP:")
        mcp_server = server.mcp
        if not mcp_server:
            print("❌ MCP server is None")
            return

        # Try to find and call the tool
        try:
            result = await mcp_server._mcp_call_tool("metal_findProjects", {})
            print(f"✅ FastMCP call successful: {type(result)}")
        except Exception as e:
            print(f"❌ FastMCP call failed: {e}")

            # Check if it's related to auth
            error_str = str(e).lower()
            if "401" in error_str or "unauthorized" in error_str:
                print("\n🚨 AUTHENTICATION ISSUE DETECTED!")
                print(
                    "   The problem is that FastMCP is not using our AuthenticatedClient properly"
                )
                print(
                    "   This means response schema processing test needs auth to be fixed first"
                )
            else:
                print(f"   Error type: {type(e)}")
                import traceback

                traceback.print_exc()

    except Exception as e:
        print(f"❌ Server initialization error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_auth_debug())
