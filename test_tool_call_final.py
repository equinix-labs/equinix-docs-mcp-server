#!/usr/bin/env python3
"""Final test to call equinix.metal_findProjects with experimental parser."""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))


async def call_metal_find_projects():
    """Call the actual equinix.metal_findProjects tool."""
    print("🚀 Final Test: equinix.metal_findProjects with {} arguments")
    print("=" * 70)

    # Enable experimental parser
    os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"
    print("✅ Experimental parser enabled")

    from equinix_mcp_server.main import EquinixMCPServer

    try:
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()

        print("✅ Server initialized with experimental parser!")
        mcp_server = server.mcp

        # Let's explore the experimental parser structure more thoroughly
        print("🔍 Exploring experimental parser structure...")

        # Check the underlying server type
        print(f"📄 MCP server type: {type(mcp_server)}")
        print(f"📄 MCP server module: {mcp_server.__class__.__module__}")

        # Look for the actual server instance
        server_attrs = [attr for attr in dir(mcp_server) if not attr.startswith("_")]
        print(f"📄 Available attributes: {server_attrs}")

        # Try to find the server that handles MCP requests
        if hasattr(mcp_server, "server"):
            underlying = mcp_server.server
            print(f"📄 Underlying server: {type(underlying)}")

            # Check for request handlers
            if hasattr(underlying, "request_handlers"):
                handlers = underlying.request_handlers
                print(f"📄 Request handlers: {list(handlers.keys())}")

                # Look for our call_tool handler
                for method, handler in handlers.items():
                    if "call" in method.lower() and "tool" in method.lower():
                        print(f"✅ Found tool handler: {method} -> {handler}")

                        # Try to create a proper request and call it
                        try:
                            from mcp.types import CallToolRequest, CallToolRequestParams

                            params = CallToolRequestParams(
                                name="equinix.metal_findProjects", arguments={}
                            )

                            request = CallToolRequest(
                                method="tools/call", params=params
                            )

                            print(
                                "🔄 Calling equinix.metal_findProjects with {} arguments..."
                            )
                            result = await handler(request)

                            print("✅ SUCCESS! Tool called successfully!")
                            print(f"📄 Result type: {type(result)}")

                            # Process the result
                            if hasattr(result, "content") and result.content:
                                print(
                                    f"📄 Response has {len(result.content)} content items"
                                )

                                for i, content_item in enumerate(result.content):
                                    print(f"\n📄 Content item {i+1}:")
                                    print(f"   Type: {type(content_item)}")

                                    if hasattr(content_item, "type"):
                                        print(f"   Content type: {content_item.type}")

                                    if hasattr(content_item, "text"):
                                        text = content_item.text
                                        print(f"   Text length: {len(text)} characters")

                                        # Try to parse as JSON
                                        if text.strip().startswith(
                                            "{"
                                        ) or text.strip().startswith("["):
                                            try:
                                                parsed = json.loads(text)
                                                print(f"   ✅ Valid JSON response!")

                                                if isinstance(parsed, dict):
                                                    print(
                                                        f"   📄 JSON keys: {list(parsed.keys())}"
                                                    )

                                                    # Look for project data
                                                    if "projects" in parsed:
                                                        projects = parsed["projects"]
                                                        print(
                                                            f"   📄 Found {len(projects)} projects"
                                                        )

                                                        if projects and isinstance(
                                                            projects, list
                                                        ):
                                                            first_project = projects[0]
                                                            if isinstance(
                                                                first_project, dict
                                                            ):
                                                                print(
                                                                    f"   📄 First project keys: {list(first_project.keys())}"
                                                                )
                                                                if (
                                                                    "name"
                                                                    in first_project
                                                                ):
                                                                    print(
                                                                        f"   📄 First project name: {first_project['name']}"
                                                                    )

                                                # Show truncated response
                                                json_str = json.dumps(parsed, indent=2)
                                                if len(json_str) > 2000:
                                                    print(
                                                        f"   📄 Response (first 2000 chars):\n{json_str[:2000]}..."
                                                    )
                                                else:
                                                    print(
                                                        f"   📄 Full response:\n{json_str}"
                                                    )

                                            except json.JSONDecodeError as e:
                                                print(f"   ❌ JSON parsing failed: {e}")
                                                if len(text) > 500:
                                                    print(
                                                        f"   📄 Raw text (first 500 chars): {text[:500]}..."
                                                    )
                                                else:
                                                    print(f"   📄 Raw text: {text}")
                                        else:
                                            # Not JSON, show raw text
                                            if len(text) > 1000:
                                                print(
                                                    f"   📄 Text (first 1000 chars): {text[:1000]}..."
                                                )
                                            else:
                                                print(f"   📄 Text: {text}")
                            else:
                                print(f"📄 Raw result: {result}")

                            return  # Success! Exit here

                        except Exception as e:
                            print(f"❌ Error calling tool handler: {e}")
                            print(f"📄 Error type: {type(e)}")
                            import traceback

                            traceback.print_exc()

                            # Check if this is the schema error we're debugging
                            error_str = str(e).lower()
                            if any(
                                keyword in error_str
                                for keyword in [
                                    "schema",
                                    "pointer",
                                    "validation",
                                    "$defs",
                                    "reference",
                                ]
                            ):
                                print("\n🎯 FOUND THE SCHEMA ISSUE!")
                                print("This is the error you're trying to debug!")
                                print(f"Error details: {e}")
                                return

        # If we get here, we couldn't find the handler
        print("❌ Could not find the tool call handler")
        print("🔍 Let's try a different approach...")

        # Alternative: Try to access tools through different paths
        possible_paths = [
            "tools",
            "_tools",
            "tool_registry",
            "_tool_registry",
            "handlers",
            "_handlers",
            "routes",
            "_routes",
        ]

        for path in possible_paths:
            if hasattr(mcp_server, path):
                attr = getattr(mcp_server, path)
                print(f"📄 Found {path}: {type(attr)}")

                # If it's dict-like, look for our tool
                if hasattr(attr, "get"):
                    tool = attr.get("equinix.metal_findProjects")
                    if tool:
                        print(f"✅ Found tool via {path}: {type(tool)}")
                        break

    except Exception as e:
        print(f"❌ Main error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(call_metal_find_projects())
