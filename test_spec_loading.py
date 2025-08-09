#!/usr/bin/env python3
"""Test to debug spec loading and schema detection."""

import asyncio
from pathlib import Path

import yaml

from src.equinix_mcp_server.config import Config
from src.equinix_mcp_server.spec_manager import SpecManager


async def test_spec_loading():
    """Test if individual specs are being loaded with schemas."""
    config = Config.load("config/apis.yaml")
    spec_manager = SpecManager(config)

    print("Updating specs...")
    await spec_manager.update_specs()

    print(f"\n=== LOADED SPECS ===")
    print(f"Number of specs loaded: {len(spec_manager.specs_cache)}")

    for api_name, spec in spec_manager.specs_cache.items():
        print(f"\n--- {api_name} ---")
        print(f"OpenAPI version: {spec.get('openapi', 'not found')}")

        # Check for schemas in different locations
        components = spec.get("components", {})
        schemas = components.get("schemas", {})
        defs = spec.get("$defs", {})
        definitions = spec.get("definitions", {})

        print(f"components.schemas: {len(schemas)} items")
        print(f"$defs: {len(defs)} items")
        print(f"definitions: {len(definitions)} items")

        # Show first few schema names
        all_schemas = {**schemas, **defs, **definitions}
        if all_schemas:
            schema_names = list(all_schemas.keys())[:5]
            print(f"Schema examples: {schema_names}")
            if len(all_schemas) > 5:
                print(f"... and {len(all_schemas) - 5} more")

        # Check for MetalMeta specifically
        if "MetalMeta" in all_schemas:
            print(f"✅ Found MetalMeta in {api_name}")

        # Show paths count
        paths = spec.get("paths", {})
        print(f"Paths: {len(paths)} endpoints")


if __name__ == "__main__":
    asyncio.run(test_spec_loading())
