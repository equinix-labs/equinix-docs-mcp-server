"""OpenAPI overlay management functionality."""

from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import yaml


class OverlayManager:
    """Manages OpenAPI overlay loading, creation, and application."""

    def __init__(self) -> None:
        """Initialize the overlay manager."""
        self.overlays_cache: Dict[str, Dict[str, Any]] = {}

    async def load_overlay(self, overlay_path: str) -> Optional[Dict[str, Any]]:
        """Load overlay file from disk.

        Args:
            overlay_path: Path to the overlay file

        Returns:
            Loaded overlay dictionary or None if file doesn't exist
        """
        path = Path(overlay_path)
        if not path.exists():
            return None

        async with aiofiles.open(path, "r") as f:
            content = await f.read()
            overlay = yaml.safe_load(content)

        # Cache the overlay
        cache_key = str(path)
        self.overlays_cache[cache_key] = overlay
        return overlay

    async def create_overlay_template(
        self, overlay_path: str, service_name: str, api_name: str
    ) -> None:
        """Create a basic overlay template for an API.

        Args:
            overlay_path: Path where the overlay file should be created
            service_name: Name of the service (e.g., "metal", "fabric")
            api_name: API identifier for special handling
        """
        path = Path(overlay_path)

        overlay = {
            "overlay": "1.0.0",
            "info": {
                "title": f"Overlay for {service_name}",
                "version": "1.0.0",
                "description": f"Overlay spec to normalize {service_name} API",
            },
            "actions": [
                {
                    "target": "$.info.title",
                    "update": f"Equinix {service_name.title()} API",
                },
                {
                    "target": "$.servers",
                    "update": [
                        {
                            "url": "https://api.equinix.com",
                            "description": "Equinix API Server",
                        }
                    ],
                },
            ],
        }

        # Handle Metal API special case
        if api_name == "metal":
            # Ensure actions is a list and extend it
            actions_list = overlay["actions"]
            if isinstance(actions_list, list):
                actions_list.extend(
                    [{"target": "$.paths.*", "update": {"path_prefix": "/metal/v1"}}]
                )

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(path, "w") as f:
            await f.write(yaml.dump(overlay, default_flow_style=False))

    async def apply_overlay(
        self, spec: Dict[str, Any], overlay: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply overlay transformations to a spec.

        Args:
            spec: The OpenAPI specification to modify
            overlay: The overlay definition containing transformation actions

        Returns:
            Modified specification
        """
        # This is a simplified overlay implementation
        # In a full implementation, you'd use a proper overlay engine

        actions = overlay.get("actions", [])
        modified_spec = spec.copy()

        for action in actions:
            target = action.get("target")
            update = action.get("update")

            # Simple path-based updates
            if target == "$.info.title" and "info" in modified_spec:
                modified_spec["info"]["title"] = update
            elif target == "$.servers":
                modified_spec["servers"] = update

        return modified_spec

    def get_cached_overlay(self, overlay_path: str) -> Optional[Dict[str, Any]]:
        """Get a cached overlay by path.

        Args:
            overlay_path: Path to the overlay file

        Returns:
            Cached overlay or None if not found
        """
        return self.overlays_cache.get(overlay_path)

    def clear_cache(self) -> None:
        """Clear the overlay cache."""
        self.overlays_cache.clear()
