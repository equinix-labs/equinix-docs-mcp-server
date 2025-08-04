"""OpenAPI spec management and merging functionality."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import httpx
import yaml
from openapi_spec_validator import validate_spec
import prance
from prance.convert import convert_spec

from .config import Config
from .openapi_overlays import OverlayManager


class SpecManager:
    """Manages OpenAPI spec fetching, overlaying, and merging."""

    def __init__(self, config: Config):
        """Initialize with configuration."""
        self.config = config
        self.specs_cache: Dict[str, Dict[str, Any]] = {}
        self.overlay_manager = OverlayManager()

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

            # Parse YAML content with error handling
            try:
                spec = yaml.safe_load(response.text)
            except yaml.YAMLError as e:
                print(f"Warning: YAML parsing failed for {api_name}: {e}")
                print(f"Attempting to fix common YAML issues...")
                
                # Try to fix common YAML issues
                fixed_content = response.text
                
                # Fix various malformed YAML patterns
                yaml_fixes = [
                    ("example: =", "example: ''"),
                    ("operator: =", "operator: ''"),
                    ("default: =", "default: ''"),
                    ("value: =", "value: ''"),
                    ("- =", "- ''"),  # Array elements with just =
                    (": =\n", ": ''\n"),  # General pattern
                ]
                
                for old_pattern, new_pattern in yaml_fixes:
                    fixed_content = fixed_content.replace(old_pattern, new_pattern)
                
                try:
                    spec = yaml.safe_load(fixed_content)
                    print(f"✓ YAML parsing succeeded after fixing for {api_name}")
                except yaml.YAMLError as e2:
                    print(f"Error: Could not fix YAML for {api_name}: {e2}")
                    # Try to skip the problematic spec for now
                    print(f"Skipping {api_name} due to unfixable YAML errors")
                    return {}

            # Convert Swagger v2 to OpenAPI v3 if needed
            spec = await self._convert_swagger_to_openapi(spec, api_name)

            # Validate the spec
            try:
                validate_spec(spec)
                print(f"✓ Spec validation passed for {api_name}")
            except Exception as e:
                print(f"Warning: Spec validation failed for {api_name}: {e}")
                # Continue processing despite validation warnings

            # Cache the spec
            self.specs_cache[api_name] = spec

            # Save to local file for debugging
            cache_dir = Path("cache/specs")
            cache_dir.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(cache_dir / f"{api_name}.yaml", "w") as f:
                await f.write(yaml.dump(spec, default_flow_style=False))

            return spec

    async def _convert_swagger_to_openapi(self, spec: Dict[str, Any], api_name: str) -> Dict[str, Any]:
        """Convert Swagger v2 to OpenAPI v3 if needed."""
        # Check if this is a Swagger v2 spec
        if spec.get("swagger") == "2.0":
            print(f"Converting Swagger v2 to OpenAPI v3 for {api_name}...")
            try:
                # Use prance to convert Swagger v2 to OpenAPI v3
                # convert_spec returns a parser, we need the specification from it
                converted_parser = convert_spec(spec, prance.BaseParser)
                converted_spec = converted_parser.specification
                
                print(f"✓ Successfully converted {api_name} from Swagger v2 to OpenAPI v3")
                return converted_spec
                    
            except Exception as e:
                print(f"Error: Failed to convert {api_name} from Swagger v2: {e}")
                # Return original spec if conversion fails
                return spec
        else:
            # Already OpenAPI v3 or other format
            return spec

    async def _load_overlay(self, api_name: str) -> Optional[Dict[str, Any]]:
        """Load overlay file for an API."""
        api_config = self.config.get_api_config(api_name)
        if not api_config:
            return None

        overlay_path = api_config.overlay

        # Try to load existing overlay
        overlay = await self.overlay_manager.load_overlay(overlay_path)

        if overlay is None:
            # Create a basic overlay template if it doesn't exist
            await self.overlay_manager.create_overlay_template(
                overlay_path, api_config.service_name, api_name
            )
            return None

        return overlay

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
        overlay = self.overlay_manager.get_cached_overlay(api_config.overlay)
        if overlay:
            spec = await self.overlay_manager.apply_overlay(spec, overlay)

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
