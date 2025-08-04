#!/usr/bin/env python3
"""
Test script to demonstrate the Equinix MCP Server with a specific focus on Network Edge device listing.
This script shows the exact tools available and what a client would see.
"""

import asyncio
import json
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.main import EquinixMCPServer

async def demonstrate_network_edge_tools():
    """Demonstrate the Network Edge tools available through MCP"""
    
    print("🔧 Initializing Equinix MCP Server...")
    
    # Initialize the server
    server = EquinixMCPServer("config/apis.yaml")
    await server.initialize()
    
    print("✅ Server initialized successfully!")
    
    # Get the merged spec to see what tools are available
    merged_spec = await server.spec_manager.get_merged_spec()
    
    print("\n🔍 Looking for Network Edge API operations...")
    
    # Find Network Edge operations
    network_edge_ops = []
    
    for path, methods in merged_spec.get("paths", {}).items():
        if "/network-edge/" in path:
            for method, operation in methods.items():
                if isinstance(operation, dict):
                    op_id = operation.get("operationId", "")
                    summary = operation.get("summary", "")
                    description = operation.get("description", "")
                    
                    network_edge_ops.append({
                        "method": method.upper(),
                        "path": path,
                        "operationId": op_id,
                        "summary": summary,
                        "description": description
                    })
    
    print(f"\n📋 Found {len(network_edge_ops)} Network Edge operations:")
    
    # Show the most relevant operations for device listing
    device_listing_ops = [op for op in network_edge_ops if 
                         "get" in op["method"].lower() and 
                         ("ves" in op["path"] or "device" in op["path"].lower())]
    
    if device_listing_ops:
        print("\n🎯 Device Listing Operations (what you'd ask Ollama to use):")
        for op in device_listing_ops[:5]:  # Show first 5
            print(f"   • {op['method']} {op['path']}")
            print(f"     Operation ID: {op['operationId']}")
            print(f"     Summary: {op['summary']}")
            print()
    
    # Show what a typical MCP query would look like
    print("💬 Example prompt for Ollama/Claude:")
    print('   "Can you list the Network Edge devices using the Equinix API?"')
    print("\n🔧 What should happen:")
    print("   1. MCP client identifies relevant tool (e.g., 'network-edge_getNetworkEdgeV1Ves')")
    print("   2. Calls the tool with appropriate parameters")
    print("   3. Gets authentication error (without credentials) or device list (with credentials)")
    
    # Show authentication requirements
    print("\n🔐 Authentication Setup (to avoid 401 errors):")
    print("   export EQUINIX_CLIENT_ID='your_client_id'")
    print("   export EQUINIX_CLIENT_SECRET='your_client_secret'")
    
    print("\n✅ Demo completed! The MCP server is ready for Ollama integration.")
    
    return network_edge_ops

if __name__ == "__main__":
    ops = asyncio.run(demonstrate_network_edge_tools())
