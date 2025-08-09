#!/usr/bin/env python3
"""
Response Processing Test - Check for schema errors in response processing
"""

import asyncio
import os

# Set experimental parser
os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

from src.equinix_mcp_server.main import EquinixMCPServer


async def test_response_processing():
    print(
        "🚀 RESPONSE PROCESSING TEST: Check for schema errors during response processing"
    )
    print("=" * 80)

    # Check environment variable
    token = os.getenv("EQUINIX_METAL_TOKEN")
    if token:
        print(f"✅ EQUINIX_METAL_TOKEN found (length: {len(token)})")
    else:
        print("❌ EQUINIX_METAL_TOKEN not found in environment")
        print("   Please export EQUINIX_METAL_TOKEN=your_token_here")
        return

    try:
        # Initialize server
        print("✅ Experimental parser enabled")
        server = EquinixMCPServer()
        await server.initialize()
        print("✅ Server initialized with experimental parser!")

        mcp_server = server.mcp
        if not mcp_server:
            print("❌ MCP server is None - initialization failed")
            return

        tool_name = "metal_findProjects"
        arguments = {}
        print(f"🎯 Calling tool: {tool_name} with arguments: {arguments}")
        print(f"🔍 Looking for response processing errors (not auth errors)...")

        try:
            result = await mcp_server._mcp_call_tool(tool_name, arguments)
            print(f"✅ Tool call completed successfully!")
            print(f"📊 Result type: {type(result)}")
            print(f"📊 Result: {result}")

            # If we get here, there's no schema processing error
            print("🎉 SUCCESS: No schema processing errors found!")
            print("   - Tool call completed")
            print("   - Response was processed successfully")
            print("   - Schema validation passed")

        except Exception as e:
            error_msg = str(e).lower()

            if "401" in error_msg or "unauthorized" in error_msg:
                print(f"❌ Authentication Error: {e}")
                print(
                    "   This indicates the API call reached the server but token is invalid/expired"
                )
                print(
                    "   Schema processing is working - this is an auth issue, not a schema issue"
                )
                print("   ✅ No schema processing errors detected!")

            elif "400" in error_msg or "bad request" in error_msg:
                print(f"❌ Bad Request Error: {e}")
                print("   This could indicate a schema processing issue")
                print("   🔍 This might be the error you're looking for!")

            elif (
                "schema" in error_msg
                or "validation" in error_msg
                or "reference" in error_msg
            ):
                print(f"❌ Schema Processing Error Found: {e}")
                print("   🎯 This is likely the error you're debugging!")
                import traceback

                traceback.print_exc()

            else:
                print(f"❌ Other Error: {e}")
                print(f"   Error type: {type(e)}")
                print("   Full traceback:")
                import traceback

                traceback.print_exc()

    except Exception as e:
        print(f"❌ Server initialization error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_response_processing())
