#!/usr/bin/env python3
"""
Example script demonstrating Equinix MCP Server functionality.

This script shows how to:
1. Load configuration
2. Fetch API specs
3. Apply overlays
4. Generate merged specification
5. Update documentation cache
"""

import asyncio
import json
import os
from pathlib import Path

from equinix_mcp_server.config import Config
from equinix_mcp_server.spec_manager import SpecManager
from equinix_mcp_server.docs import DocsManager


async def main():
    """Run the example."""
    print("🚀 Equinix MCP Server Example")
    print("=" * 40)
    
    # Load configuration
    print("\n📋 Loading configuration...")
    config = Config.load("config/apis.yaml")
    print(f"✅ Loaded configuration for {len(config.apis)} APIs:")
    for name, api_config in config.apis.items():
        print(f"   - {name}: {api_config.service_name} {api_config.version}")
    
    # Initialize managers
    spec_manager = SpecManager(config)
    docs_manager = DocsManager(config)
    
    # Demonstrate spec fetching (with mock to avoid hitting real APIs)
    print("\n🔗 API Specification Management:")
    print("   Note: In production, this would fetch real API specs")
    print("   from Equinix API catalog URLs")
    
    # Show overlay processing
    print("\n🔧 Overlay Processing:")
    for api_name in config.get_api_names():
        api_config = config.get_api_config(api_name)
        overlay_path = Path(api_config.overlay)
        if overlay_path.exists():
            print(f"   ✅ {api_name}: {overlay_path}")
        else:
            print(f"   ❌ {api_name}: {overlay_path} (missing)")
    
    # Show path normalization
    print("\n🛣️  Path Normalization Examples:")
    test_paths = [
        ("metal", "/devices", "/metal/v1/devices"),
        ("fabric", "/connections", "/fabric/v4/connections"),
        ("billing", "/invoices", "/billing/v2/invoices"),
        ("network-edge", "/devices", "/network-edge/v1/devices"),
    ]
    
    for api_name, input_path, expected in test_paths:
        normalized = await spec_manager._normalize_path(api_name, input_path)
        status = "✅" if normalized == expected else "❌"
        print(f"   {status} {api_name}: {input_path} → {normalized}")
    
    # Demonstrate documentation features
    print("\n📚 Documentation Management:")
    
    # Create some sample documentation data for demo
    docs_manager.sitemap_cache = [
        {
            "url": "https://docs.equinix.com/metal/getting-started",
            "title": "Getting Started with Metal",
            "category": "Metal",
            "lastmod": "2023-12-01"
        },
        {
            "url": "https://docs.equinix.com/fabric/overview",
            "title": "Fabric Overview",
            "category": "Fabric", 
            "lastmod": "2023-12-02"
        },
        {
            "url": "https://docs.equinix.com/api-catalog/metalv1",
            "title": "Metal API Reference",
            "category": "API",
            "lastmod": "2023-12-03"
        }
    ]
    
    print("   Sample documentation entries loaded for demo")
    
    # Test documentation search
    search_result = await docs_manager.search_docs("metal")
    print(f"   🔍 Search for 'metal' found {search_result.count('**')} results")
    
    # Test documentation listing with filter
    filtered_docs = await docs_manager.list_docs("API")
    print(f"   📋 Filtered docs for 'API' category")
    
    # Authentication examples
    print("\n🔐 Authentication Configuration:")
    auth_config = config.auth
    
    print(f"   OAuth2 Token URL: {auth_config.client_credentials.get('token_url', 'Not configured')}")
    print(f"   Metal Token Header: {auth_config.metal_token.get('header_name', 'Not configured')}")
    
    # Environment variable checks
    env_vars = ["EQUINIX_CLIENT_ID", "EQUINIX_CLIENT_SECRET", "EQUINIX_METAL_TOKEN"]
    print("\n🌍 Environment Variables:")
    for var in env_vars:
        value = os.getenv(var)
        status = "✅ Set" if value else "❌ Not set"
        print(f"   {status} {var}")
    
    # Show generated file structure
    print("\n📁 Generated Files Structure:")
    example_files = [
        "cache/specs/metal.yaml",
        "cache/specs/fabric.yaml", 
        "merged-openapi.yaml",
        "docs/sitemap_cache.xml"
    ]
    
    for file_path in example_files:
        path = Path(file_path)
        if path.exists():
            print(f"   ✅ {file_path}")
        else:
            print(f"   ⏳ {file_path} (would be generated)")
    
    print("\n🎉 Example completed!")
    print("\nTo try the real functionality:")
    print("1. Set your API credentials in environment variables")
    print("2. Run: equinix-mcp-server --update-specs")
    print("3. Run: equinix-mcp-server")


if __name__ == "__main__":
    asyncio.run(main())