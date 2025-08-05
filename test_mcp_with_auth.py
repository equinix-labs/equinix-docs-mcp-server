#!/usr/bin/env python3
"""
Test the full MCP server with real authentication
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, "src")

from equinix_mcp_server.main import EquinixMCPServer


async def test_mcp_with_auth():
    """Test the full MCP server with authentication"""
    print("🔧 Testing MCP Server with Live Authentication...")

    # Check environment variables
    client_id = os.getenv("EQUINIX_CLIENT_ID")
    client_secret = os.getenv("EQUINIX_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("❌ Missing credentials")
        return

    print(f"✅ EQUINIX_CLIENT_ID: {client_id[:10]}...")
    print(f"✅ EQUINIX_CLIENT_SECRET: {client_secret[:10]}...")

    try:
        # Initialize the MCP server
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        print("✅ MCP Server initialized successfully")

        # Test fabric_searchConnections through the MCP server
        print("\n🧪 Testing fabric_searchConnections via MCP...")

        # Call the tool through FastMCP
        result = await server.mcp.call_tool(
            "fabric_searchConnections",
            {"body": {"pagination": {"offset": 0, "limit": 5}}},
        )

        print("✅ Tool call successful!")

        if hasattr(result, "content") and result.content:
            try:
                data = json.loads(result.content[0].text)
                total = data.get("pagination", {}).get("total", 0)
                connections = data.get("data", [])
                print(f"   - Total connections: {total}")
                print(f"   - Returned: {len(connections)} connections")

                if connections:
                    first_conn = connections[0]
                    print(
                        f"   - First connection: {first_conn.get('name', 'N/A')} ({first_conn.get('uuid', 'N/A')[:8]}...)"
                    )

            except Exception as e:
                print(f"   - Response parsing error: {e}")
                print(f"   - Raw response: {result.content[0].text[:200]}...")

    except Exception as e:
        print(f"❌ MCP server test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_mcp_with_auth())
