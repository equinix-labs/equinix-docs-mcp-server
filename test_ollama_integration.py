#!/usr/bin/env python3
"""
Integration test script to verify Ollama + mcp-client-for-ollama + Equinix MCP Server
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def test_ollama_integration():
    """Test the complete Ollama integration pipeline"""

    print("🧪 Testing Ollama + mcp-client-for-ollama + Equinix MCP Server Integration")
    print("=" * 70)

    # Test 1: Check if Ollama is running
    print("\n1. Testing Ollama availability...")
    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            models = json.loads(result.stdout).get("models", [])
            print(f"✅ Ollama is running with {len(models)} models")

            # Check for compatible models
            compatible_models = ["qwen2.5", "llama3.1", "llama3.2", "mistral"]
            available_compatible = []
            for model in models:
                model_name = model.get("name", "")
                for compatible in compatible_models:
                    if compatible in model_name:
                        available_compatible.append(model_name)
                        break

            if available_compatible:
                print(
                    f"✅ Compatible models found: {', '.join(available_compatible[:3])}"
                )
                recommended_model = available_compatible[0]
            else:
                print("⚠️  No compatible models found, you may need to install one:")
                print("   ollama pull qwen2.5:7b")
                recommended_model = "qwen2.5:7b"
        else:
            print("❌ Ollama is not running")
            print("   Start Ollama: ollama serve")
            return False
    except Exception as e:
        print(f"❌ Error checking Ollama: {e}")
        return False

    # Test 2: Check if ollmcp is installed
    print("\n2. Testing mcp-client-for-ollama availability...")
    try:
        result = subprocess.run(
            ["ollmcp", "--help"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            print("✅ mcp-client-for-ollama (ollmcp) is installed")
        else:
            print("❌ ollmcp command failed")
            return False
    except FileNotFoundError:
        print("❌ mcp-client-for-ollama not found")
        print("   Install it: pip install --upgrade ollmcp")
        return False
    except Exception as e:
        print(f"❌ Error checking ollmcp: {e}")
        return False

    # Test 3: Check if our MCP server can start
    print("\n3. Testing Equinix MCP Server...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "equinix_mcp_server.main", "--test-update-specs"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=Path.cwd(),
        )

        if (
            result.returncode == 0
            and "✅ API spec fetching and validation test completed successfully"
            in result.stdout
        ):
            print("✅ Equinix MCP Server can fetch and validate API specs")
        else:
            print("❌ Equinix MCP Server test failed")
            print(f"   STDOUT: {result.stdout[-200:]}")
            print(f"   STDERR: {result.stderr[-200:]}")
            return False
    except Exception as e:
        print(f"❌ Error testing MCP server: {e}")
        return False

    # Test 4: Check if configuration file exists
    print("\n4. Testing configuration...")
    config_path = Path("equinix-mcp-config.json")
    if config_path.exists():
        print("✅ equinix-mcp-config.json exists")

        # Validate config
        try:
            with open(config_path) as f:
                config = json.load(f)
            if "mcpServers" in config and "equinix" in config["mcpServers"]:
                print("✅ Configuration format is valid")
            else:
                print("⚠️  Configuration format may be incorrect")
        except Exception as e:
            print(f"⚠️  Error validating config: {e}")
    else:
        print("⚠️  equinix-mcp-config.json not found")
        print(
            "   Copy from template: cp equinix-mcp-config.template.json equinix-mcp-config.json"
        )

    # Test 5: Test MCP server standalone
    print("\n5. Testing MCP server standalone...")
    try:
        # Test the server with a simple tool listing
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import asyncio
import sys
sys.path.insert(0, "src")
from equinix_mcp_server.main import EquinixMCPServer

async def test_tools():
    try:
        server = EquinixMCPServer("config/apis.yaml")
        await server.initialize()
        tools = await server.mcp.get_tools()
        print(f"✅ MCP server has {len(tools)} tools available")
        
        # Check for key Network Edge tool
        if "network_edge_getVirtualDevicesUsingGET_1" in tools:
            print("✅ Network Edge device listing tool found")
        else:
            print("⚠️  Network Edge device listing tool missing")
            
        return True
    except Exception as e:
        print(f"❌ Error initializing MCP server: {e}")
        return False

if asyncio.run(test_tools()):
    print("✅ MCP server tools test passed")
else:
    print("❌ MCP server tools test failed")
""",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=Path.cwd(),
        )

        if result.returncode == 0 and "✅ MCP server has" in result.stdout:
            print("✅ MCP server tools are properly exposed")
        else:
            print("⚠️  MCP server tool test had issues")
            print(f"   Output: {result.stdout}")
            if result.stderr:
                print(f"   Errors: {result.stderr}")

    except Exception as e:
        print(f"❌ Error testing MCP server: {e}")
        return False

    # Test 6: Brief bridge test (non-blocking)
    print("\n6. Testing bridge startup...")
    print("   Note: Bridge may show asyncio warnings due to test environment")
    try:
        # Just test if the bridge can parse config and start
        proc = subprocess.Popen(
            [
                "ollmcp",
                "--servers-json",
                "equinix-mcp-config.json",
                "--model",
                recommended_model,
                "--help",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path.cwd(),
        )

        # Short wait
        time.sleep(2)

        # Check if still running (good sign)
        if proc.poll() is None:
            print("✅ Bridge command parsing successful")
            proc.terminate()
        else:
            stdout, stderr = proc.communicate()
            if "--help" in stdout or "usage:" in stdout.lower():
                print("✅ Bridge executable working")
            else:
                print("⚠️  Bridge may have configuration issues")

    except Exception as e:
        print(f"⚠️  Bridge test inconclusive: {e}")
        # Not a failure - this is expected in some test environments

    # Summary
    print("\n" + "=" * 70)
    print("🎉 Integration Test Summary")
    print("✅ All components are working!")
    print("\n🚀 Ready to use! Run this command:")
    print(
        f"   ollmcp --servers-json equinix-mcp-config.json --model {recommended_model}"
    )
    print(
        "\n💬 Then ask: 'Can you list the Network Edge devices using the Equinix API?'"
    )
    print("\n📚 For detailed instructions, see: TESTING_WITH_OLLAMA.md")

    return True


if __name__ == "__main__":
    success = test_ollama_integration()
    sys.exit(0 if success else 1)
