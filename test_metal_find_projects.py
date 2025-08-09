#!/usr/bin/env python3
"""Test the specific metal_findProjects tool call.

Enhanced to emulate E2E validation:
- enumerate tools and locate the metal_findProjects tool name
- inspect tool.output_schema for $defs and MetalMeta
- call the tool directly (bypassing _mcp_call_tool wrapping) to get structured_content
- validate structured_content against tool.output_schema with jsonschema Draft 2020-12
"""

import asyncio
import json
from typing import Any

from jsonschema import Draft202012Validator

from src.equinix_mcp_server.main import EquinixMCPServer


async def test_metal_find_projects():
    """Test the equinix.metal_findProjects tool call with {} arguments."""
    server = EquinixMCPServer("config/apis.yaml")

    print("🔍 Testing equinix.metal_findProjects tool call")
    print("=" * 80)

    # Initialize the server
    await server.initialize()
    print("✅ Server initialized")

    mcp_server = server.mcp
    if not mcp_server:
        print("❌ MCP server is None")
        return

    # Discover the actual tool name
    tools = await mcp_server.get_tools()
    target_keys = [
        k for k in tools.keys() if "metal" in k and "find" in k and "project" in k
    ]
    print(f"🔎 Candidate tool keys: {target_keys[:5]}")
    tool_key = None
    for k in ["metal_findProjects", "equinix.metal_findProjects"] + target_keys:
        if k in tools:
            tool_key = k
            break

    if not tool_key:
        print("❌ Could not locate metal_findProjects tool in registry")
        return

    tool = tools[tool_key]
    output_schema: dict[str, Any] | None = getattr(tool, "output_schema", None)
    print(f"🧰 Using tool: {tool_key}")

    # Inspect output schema and $defs
    if isinstance(output_schema, dict):
        defs = output_schema.get("$defs") or {}
        print(
            f"📦 output_schema has $defs: {bool(defs)} (count={len(defs) if isinstance(defs, dict) else 0})"
        )
        if isinstance(defs, dict):
            print(f"🔗 MetalMeta in $defs: {'MetalMeta' in defs}")
        # Try to locate meta property ref
        meta_schema = None
        if output_schema.get("properties") and isinstance(
            output_schema["properties"], dict
        ):
            meta_schema = output_schema["properties"].get("meta")
        if isinstance(meta_schema, dict):
            print(f"➡️  meta $ref: {meta_schema.get('$ref')}")
    else:
        print("⚠️ Tool has no output_schema; validation will be skipped")

    print(f"\n🎯 Calling {tool_key} with {{}} arguments (direct run)...")
    try:
        # Run tool directly to get ToolResult with structured_content
        tr = await tool.run({})
        structured = getattr(tr, "structured_content", None)
        print("✅ Tool run successful!")
        print(f"Structured content type: {type(structured)}")

        # E2E-like validation: validate against output_schema using Draft 2020-12
        if isinstance(output_schema, dict) and structured is not None:
            try:
                Draft202012Validator(output_schema).validate(structured)
                print("✅ JSON Schema validation passed (Draft 2020-12)")
            except Exception as ve:
                print(f"❌ JSON Schema validation FAILED: {ve}")
                if "PointerToNowhere" in str(ve):
                    print(
                        "🔥 This is the $defs schema reference error (PointerToNowhere)!"
                    )
                elif "is not of type 'object'" in str(ve) and structured is None:
                    print(
                        "🔥 None is not of type 'object' — output was None but schema expects object"
                    )
                raise
        else:
            # Fallback: call via _mcp_call_tool and show response shape
            result = await mcp_server._mcp_call_tool(tool_key, {})
            print("ℹ️ Used _mcp_call_tool fallback; content blocks returned")
            if hasattr(result, "content") and result.content:
                try:
                    data = json.loads(result.content[0].text)
                    print(
                        f"Response type via MCP: {type(data)} keys={list(data) if isinstance(data, dict) else 'n/a'}"
                    )
                except Exception:
                    pass

    except Exception as e:
        print(f"❌ Tool call failed: {e}")
        print(f"Error type: {type(e)}")

        # Check if it's the schema error we were trying to fix
        if "PointerToNowhere" in str(e):
            print("🔥 This is the $defs schema reference error!")
        elif "$defs" in str(e):
            print("🔥 This is related to $defs schema processing!")
        else:
            print("This appears to be a different error.")


if __name__ == "__main__":
    asyncio.run(test_metal_find_projects())
