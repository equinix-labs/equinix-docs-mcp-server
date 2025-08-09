#!/usr/bin/env python3

import asyncio
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.config import Config
from equinix_mcp_server.spec_manager import SpecManager


async def main():
    """Test spec manager debug output."""
    config = Config.load("config/apis.yaml")
    spec_manager = SpecManager(config)

    print("🔄 Testing spec manager with debug output...")

    try:
        # Update specs (fetch and process)
        await spec_manager.update_specs()

        # Get merged spec
        merged_spec = await spec_manager.get_merged_spec()

        print("\n✅ Spec merging completed successfully!")
        print(f"Total paths: {len(merged_spec.get('paths', {}))}")
        print(f"Total $defs: {len(merged_spec.get('$defs', {}))}")
        print(f"Sample $defs keys: {list(merged_spec.get('$defs', {}).keys())[:10]}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
