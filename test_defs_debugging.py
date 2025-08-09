#!/usr/bin/env python3
"""Test to debug $defs placement in merged OpenAPI spec."""

import asyncio
from pathlib import Path

import yaml

from src.equinix_mcp_server.config import Config
from src.equinix_mcp_server.spec_manager import SpecManager


async def test_defs_placement():
    """Test where $defs are being placed in the merged spec."""
    config = Config.load("config/apis.yaml")
    spec_manager = SpecManager(config)

    print("Getting merged spec...")
    merged_spec = await spec_manager.get_merged_spec()

    print("\n=== MERGED SPEC STRUCTURE ===")
    print(f"Root keys: {list(merged_spec.keys())}")

    if "$defs" in merged_spec:
        print(f"\n$defs at root level: {len(merged_spec['$defs'])} schemas")
        for schema_name in list(merged_spec["$defs"].keys())[:5]:  # Show first 5
            print(f"  - {schema_name}")
        if len(merged_spec["$defs"]) > 5:
            print(f"  ... and {len(merged_spec['$defs']) - 5} more")
    else:
        print("\n❌ NO $defs at root level!")

    # Check if $defs appear anywhere else in the structure
    def find_defs_recursive(obj, path=""):
        """Recursively find all $defs keys."""
        results = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                if key == "$defs":
                    results.append(
                        (
                            new_path,
                            len(value) if isinstance(value, dict) else "not dict",
                        )
                    )
                if isinstance(value, (dict, list)):
                    results.extend(find_defs_recursive(value, new_path))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = f"{path}[{i}]"
                if isinstance(item, (dict, list)):
                    results.extend(find_defs_recursive(item, new_path))
        return results

    all_defs = find_defs_recursive(merged_spec)
    print(f"\n=== ALL $defs LOCATIONS ===")
    for path, count in all_defs:
        print(f"  {path}: {count} items")

    # Look for MetalMeta specifically
    def find_metal_meta(obj, path=""):
        """Find where MetalMeta is defined."""
        results = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                if key == "MetalMeta":
                    results.append(new_path)
                if isinstance(value, (dict, list)):
                    results.extend(find_metal_meta(value, new_path))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = f"{path}[{i}]"
                if isinstance(item, (dict, list)):
                    results.extend(find_metal_meta(item, new_path))
        return results

    metal_meta_locations = find_metal_meta(merged_spec)
    print(f"\n=== MetalMeta LOCATIONS ===")
    for location in metal_meta_locations:
        print(f"  {location}")

    # Check the actual YAML output
    print(f"\n=== CHECKING YAML FILE ===")
    yaml_path = Path(config.output.merged_spec_path)
    if yaml_path.exists():
        print(f"YAML file exists: {yaml_path}")
        print(f"File size: {yaml_path.stat().st_size} bytes")

        # Read first 100 lines to see structure
        with open(yaml_path, "r") as f:
            lines = f.readlines()

        print(f"\nFirst 20 lines of YAML:")
        for i, line in enumerate(lines[:20]):
            print(f"{i+1:3d}: {line.rstrip()}")

        # Look for $defs in the file
        defs_lines = []
        for i, line in enumerate(lines):
            if "$defs:" in line:
                defs_lines.append((i + 1, line.strip()))

        print(f"\n$defs occurrences in YAML file:")
        for line_num, line_content in defs_lines:
            print(f"  Line {line_num}: {line_content}")

            # Show context around each $defs
            start = max(0, line_num - 3)
            end = min(len(lines), line_num + 3)
            print(f"    Context:")
            for j in range(start, end):
                marker = ">>>" if j == line_num - 1 else "   "
                print(f"    {marker} {j+1:3d}: {lines[j].rstrip()}")
            print()
    else:
        print(f"❌ YAML file not found: {yaml_path}")


if __name__ == "__main__":
    asyncio.run(test_defs_placement())
