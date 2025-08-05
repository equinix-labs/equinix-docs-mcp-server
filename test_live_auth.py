#!/usr/bin/env python3
"""
Test live authentication with actual credentials
"""
import asyncio
import os
import sys

sys.path.insert(0, "src")

from equinix_mcp_server.auth import AuthManager


async def test_live_auth():
    """Test authentication with real credentials"""
    print("🔧 Testing Live Authentication...")

    # Check environment variables
    client_id = os.getenv("EQUINIX_CLIENT_ID")
    client_secret = os.getenv("EQUINIX_CLIENT_SECRET")

    if not client_id:
        print("❌ EQUINIX_CLIENT_ID not set")
        return
    if not client_secret:
        print("❌ EQUINIX_CLIENT_SECRET not set")
        return

    print(f"✅ EQUINIX_CLIENT_ID: {client_id[:10]}...")
    print(f"✅ EQUINIX_CLIENT_SECRET: {client_secret[:10]}...")

    # Test authentication
    try:
        from equinix_mcp_server.config import Config

        # Load config
        config = Config.load("config/apis.yaml")
        auth_manager = AuthManager(config)

        print("\n🔑 Requesting OAuth2 token...")
        token = await auth_manager._get_access_token()

        if token:
            print(f"✅ Got access token: {token[:20]}...")
            print("✅ Authentication successful!")

            # Test a simple API call with the token
            print("\n🧪 Testing API call with token...")

            import httpx

            async with httpx.AsyncClient() as client:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }

                # Try a simple Fabric connections search
                response = await client.post(
                    "https://api.equinix.com/fabric/v4/connections/search",
                    headers=headers,
                    json={"pagination": {"offset": 0, "limit": 5}},
                )

                print(f"API Response Status: {response.status_code}")

                if response.status_code == 200:
                    data = response.json()
                    total = data.get("pagination", {}).get("total", 0)
                    print(f"✅ API call successful! Found {total} connections")
                else:
                    print(f"❌ API call failed: {response.text}")

        else:
            print("❌ Failed to get access token")

    except Exception as e:
        print(f"❌ Authentication test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_live_auth())
