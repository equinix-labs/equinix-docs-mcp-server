#!/usr/bin/env python3
"""
Simple test to verify MetalHref schema references are resolved.
"""

import json

import yaml


def test_metal_href_references():
    """Test that MetalHref schema references are properly resolved in merged spec."""

    print("🧪 Testing MetalHref schema references in merged spec...")

    # Load the merged spec
    spec_path = "/Users/mjohansson/dev/equinix-mcp-server/merged-openapi.yaml"

    with open(spec_path, "r") as f:
        spec = yaml.safe_load(f)

    # Check if MetalHref schema exists in $defs
    if "$defs" not in spec:
        print("❌ No $defs section found")
        return False

    if "MetalHref" not in spec["$defs"]:
        print("❌ MetalHref not found in $defs")
        return False

    print("✅ MetalHref schema found in $defs")

    # Check the MetalHref schema structure
    metal_href = spec["$defs"]["MetalHref"]
    print(f"✅ MetalHref type: {metal_href.get('type', 'unknown')}")

    # Look for schemas that reference MetalHref
    refs_to_metal_href = []

    def find_metal_href_refs(obj, path=""):
        """Recursively find references to MetalHref."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                if key == "$ref" and isinstance(value, str) and "MetalHref" in value:
                    refs_to_metal_href.append((current_path, value))
                else:
                    find_metal_href_refs(value, current_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                find_metal_href_refs(item, f"{path}[{i}]")

    # Search for MetalHref references
    find_metal_href_refs(spec)

    print(f"✅ Found {len(refs_to_metal_href)} references to MetalHref:")
    for path, ref in refs_to_metal_href[:5]:  # Show first 5
        print(f"  - {path}: {ref}")

    # Check that references use the correct format in $defs section
    defs_section = spec.get("$defs", {})
    incorrect_refs = []

    def check_defs_refs(obj, path="$defs"):
        """Check that all refs in $defs section use #/$defs/ format."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                if key == "$ref" and isinstance(value, str):
                    if value.startswith("#/components/schemas/"):
                        incorrect_refs.append((current_path, value))
                else:
                    check_defs_refs(value, current_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                check_defs_refs(item, f"{path}[{i}]")

    check_defs_refs(defs_section)

    if incorrect_refs:
        print(f"❌ Found {len(incorrect_refs)} incorrect refs in $defs section:")
        for path, ref in incorrect_refs[:3]:
            print(f"  - {path}: {ref}")
        return False
    else:
        print("✅ All references in $defs section use correct #/$defs/ format")

    # Check a specific metal project schema that should reference MetalHref
    if "MetalProject" in spec.get("$defs", {}):
        metal_project = spec["$defs"]["MetalProject"]
        print("✅ Found MetalProject schema")

        # Convert to string to search for references
        project_str = json.dumps(metal_project)
        if "MetalHref" in project_str:
            print("✅ MetalProject contains MetalHref references")
        else:
            print("⚠️  MetalProject doesn't contain MetalHref references")

    print("🎉 MetalHref schema validation passed!")
    return True


if __name__ == "__main__":
    test_metal_href_references()
