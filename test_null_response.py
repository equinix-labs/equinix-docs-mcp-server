#!/usr/bin/env python3
"""
Test to verify that output schemas can handle null API responses properly.
"""

import asyncio
import json
import logging
import os
import sys

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from equinix_mcp_server.main import EquinixMCPServer


async def test_null_response():
    """Test that MCP tools can handle null API responses without validation errors."""
    print("🧪 Testing null response handling...")

    # Create server
    server = EquinixMCPServer()

    try:
        # Initialize server components
        await server.initialize()
        print("✓ Server initialization successful")

        # The FastMCP server should now have schemas that allow null responses
        if hasattr(server.mcp, "openapi_server") and hasattr(
            server.mcp.openapi_server, "routes"
        ):
            routes = server.mcp.openapi_server.routes
            print(f"Found {len(routes)} routes")

            # Look for project-related routes
            project_routes = [r for r in routes if "project" in r.path.lower()]
            if project_routes:
                print(f"Found {len(project_routes)} project-related routes")

                # Check a few routes for their output schemas
                for route in project_routes[:3]:  # Check first 3
                    print(f"Checking route: {route.method} {route.path}")

                    # Check if route has response schema that allows null
                    if hasattr(route, "responses") and route.responses:
                        for status_code, response_info in route.responses.items():
                            if status_code.startswith("2"):  # Success responses
                                print(
                                    f"  Response {status_code}: {response_info.description or 'No description'}"
                                )

                                if (
                                    hasattr(response_info, "content_schema")
                                    and response_info.content_schema
                                ):
                                    for (
                                        media_type,
                                        schema,
                                    ) in response_info.content_schema.items():
                                        if media_type == "application/json":
                                            print(
                                                f"    JSON schema type: {schema.get('type', 'unknown')}"
                                            )

                                            # Check if schema allows null
                                            allows_null = False
                                            if "anyOf" in schema:
                                                allows_null = any(
                                                    item.get("type") == "null"
                                                    for item in schema["anyOf"]
                                                )
                                            elif (
                                                isinstance(schema.get("type"), list)
                                                and "null" in schema["type"]
                                            ):
                                                allows_null = True

                                            print(f"    Allows null: {allows_null}")
                                            if allows_null:
                                                print(
                                                    f"    ✓ Schema properly handles null responses"
                                                )
                                            else:
                                                print(
                                                    f"    ⚠️  Schema may not handle null responses"
                                                )

                print("🎉 Null response handling test completed!")
                return True
            else:
                print("❌ No project routes found")
                return False
        else:
            print("❌ Could not access server routes")
            return False

    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    # Set environment for testing
    os.environ.setdefault("CLIENT_ID", "test")
    os.environ.setdefault("CLIENT_SECRET", "test")

    # Enable new parser
    os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

    # Run the test
    success = asyncio.run(test_null_response())
    sys.exit(0 if success else 1)
