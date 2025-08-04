#!/usr/bin/env python3
"""
Simple test script to verify MCP server functionality
"""

import asyncio
import subprocess
import sys
import json
import time
from pathlib import Path

async def test_mcp_server():
    """Test the MCP server directly"""
    
    print("🔧 Testing Equinix MCP Server...")
    
    # Test 1: Can we start the server process?
    print("\n1. Testing server startup...")
    proc = None
    try:
        proc = subprocess.Popen([
            sys.executable, "-m", "equinix_mcp_server.main"
        ], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        cwd=Path.cwd()
        )
        
        # Give it a moment to start
        time.sleep(5)
        
        if proc.poll() is None:
            print("✅ Server process started successfully")
            proc.terminate()
            proc.wait(timeout=5)
        else:
            stdout, stderr = proc.communicate()
            print(f"❌ Server failed to start")
            print(f"STDOUT: {stdout.decode()}")
            print(f"STDERR: {stderr.decode()}")
            return False
            
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        if proc:
            proc.terminate()
        return False
    
    print("✅ MCP Server test completed successfully!")
    return True

if __name__ == "__main__":
    success = asyncio.run(test_mcp_server())
    sys.exit(0 if success else 1)
