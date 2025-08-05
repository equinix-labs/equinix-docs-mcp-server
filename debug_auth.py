#!/usr/bin/env python3
"""Debug script to test authentication flow."""

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


async def test_auth():
    """Test the authentication flow."""
    print("=" * 60)
    print("DEBUGGING EQUINIX AUTHENTICATION")
    print("=" * 60)

    # Check environment variables
    print("\n1. CHECKING ENVIRONMENT VARIABLES:")
    client_id = os.getenv("EQUINIX_CLIENT_ID")
    client_secret = os.getenv("EQUINIX_CLIENT_SECRET")
    metal_token = os.getenv("EQUINIX_METAL_TOKEN")

    print(f"   EQUINIX_CLIENT_ID: {'✓ Set' if client_id else '✗ Missing'}")
    if client_id:
        print(f"   CLIENT_ID length: {len(client_id)}")
        print(f"   CLIENT_ID starts with: {client_id[:8]}...")

    print(f"   EQUINIX_CLIENT_SECRET: {'✓ Set' if client_secret else '✗ Missing'}")
    if client_secret:
        print(f"   CLIENT_SECRET length: {len(client_secret)}")
        print(f"   CLIENT_SECRET starts with: {client_secret[:8]}...")

    print(f"   EQUINIX_METAL_TOKEN: {'✓ Set' if metal_token else '✗ Missing'}")

    if not client_id or not client_secret:
        print("\n❌ Missing required credentials!")
        return

    # Load config
    print("\n2. LOADING CONFIGURATION:")
    try:
        config = Config.load("config/apis.yaml")
        print("   ✓ Configuration loaded successfully")
    except Exception as e:
        print(f"   ✗ Configuration error: {e}")
        return

    # Initialize auth manager
    print("\n3. INITIALIZING AUTH MANAGER:")
    try:
        auth_manager = AuthManager(config)
        print("   ✓ Auth manager initialized")
    except Exception as e:
        print(f"   ✗ Auth manager error: {e}")
        return

    # Test token acquisition for fabric service
    print("\n4. TESTING FABRIC SERVICE AUTHENTICATION:")
    try:
        print("   Requesting auth header for fabric service...")
        auth_header = await auth_manager.get_auth_header("fabric")
        print(f"   ✓ Auth header obtained: {list(auth_header.keys())}")

        # Show header info without exposing token
        for key, value in auth_header.items():
            if key == "Authorization" and value.startswith("Bearer "):
                token = value[7:]  # Remove "Bearer "
                print(f"   Token length: {len(token)}")
                print(f"   Token starts with: {token[:20]}...")
                print(f"   Token ends with: ...{token[-20:]}")
            else:
                print(f"   {key}: {value}")

    except Exception as e:
        print(f"   ✗ Auth error: {e}")
        import traceback

        traceback.print_exc()
        return

    # Test direct API call
    print("\n5. TESTING DIRECT API CALL:")
    try:
        import httpx

        url = "https://api.equinix.com/fabric/v4/connections"
        headers = {"Content-Type": "application/json", **auth_header}

        print(f"   Making request to: {url}")
        print(f"   Headers: {list(headers.keys())}")

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            print(f"   Response status: {response.status_code}")
            print(f"   Response headers: {dict(response.headers)}")

            if response.status_code == 200:
                print("   ✓ API call successful!")
                data = response.json()
                print(
                    f"   Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}"
                )
            else:
                print(f"   ✗ API call failed: {response.status_code}")
                print(f"   Error response: {response.text}")

    except Exception as e:
        print(f"   ✗ API call error: {e}")
        import traceback

        traceback.print_exc()

    print("\n" + "=" * 60)
    print("DEBUG COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_auth())
