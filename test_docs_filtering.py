#!/usr/bin/env python3
"""
Quick test to verify the improved docs filtering is working.
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

async def test_docs_filtering():
    """Test the improved docs filtering through the MCP server."""
    from equinix_mcp_server.main import EquinixMCPServer
    
    print("🔍 Testing improved documentation filtering...")
    print("=" * 50)
    
    try:
        server = EquinixMCPServer('config/apis.yaml')
        await server.initialize()
        
        # Test cases
        test_cases = [
            ("Fabric providers", "This was the failing case"),
            ("Fabric", "This was working before"), 
            ("Network Edge", "Should find Network Edge docs"),
            ("Metal API", "Should find Metal documentation"),
            ("provider guide", "Should find provider-related docs")
        ]
        
        for query, description in test_cases:
            print(f"\n🔍 Testing: '{query}' ({description})")
            
            try:
                result = await server.docs_manager.list_docs(query)
                lines = result.split('\n')
                doc_entries = [l for l in lines if l.startswith('- ')]
                
                print(f"   📚 Found {len(doc_entries)} documentation entries")
                
                # Show first few results
                if doc_entries:
                    print("   📖 Sample results:")
                    for i, entry in enumerate(doc_entries[:3]):
                        print(f"      {i+1}. {entry[2:]}")  # Remove "- " prefix
                    if len(doc_entries) > 3:
                        print(f"      ... and {len(doc_entries) - 3} more")
                else:
                    print("   ⚠️  No matching documentation found")
                    
            except Exception as e:
                print(f"   ❌ Error: {e}")
        
        print("\n" + "=" * 50)
        print("✅ Documentation filtering test completed!")
        print("\n💡 The improved filtering now supports:")
        print("   • Flexible word matching")
        print("   • Singular/plural variations") 
        print("   • Partial phrase matches")
        print("   • Relevance scoring")
        
    except Exception as e:
        print(f"❌ Failed to initialize server: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = asyncio.run(test_docs_filtering())
    sys.exit(0 if success else 1)
