"""OpenAPI spec management and merging functionality."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import httpx
import yaml
from openapi_spec_validator import validate_spec

from .config import Config


class SpecManager:
    """Manages OpenAPI spec fetching, overlaying, and merging."""

    def __init__(self, config: Config):
        """Initialize with configuration."""
        self.config = config
        self.specs_cache: Dict[str, Dict[str, Any]] = {}
        self.overlays_cache: Dict[str, Dict[str, Any]] = {}

    async def update_specs(self) -> None:
        """Update all API specs from their sources."""
        for api_name in self.config.get_api_names():
            await self._fetch_spec(api_name)
            await self._load_overlay(api_name)

    async def _fetch_spec(self, api_name: str) -> Dict[str, Any]:
        """Fetch an OpenAPI spec from its URL."""
        api_config = self.config.get_api_config(api_name)
        if not api_config:
            raise ValueError(f"Unknown API: {api_name}")

        async with httpx.AsyncClient() as client:
            response = await client.get(api_config.url)
            response.raise_for_status()

            # Parse YAML content
            spec = yaml.safe_load(response.text)

            # Validate the spec
            try:
                validate_spec(spec)
            except Exception as e:
                print(f"Warning: Spec validation failed for {api_name}: {e}")

            # Cache the spec
            self.specs_cache[api_name] = spec

            # Save to local file for debugging
            cache_dir = Path("cache/specs")
            cache_dir.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(cache_dir / f"{api_name}.yaml", "w") as f:
                await f.write(yaml.dump(spec, default_flow_style=False))

            return spec

    async def _load_overlay(self, api_name: str) -> Optional[Dict[str, Any]]:
        """Load overlay file for an API."""
        api_config = self.config.get_api_config(api_name)
        if not api_config:
            return None

        overlay_path = Path(api_config.overlay)
        if not overlay_path.exists():
            # Create a basic overlay template
            await self._create_overlay_template(api_name, overlay_path)
            return None

        async with aiofiles.open(overlay_path, "r") as f:
            content = await f.read()
            overlay = yaml.safe_load(content)

        self.overlays_cache[api_name] = overlay
        return overlay

    async def _create_overlay_template(self, api_name: str, overlay_path: Path) -> None:
        """Create a basic overlay template for an API."""
        api_config = self.config.get_api_config(api_name)
        if not api_config:
            return

        overlay = {
            "overlay": "1.0.0",
            "info": {
                "title": f"Overlay for {api_config.service_name}",
                "version": "1.0.0",
                "description": f"Overlay spec to normalize {api_config.service_name} API",
            },
            "actions": [
                {
                    "target": "$.info.title",
                    "update": f"Equinix {api_config.service_name.title()} API",
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
        overlay_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(overlay_path, "w") as f:
            await f.write(yaml.dump(overlay, default_flow_style=False))

    async def get_merged_spec(self) -> Dict[str, Any]:
        """Get the merged OpenAPI specification."""
        if not self.specs_cache:
            await self.update_specs()

        # Start with base spec structure
        merged_spec = {
            "openapi": "3.0.3",
            "info": {
                "title": "Equinix Unified API",
                "version": "1.0.0",
                "description": "Merged Equinix APIs for MCP Server",
                "contact": {
                    "name": "Equinix API Support",
                    "url": "https://docs.equinix.com",
                },
            },
            "servers": [
                {"url": "https://api.equinix.com", "description": "Equinix API Server"}
            ],
            "paths": {},
            "components": {
                "schemas": {},
                "securitySchemes": {
                    "ClientCredentials": {
                        "type": "oauth2",
                        "flows": {
                            "clientCredentials": {
                                "tokenUrl": "https://api.equinix.com/oauth2/v1/token",
                                "scopes": {
                                    "read": "Read access",
                                    "write": "Write access",
                                },
                            }
                        },
                    },
                    "MetalToken": {
                        "type": "apiKey",
                        "in": "header",
                        "name": "X-Auth-Token",
                    },
                },
            },
            "security": [{"ClientCredentials": ["read", "write"]}, {"MetalToken": []}],
        }

        # Merge each API spec
        for api_name, spec in self.specs_cache.items():
            await self._merge_api_spec(merged_spec, api_name, spec)

        # Save merged spec
        output_path = Path(self.config.output.merged_spec_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(output_path, "w") as f:
            await f.write(yaml.dump(merged_spec, default_flow_style=False))

        return merged_spec

    async def _merge_api_spec(
        self, merged_spec: Dict[str, Any], api_name: str, spec: Dict[str, Any]
    ) -> None:
        """Merge a single API spec into the merged spec."""
        api_config = self.config.get_api_config(api_name)
        if not api_config:
            return

        # Apply overlay if available
        if api_name in self.overlays_cache:
            spec = await self._apply_overlay(spec, self.overlays_cache[api_name])

        # Merge paths with prefixing
        spec_paths = spec.get("paths", {})
        for path, methods in spec_paths.items():
            # Normalize path based on API type
            normalized_path = await self._normalize_path(api_name, path)

            # Add operationId prefixes to avoid conflicts
            for method, operation in methods.items():
                if isinstance(operation, dict) and "operationId" in operation:
                    operation["operationId"] = f"{api_name}_{operation['operationId']}"

                    # Add tags for organization
                    if "tags" not in operation:
                        operation["tags"] = []
                    operation["tags"].append(api_config.service_name)

            merged_spec["paths"][normalized_path] = methods

        # Merge components/schemas with prefixing
        components = spec.get("components", {})
        schemas = components.get("schemas", {})

        for schema_name, schema_def in schemas.items():
            prefixed_name = f"{api_name.title()}{schema_name}"
            merged_spec["components"]["schemas"][prefixed_name] = schema_def

    async def _normalize_path(self, api_name: str, path: str) -> str:
        """Normalize API paths to handle different base path conventions."""
        api_config = self.config.get_api_config(api_name)
        if not api_config:
            return path

        # Metal API uses /metal/v1 prefix
        if api_name == "metal":
            if not path.startswith("/metal/v1"):
                return f"/metal/v1{path}"
            return path

        # Other APIs use /{service}/{version} pattern
        expected_prefix = f"/{api_config.service_name}/{api_config.version}"
        if not path.startswith(expected_prefix):
            return f"{expected_prefix}{path}"

        return path

    async def _apply_overlay(
        self, spec: Dict[str, Any], overlay: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply overlay transformations to a spec."""
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
