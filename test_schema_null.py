#!/usr/bin/env python3
"""
Test to directly check output schema generation with the new parser.
"""

import asyncio
import json
import os
import sys

# Set environment for testing
os.environ.setdefault("CLIENT_ID", "test")
os.environ.setdefault("CLIENT_SECRET", "test")
os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

# Import after setting environment
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), ".venv", "lib", "python3.13", "site-packages"
    ),
)

from fastmcp.utilities.openapi import ResponseInfo, extract_output_schema_from_responses


def test_output_schema_null_handling():
    """Test that output schemas properly handle null responses."""
    print("🧪 Testing output schema null handling...")

    # Create mock response info that represents an object schema
    mock_schema = {
        "type": "object",
        "properties": {
            "meta": {"type": "object"},
            "projects": {"type": "array", "items": {"type": "object"}},
        },
    }

    response_info = ResponseInfo(
        description="OK", content_schema={"application/json": mock_schema}
    )

    responses = {"200": response_info}

    # Test with OpenAPI 3.0 (which should add null handling)
    output_schema = extract_output_schema_from_responses(
        responses=responses, schema_definitions={}, openapi_version="3.0.3"
    )

    print("Generated output schema:")
    print(json.dumps(output_schema, indent=2))

    # Check if the schema allows null responses
    if output_schema and "anyOf" in output_schema:
        has_null = any(item.get("type") == "null" for item in output_schema["anyOf"])
        print(f"✓ Schema uses anyOf pattern: {has_null}")

        if has_null:
            print("✓ Schema properly allows null responses")
            return True
        else:
            print("❌ Schema does not allow null responses")
            return False
    else:
        print("❌ Schema does not use anyOf pattern")
        return False


if __name__ == "__main__":
    success = test_output_schema_null_handling()
    print(f"\n🎉 Test {'passed' if success else 'failed'}!")
    sys.exit(0 if success else 1)
