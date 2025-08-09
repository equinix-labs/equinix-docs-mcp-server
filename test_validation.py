#!/usr/bin/env python3
"""
Test to simulate JSON schema validation with null values.
"""

import json
import os
import sys

# Set environment for testing
os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

try:
    from jsonschema import ValidationError, validate

    has_jsonschema = True
except ImportError:
    print("⚠️  jsonschema not available, skipping validation test")
    has_jsonschema = False


def test_schema_validation():
    """Test that our schemas can validate null responses."""
    if not has_jsonschema:
        return True

    print("🧪 Testing schema validation with null responses...")

    # Old schema (would fail with null)
    old_schema = {
        "type": "object",
        "properties": {
            "meta": {"type": "object"},
            "projects": {"type": "array", "items": {"type": "object"}},
        },
    }

    # New schema (allows null)
    new_schema = {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "meta": {"type": "object"},
                    "projects": {"type": "array", "items": {"type": "object"}},
                },
            },
            {"type": "null"},
        ]
    }

    # Test data
    null_response = None
    valid_response = {"meta": {}, "projects": []}

    print("Testing old schema with null response...")
    try:
        validate(null_response, old_schema)
        print("❌ Old schema unexpectedly accepted null")
        return False
    except ValidationError as e:
        print(f"✓ Old schema correctly rejected null: {e.message}")

    print("\nTesting new schema with null response...")
    try:
        validate(null_response, new_schema)
        print("✓ New schema correctly accepts null")
    except ValidationError as e:
        print(f"❌ New schema rejected null: {e.message}")
        return False

    print("\nTesting new schema with valid response...")
    try:
        validate(valid_response, new_schema)
        print("✓ New schema correctly accepts valid object")
    except ValidationError as e:
        print(f"❌ New schema rejected valid object: {e.message}")
        return False

    print("\n🎉 All validation tests passed!")
    return True


if __name__ == "__main__":
    success = test_schema_validation()
    sys.exit(0 if success else 1)
