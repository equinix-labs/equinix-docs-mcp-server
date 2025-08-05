#!/usr/bin/env python3
"""
Simple bridge connection test for mcp-client-for-ollama + Equinix MCP Server
Run this in a separate terminal to test the full integration.
"""

import subprocess
import sys
import time
from pathlib import Path


def test_bridge_connection():
    """Test the bridge connection with proper asyncio handling"""

    print("🌉 Testing mcp-client-for-ollama bridge connection...")
    print("This will start the bridge - press Ctrl+C to stop when done testing")
    print("=" * 60)

    # Check for config file
    config_path = Path("equinix-mcp-config.json")
    if not config_path.exists():
        print("❌ equinix-mcp-config.json not found")
        print(
            "   Copy from template: cp equinix-mcp-config.template.json equinix-mcp-config.json"
        )
        return False

    # Get recommended model
    try:
        import json

        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            models = json.loads(result.stdout).get("models", [])
            if models:
                model_name = models[0]["name"]
                print(f"Using model: {model_name}")
            else:
                model_name = "qwen2.5:7b"
                print(f"No models found, using default: {model_name}")
        else:
            model_name = "qwen2.5:7b"
            print(f"Cannot check models, using default: {model_name}")
    except:
        model_name = "qwen2.5:7b"
        print(f"Using default model: {model_name}")

    print("\nStarting bridge (may take a few seconds to initialize)...")
    print("Look for messages like:")
    print("  - 'Found server in config: equinix'")
    print("  - 'Connected to MCP server successfully'")
    print("  - 'Created FastMCP OpenAPI server with 441 routes'")
    print("\nThen try asking: 'Can you list the Network Edge devices?'")
    print("-" * 60)

    try:
        # Start the bridge in interactive mode
        subprocess.run(
            [
                "ollmcp",
                "--servers-json",
                "equinix-mcp-config.json",
                "--model",
                model_name,
            ],
            cwd=Path.cwd(),
        )

    except KeyboardInterrupt:
        print("\n\n✅ Bridge test completed (stopped by user)")
        return True
    except Exception as e:
        print(f"\n❌ Error running bridge: {e}")
        return False


if __name__ == "__main__":
    success = test_bridge_connection()
    if success:
        print("\n📚 For more testing instructions, see: TESTING_WITH_OLLAMA.md")
    sys.exit(0 if success else 1)
