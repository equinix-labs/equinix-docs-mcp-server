#!/usr/bin/env python3
"""Test script to debug authentication issues."""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.auth import AuthManager
from equinix_mcp_server.config import Config


async def test_auth():
    """Test authentication for different services."""
    print("🔐 Testing Equinix Authentication...")

    try:
        # Check environment variables
        client_id = os.getenv("EQUINIX_CLIENT_ID")
        client_secret = os.getenv("EQUINIX_CLIENT_SECRET")

        print(
            f"Client ID: {'✅ Set (' + client_id[:10] + '...)' if client_id else '❌ Missing'}"
        )
        print(
            f"Client Secret: {'✅ Set (' + client_secret[:10] + '...)' if client_secret else '❌ Missing'}"
        )

        if not client_id or not client_secret:
            print(
                "❌ Missing credentials. Set EQUINIX_CLIENT_ID and EQUINIX_CLIENT_SECRET"
            )
            return

        # Initialize config and auth manager
        config = Config.load("config/apis.yaml")
        auth_manager = AuthManager(config)

        # Test getting a token directly
        print("\n🧪 Testing OAuth token request...")

        # Debug the token request
        import httpx

        token_url = "https://api.equinix.com/oauth2/v1/token"

        # Try method 1: Form data with client credentials
        headers1 = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data1 = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "read write",
        }

        print(f"🧪 Method 1: Form data with client credentials")
        print(f"Token URL: {token_url}")
        print(f"Data: {list(data1.keys())}")

        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, headers=headers1, data=data1)
            print(f"Response Status: {response.status_code}")
            print(f"Response Text: {response.text}")

            if response.status_code == 200:
                token_data = response.json()
                token = token_data["access_token"]
                print(f"✅ Got access token: {token[:20]}...")
            else:
                # Try method 2: Basic Auth header
                print(f"\n🧪 Method 2: Basic Auth header")
                import base64

                credentials = f"{client_id}:{client_secret}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()

                headers2 = {
                    "Authorization": f"Basic {encoded_credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                }

                data2 = {
                    "grant_type": "client_credentials",
                    "scope": "read write",
                }

                response = await client.post(token_url, headers=headers2, data=data2)
                print(f"Response Status: {response.status_code}")
                print(f"Response Text: {response.text}")

                if response.status_code != 200:
                    print("❌ Both token request methods failed")
                    return
                else:
                    token_data = response.json()
                    token = token_data["access_token"]
                    print(f"✅ Got access token: {token[:20]}...")

        # Test fabric API auth (client credentials)
        print("\n🧪 Testing fabric API authentication...")
        auth_header = await auth_manager.get_auth_header("fabric")
        print(f"✅ Got auth header: {list(auth_header.keys())}")

        # Make a test request to verify the token works
        import httpx

        print("\n🧪 Testing API call with auth header...")
        async with httpx.AsyncClient() as client:
            # Test with the simpler connections search endpoint
            search_payload = {
                "filter": {
                    "and": [
                        {
                            "property": "/direction",
                            "operator": "=",
                            "values": ["OUTGOING"],
                        }
                    ]
                },
                "pagination": {"limit": 25, "offset": 0, "total": 0},
                "sort": [
                    {"property": "/changeLog/updatedDateTime", "direction": "DESC"}
                ],
            }

            response = await client.post(
                "https://api.equinix.com/fabric/v4/connections/search",
                headers=auth_header,
                json=search_payload,
            )
            print(f"Status: {response.status_code}")
            if response.status_code != 200:
                print(f"Response: {response.text}")
            else:
                print("✅ API call successful!")
                response_data = response.json()
                print(
                    f"Response data keys: {list(response_data.keys()) if isinstance(response_data, dict) else type(response_data)}"
                )
                if isinstance(response_data, dict) and "data" in response_data:
                    data = response_data["data"]
                    print(
                        f"Found {len(data) if isinstance(data, list) else 'N/A'} connections"
                    )

    except Exception as e:
        print(f"❌ Auth test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_auth())
