#!/usr/bin/env python3
"""Test the updated auth manager with JSON format."""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.auth import AuthManager
from equinix_mcp_server.config import Config


async def test_auth_manager():
    """Test the auth manager directly."""
    print("🔐 Testing Updated AuthManager with JSON format...")

    # Check credentials
    client_id = os.getenv("EQUINIX_CLIENT_ID")
    client_secret = os.getenv("EQUINIX_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("❌ Missing credentials")
        return

    print(f"Client ID: ✅ Set ({client_id[:10]}...)")
    print(f"Client Secret: ✅ Set ({client_secret[:10]}...)")

    # Initialize config and auth manager
    config = Config.load("config/apis.yaml")
    auth_manager = AuthManager(config)

    try:
        print("\n🧪 Testing direct token request with JSON format...")
        token = await auth_manager._get_access_token()
        print(f"✅ Got access token: {token[:20]}...")

        print("\n🧪 Testing full auth flow...")
        auth_header = await auth_manager.get_auth_header("fabric")
        print(f"✅ Got auth header: {list(auth_header.keys())}")

        # Test API call
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.equinix.com/fabric/v4/connections/search",
                headers=auth_header,
                json={
                    "filter": {
                        "and": [
                            {
                                "property": "/direction",
                                "operator": "=",
                                "values": ["OUTGOING"],
                            }
                        ]
                    },
                    "pagination": {"limit": 1, "offset": 0},
                },
            )
            print(f"API Status: {response.status_code}")
            if response.status_code == 200:
                print("✅ API call successful with JSON auth format!")
            else:
                print(f"❌ API call failed: {response.text}")

    except Exception as e:
        print(f"❌ Auth test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_auth_manager())
