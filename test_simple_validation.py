#!/usr/bin/env python3
"""
Simple test to verify schema references are working correctly.
"""

import asyncio
import logging
import os
from pathlib import Path

from fastmcp import FastMCP

# Set up logging
logging.basicConfig(level=logging.INFO)

# Use test environment variables
os.environ["EQUINIX_CLIENT_ID"] = "test"
os.environ["EQUINIX_CLIENT_SECRET"] = "test"


async def test_schema_validation():
    """Test that FastMCP can parse schemas without reference errors."""

    print("🧪 Testing schema validation...")

    # Load the merged spec
    spec_path = "/Users/mjohansson/dev/equinix-mcp-server/merged-openapi.yaml"

    # Test with new parser
    os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

    try:
        app = FastMCP("Test", openapi_yaml_path=spec_path)
        print("✅ FastMCP loaded spec successfully with new parser")

        # Check if we can get tools
        tools = app.list_tools()
        print(f"✅ Found {len(tools)} tools")

        # Look for metal project tools specifically
        metal_project_tools = [
            t
            for t in tools
            if "metal" in t.name.lower() and "project" in t.name.lower()
        ]
        print(f"✅ Found {len(metal_project_tools)} metal project tools")

        if metal_project_tools:
            # Get the first tool and check its schema
            tool = metal_project_tools[0]
            print(f"✅ Tool '{tool.name}' has schema: {bool(tool.parameters)}")

        print("🎉 All schema validation tests passed!")
        return True

    except Exception as e:
        if "PointerToNowhere" in str(e):
            print(f"❌ Schema reference error still exists: {e}")
            return False
        else:
            print(f"❌ Other error: {e}")
            return False


if __name__ == "__main__":
    asyncio.run(test_schema_validation())
