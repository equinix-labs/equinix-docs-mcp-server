#!/usr/bin/env python3
"""Test the MCP server initialization and authentication."""

import asyncio
import logging
import os
import sys

# Add src to path
sys.path.insert(0, "src")

from equinix_mcp_server.main import EquinixMCPServer

# Set up logging to see everything
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


async def test_mcp_server():
    """Test the MCP server initialization and authentication."""
    print("=" * 70)
    print("TESTING MCP SERVER INITIALIZATION AND AUTHENTICATION")
    print("=" * 70)

    # Check environment variables
    client_id = os.getenv("EQUINIX_CLIENT_ID")
    client_secret = os.getenv("EQUINIX_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("❌ Missing required credentials!")
        return

    print(f"✅ CLIENT_ID: {client_id[:10]}...")
    print(f"✅ CLIENT_SECRET: {client_secret[:10]}...")

    # Initialize MCP server
    print("\n1. Initializing MCP server...")
    try:
        server = EquinixMCPServer("config/apis.yaml")
        print("✅ MCP server created")

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

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_mcp_server())
