#!/usr/bin/env python3
"""Quick test script to test Metal API calls directly through authenticated client."""

import asyncio
import json
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def test_direct_api_call():
    """Test the Metal findProjects API call directly through authenticated client."""
    print("🔄 Initializing MCP server...")

    # Initialize the server
    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    print("✅ MCP server initialized!")
    print("🔄 Making direct API call to Metal findProjects...")

    try:
        # Get the authenticated client from the server
        # We need to create one like the server does
        from equinix_mcp_server.main import AuthenticatedClient

        client = AuthenticatedClient(
            server.auth_manager, base_url="https://api.equinix.com"
        )

        # Make the API call directly
        print("🔄 Calling GET /metal/v1/projects")

        async with client:
            response = await client.request("GET", "/metal/v1/projects")

            print(f"✅ API call completed!")
            print(f"📄 Status Code: {response.status_code}")
            print(f"📄 Headers: {dict(response.headers)}")

            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"📄 Response type: {type(data)}")

                    # Pretty print the JSON response (truncated)
                    json_str = json.dumps(data, indent=2)
                    if len(json_str) > 1000:
                        print(f"📄 Response (first 1000 chars):\n{json_str[:1000]}...")
                    else:
                        print(f"📄 Response:\n{json_str}")

                except Exception as e:
                    print(f"📄 Response text: {response.text[:500]}...")
                    print(f"❌ JSON parsing error: {e}")
            else:
                print(f"❌ API call failed with status {response.status_code}")
                print(f"📄 Response: {response.text}")

    except Exception as e:
        print(f"❌ Error making API call: {e}")
        import traceback

        traceback.print_exc()


async def test_with_query_params():
    """Test the API call with query parameters."""
    print("\n" + "=" * 60)
    print("🔄 Testing with query parameters...")

    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    try:
        from equinix_mcp_server.main import AuthenticatedClient

        client = AuthenticatedClient(
            server.auth_manager, base_url="https://api.equinix.com"
        )

        # Test with pagination parameters
        params = {"page": 1, "per_page": 5}
        print(f"🔄 Calling GET /metal/v1/projects with params: {params}")

        async with client:
            response = await client.request("GET", "/metal/v1/projects", params=params)

            print(f"✅ API call with parameters completed!")
            print(f"📄 Status Code: {response.status_code}")

            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"📄 Response type: {type(data)}")

                    # Check if it's paginated response
                    if isinstance(data, dict):
                        if "projects" in data:
                            projects = data["projects"]
                            print(f"📄 Found {len(projects)} projects")
                        if "meta" in data:
                            meta = data["meta"]
                            print(f"📄 Pagination meta: {meta}")

                    # Pretty print the JSON response (truncated)
                    json_str = json.dumps(data, indent=2)
                    if len(json_str) > 1500:
                        print(f"📄 Response (first 1500 chars):\n{json_str[:1500]}...")
                    else:
                        print(f"📄 Response:\n{json_str}")

                except Exception as e:
                    print(f"📄 Response text: {response.text[:500]}...")
                    print(f"❌ JSON parsing error: {e}")
            else:
                print(f"❌ API call failed with status {response.status_code}")
                print(f"📄 Response: {response.text}")

    except Exception as e:
        print(f"❌ Error making API call with parameters: {e}")
        import traceback

        traceback.print_exc()


async def inspect_fastmcp_tools():
    """Inspect the FastMCP server to understand its structure."""
    print("\n" + "=" * 60)
    print("🔄 Inspecting FastMCP server structure...")

    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()

    try:
        mcp_server = server.mcp
        print(f"📄 MCP server type: {type(mcp_server)}")
        print(f"📄 MCP server module: {type(mcp_server).__module__}")

        # List all attributes
        attributes = [attr for attr in dir(mcp_server) if not attr.startswith("_")]
        print(f"📄 Public attributes: {attributes}")

        # Look for tool-related attributes
        tool_attrs = [attr for attr in attributes if "tool" in attr.lower()]
        print(f"📄 Tool-related attributes: {tool_attrs}")

        # Try to find tools in various ways
        possible_tool_locations = [
            "tools",
            "_tools",
            "handlers",
            "_handlers",
            "routes",
            "_routes",
            "endpoints",
            "_endpoints",
        ]

        for attr_name in possible_tool_locations:
            if hasattr(mcp_server, attr_name):
                attr_value = getattr(mcp_server, attr_name)
                print(
                    f"📄 {attr_name}: {type(attr_value)} - {str(attr_value)[:100]}..."
                )

                # If it's a dict-like object, show some keys
                if hasattr(attr_value, "keys"):
                    keys = list(attr_value.keys())[:10]
                    print(f"   Keys: {keys}")

    except Exception as e:
        print(f"❌ Error inspecting FastMCP: {e}")
        import traceback

        traceback.print_exc()


async def main():
    """Run the tests."""
    print("🚀 Metal API Direct Test Runner")
    print("=" * 60)

    # Test 1: Inspect FastMCP structure
    await inspect_fastmcp_tools()

    # Test 2: Direct API call without parameters
    await test_direct_api_call()

    # Test 3: Direct API call with parameters
    await test_with_query_params()

    print("\n✅ All tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
