#!/usr/bin/env python3
"""Test the specific provider resources search endpoint that's failing."""

import asyncio
import logging
import os
import sys

# Add src to path
sys.path.insert(0, "src")

from equinix_mcp_server.auth import AuthManager
from equinix_mcp_server.config import Config

# Set up logging to see everything
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


async def test_provider_resources():
    """Test the specific fabric provider resources search endpoint."""
    print("=" * 70)
    print("TESTING FABRIC PROVIDER RESOURCES SEARCH ENDPOINT")
    print("=" * 70)

    # Check environment variables
    client_id = os.getenv("EQUINIX_CLIENT_ID")
    client_secret = os.getenv("EQUINIX_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("❌ Missing required credentials!")
        return

    print(f"✅ CLIENT_ID: {client_id[:10]}...")
    print(f"✅ CLIENT_SECRET: {client_secret[:10]}...")

    # Load config and get auth
    config = Config.load("config/apis.yaml")
    auth_manager = AuthManager(config)

    # Get auth header for fabric service
    print("\n1. Getting authentication header...")
    auth_header = await auth_manager.get_auth_header("fabric")
    print(f"✅ Auth header obtained: {list(auth_header.keys())}")

    # Test the exact endpoint that's failing
    import httpx

    url = "https://api.equinix.com/fabric/v4/providerResources/search"
    headers = {"Content-Type": "application/json", **auth_header}

    # Use minimal search request
    search_request = {"filter": {}, "pagination": {"offset": 0, "limit": 5}}

    print(f"\n2. Testing POST request to: {url}")
    print(f"Headers: {list(headers.keys())}")
    print(f"Request body: {search_request}")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=search_request)

            print(f"\n3. Response received:")
            print(f"Status: {response.status_code}")
            print(f"Headers: {dict(response.headers)}")

            if response.status_code == 200:
                print("✅ SUCCESS! Provider resources search works")
                data = response.json()
                print(
                    f"Response data keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}"
                )
            else:
                print(f"❌ FAILED with status {response.status_code}")
                print(f"Response text: {response.text}")

                # If it's a 401, let's check if our token is actually valid
                if response.status_code == 401:
                    print("\n4. Checking if token is valid with another endpoint...")
                    test_url = "https://api.equinix.com/fabric/v4/connections/search"
                    test_body = {"pagination": {"offset": 0, "limit": 1}}

                    test_response = await client.post(
                        test_url, headers=headers, json=test_body
                    )
                    print(f"Test endpoint status: {test_response.status_code}")
                    if test_response.status_code == 200:
                        print("✅ Token works for connections/search endpoint")
                        print(
                            "❌ But fails for providerResources/search - possible permissions issue"
                        )
                    else:
                        print(
                            f"❌ Token also fails for connections/search: {test_response.text}"
                        )

        except Exception as e:
            print(f"❌ Request failed: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_provider_resources())
