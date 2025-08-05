#!/usr/bin/env python3
"""Test the MCP server with only fabric, metal, and billing APIs (excluding network-edge)."""

import asyncio
import logging
import os
import sys

# Add src to path
sys.path.insert(0, "src")

from equinix_mcp_server.main import EquinixMCPServer

# Set up logging to see everything
logging.basicConfig(
    level=logging.INFO,  # Less verbose
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


async def test_mcp_server_without_network_edge():
    """Test the MCP server excluding network-edge API."""
    print("=" * 70)
    print("TESTING MCP SERVER WITHOUT NETWORK-EDGE API")
    print("=" * 70)

    # Check environment variables
    client_id = os.getenv("EQUINIX_CLIENT_ID")
    client_secret = os.getenv("EQUINIX_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("❌ Missing required credentials!")
        return

    print(f"✅ CLIENT_ID: {client_id[:10]}...")
    print(f"✅ CLIENT_SECRET: {client_secret[:10]}...")

    # Initialize MCP server with config that excludes network-edge
    print("\n1. Initializing MCP server without network-edge...")
    try:
        server = EquinixMCPServer("config/apis.yaml")

        # Temporarily modify the config to exclude network-edge
        if "network-edge" in server.config.apis:
            print("   Temporarily removing network-edge from config...")
            del server.config.apis["network-edge"]

        print("✅ MCP server created (without network-edge)")

        print("\n2. Initializing server components...")
        await server.initialize()
        print("✅ Server components initialized")

        print("\n3. Testing authenticated client directly...")

        # Test the authenticated client directly
        from equinix_mcp_server.main import AuthenticatedClient

        auth_client = AuthenticatedClient(
            server.auth_manager, base_url="https://api.equinix.com"
        )

        async with auth_client:
            # Try a simple fabric endpoint
            print("   Testing fabric connections/search endpoint...")
            response = await auth_client.request(
                "POST",
                "/fabric/v4/connections/search",
                json={"pagination": {"offset": 0, "limit": 1}},
            )

            print(f"   Response status: {response.status_code}")

            if response.status_code == 200:
                print("✅ Authenticated client works correctly!")
                data = response.json()
                total = data.get("pagination", {}).get("total", 0)
                print(f"   Found {total} connections")
            else:
                print(f"❌ Authenticated client failed: {response.text}")

        print("\n4. Testing MCP server tools...")
        # Check that FastMCP created tools successfully
        print(f"   MCP server initialized: {server.mcp is not None}")

        if server.mcp:
            # Try to get the list of tools
            try:
                tools = await server.mcp.list_tools()
                print(f"   Number of tools created: {len(tools)}")
                print("   Sample tools:")
                for i, tool in enumerate(tools[:5]):  # Show first 5 tools
                    print(f"     {i+1}. {tool.name}: {tool.description[:60]}...")

                print("✅ MCP server tools created successfully!")

            except Exception as e:
                print(f"❌ Error listing tools: {e}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_mcp_server_without_network_edge())
