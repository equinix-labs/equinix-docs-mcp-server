#!/usr/bin/env python3
"""Ultimate quick test for equinix.metal_findProjects tool call."""

import asyncio
import os
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def quick_test_metal_find_projects():
    """Test the metal findProjects endpoint directly."""
    print("🚀 Quick Metal API Test")
    print("=" * 50)

    # Check environment
    metal_token = os.getenv("EQUINIX_METAL_TOKEN")
    if not metal_token:
        print("❌ EQUINIX_METAL_TOKEN environment variable not set")
        print("💡 Set it with: export EQUINIX_METAL_TOKEN='your-token-here'")
        return

    print(f"✅ Metal token found: {metal_token[:10]}...")

    try:
        print("🔄 Initializing server...")
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        print("✅ Server initialized!")

        # Test direct API call
        from equinix_mcp_server.main import AuthenticatedClient

        print("🔄 Making direct API call to /metal/v1/projects...")

        async with AuthenticatedClient(
            server.auth_manager, "https://api.equinix.com"
        ) as client:
            response = await client.request("GET", "/metal/v1/projects")

            print(f"📄 Status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print(f"✅ Success! Found data type: {type(data)}")

                # Check if it has projects
                if isinstance(data, dict) and "projects" in data:
                    projects = data["projects"]
                    print(f"📄 Number of projects: {len(projects)}")

                    if projects:
                        first_project = projects[0]
                        print(f"📄 First project keys: {list(first_project.keys())}")
                        if "name" in first_project:
                            print(f"📄 First project name: {first_project['name']}")
                else:
                    print(
                        f"📄 Response structure: {list(data.keys()) if isinstance(data, dict) else type(data)}"
                    )

                # Show sample of response
                import json

                json_str = json.dumps(data, indent=2)
                if len(json_str) > 500:
                    print(f"📄 Response (first 500 chars):\n{json_str[:500]}...")
                else:
                    print(f"📄 Full response:\n{json_str}")

            else:
                print(f"❌ Error: {response.status_code}")
                print(f"📄 Response: {response.text}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


async def test_with_params():
    """Test with query parameters."""
    print("\n" + "=" * 50)
    print("🔄 Testing with pagination...")

    metal_token = os.getenv("EQUINIX_METAL_TOKEN")
    if not metal_token:
        print("❌ EQUINIX_METAL_TOKEN not set")
        return

    try:
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        from equinix_mcp_server.main import AuthenticatedClient

        params = {"page": 1, "per_page": 2}
        print(f"🔄 Making API call with params: {params}")

        async with AuthenticatedClient(
            server.auth_manager, "https://api.equinix.com"
        ) as client:
            response = await client.request("GET", "/metal/v1/projects", params=params)

            print(f"📄 Status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print(f"✅ Success with pagination!")

                # Check pagination info
                if isinstance(data, dict):
                    if "meta" in data:
                        meta = data["meta"]
                        print(f"📄 Pagination meta: {meta}")
                    if "projects" in data:
                        projects = data["projects"]
                        print(
                            f"📄 Returned {len(projects)} projects (limited by per_page={params['per_page']})"
                        )

            else:
                print(f"❌ Error: {response.status_code}")
                print(f"📄 Response: {response.text}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(quick_test_metal_find_projects())
    asyncio.run(test_with_params())
