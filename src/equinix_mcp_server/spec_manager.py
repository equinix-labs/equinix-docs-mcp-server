"""OpenAPI spec management and merging functionality."""

import json
from pathlib import Path
from typing import Any, Dict, Hashable, List, Mapping, Optional

import aiofiles
import httpx
import prance
import yaml
from openapi_spec_validator import validate_spec
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
                validate_spec(spec)  # type: ignore[arg-type]
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

    async def _convert_swagger_to_openapi(
        self, spec: Dict[str, Any], api_name: str
    ) -> Dict[str, Any]:
        """Convert Swagger v2 to OpenAPI v3 if needed."""
        # Check if this is a Swagger v2 spec
        if spec.get("swagger") == "2.0":
            print(f"Converting Swagger v2 to OpenAPI v3 for {api_name}...")
            try:
                # Use prance to convert Swagger v2 to OpenAPI v3
                # convert_spec returns a parser, we need the specification from it
                converted_parser = convert_spec(spec, prance.BaseParser)
                converted_spec = converted_parser.specification

                print(
                    f"✓ Successfully converted {api_name} from Swagger v2 to OpenAPI v3"
                )
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
        """Get the merged OpenAPI specification in 3.1 format."""
        if not self.specs_cache:
            await self.update_specs()

        # Start with base spec structure (OpenAPI 3.1)
        merged_spec = {
            "openapi": "3.1.0",  # Changed to 3.1.0
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
            "$defs": {},  # Use $defs instead of components/schemas
            "components": {
                "requestBodies": {},
                "responses": {},
                "parameters": {},
                "examples": {},
                "headers": {},
                "securitySchemes": {
                    "ClientCredentials": {
                        "type": "oauth2",
                        "flows": {
                            "clientCredentials": {
                                "tokenUrl": "https://api.equinix.com/oauth2/v1/token",
                                "scopes": {},
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
            "security": [{"ClientCredentials": []}, {"MetalToken": []}],
        }

        # Merge each API spec
        for api_name, spec in self.specs_cache.items():
            await self._merge_api_spec(merged_spec, api_name, spec)

        # Save merged spec
        output_path = Path(self.config.output.merged_spec_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(output_path, "w") as f:
            yaml_output = yaml.dump(
                merged_spec,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
                width=float("inf"),
            )
            await f.write(yaml_output)

        return merged_spec

    async def _merge_api_spec(
        self, merged_spec: Dict[str, Any], api_name: str, spec: Dict[str, Any]
    ) -> None:
        """Merge a single API spec into the merged spec with OpenAPI 3.1 format."""
        api_config = self.config.get_api_config(api_name)
        if not api_config:
            return

        # Apply overlay if available
        overlay = self.overlay_manager.get_cached_overlay(api_config.overlay)
        if overlay:
            spec = await self.overlay_manager.apply_overlay(spec, overlay)

        # Create a deep copy of the spec to avoid modifying the original
        import copy

        spec = copy.deepcopy(spec)

        # Convert OpenAPI 3.0 to 3.1 format
        spec = self._convert_to_openapi_31(spec)

        # Get all schemas from both components/schemas and $defs
        components = spec.get("components", {})
        schemas = components.get("schemas", {})
        defs_schemas = spec.get("$defs", {})

        # Merge both schema sources
        all_schemas = {**schemas, **defs_schemas}

        # Component types (excluding schemas as we handle them separately)
        component_types = [
            "requestBodies",
            "responses",
            "parameters",
            "examples",
            "headers",
            "securitySchemes",
        ]
        component_mappings = {}

        # Initialize mappings for component types
        for comp_type in component_types:
            component_mappings[comp_type] = {}
            comp_dict = components.get(comp_type, {})
            for comp_name in comp_dict.keys():
                prefixed_name = f"{api_name.title()}{comp_name}"
                component_mappings[comp_type][comp_name] = prefixed_name

        # Create schema mapping for $defs
        schema_mappings = {}
        for schema_name in all_schemas.keys():
            prefixed_name = f"{api_name.title()}{schema_name}"
            schema_mappings[schema_name] = prefixed_name

        # Function to update all references recursively for OpenAPI 3.1
        def update_all_refs(obj, schema_mappings, component_mappings):
            if isinstance(obj, dict):
                if "$ref" in obj and isinstance(obj["$ref"], str):
                    ref = obj["$ref"]

                    # Handle $defs references (OpenAPI 3.1 style)
                    if ref.startswith("#/$defs/"):
                        schema_name = ref.split("/")[-1]
                        if schema_name in schema_mappings:
                            obj["$ref"] = f"#/$defs/{schema_mappings[schema_name]}"
                    # Handle components/schemas references (convert to $defs)
                    elif ref.startswith("#/components/schemas/"):
                        schema_name = ref.split("/")[-1]
                        if schema_name in schema_mappings:
                            obj["$ref"] = f"#/$defs/{schema_mappings[schema_name]}"
                    # Handle legacy definitions (convert to $defs)
                    elif ref.startswith("#/definitions/"):
                        schema_name = ref.split("/")[-1]
                        if schema_name in schema_mappings:
                            obj["$ref"] = f"#/$defs/{schema_mappings[schema_name]}"
                    # Handle other component types
                    else:
                        for comp_type in component_types:
                            ref_pattern = f"#/components/{comp_type}/"
                            if ref.startswith(ref_pattern):
                                comp_name = ref.split("/")[-1]
                                if comp_name in component_mappings[comp_type]:
                                    obj["$ref"] = (
                                        f"#/components/{comp_type}/{component_mappings[comp_type][comp_name]}"
                                    )
                                break
                else:
                    for key, value in obj.items():
                        update_all_refs(value, schema_mappings, component_mappings)
            elif isinstance(obj, list):
                for item in obj:
                    update_all_refs(item, schema_mappings, component_mappings)

        # Update all references in the spec
        update_all_refs(spec, schema_mappings, component_mappings)

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

        # Merge schemas into $defs
        for schema_name, schema_def in all_schemas.items():
            prefixed_name = schema_mappings.get(schema_name, schema_name)
            merged_spec["$defs"][prefixed_name] = copy.deepcopy(schema_def)

        # Merge other component types
        for comp_type in component_types:
            comp_dict = components.get(comp_type, {})
            if comp_type not in merged_spec["components"]:
                merged_spec["components"][comp_type] = {}

            for comp_name, comp_def in comp_dict.items():
                prefixed_name = component_mappings[comp_type].get(comp_name, comp_name)
                merged_spec["components"][comp_type][prefixed_name] = copy.deepcopy(
                    comp_def
                )

    def _convert_to_openapi_31(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Convert OpenAPI 3.0 spec to 3.1 format."""
        # Update version if it's 3.0.x
        if spec.get("openapi", "").startswith("3.0"):
            spec["openapi"] = "3.1.0"

        # Initialize $defs if it doesn't exist
        if "$defs" not in spec:
            spec["$defs"] = {}

        # Move components/schemas to $defs if they exist
        components = spec.get("components", {})
        schemas = components.get("schemas", {})

        if schemas:
            # Move schemas to $defs
            spec["$defs"].update(schemas)

            # Remove schemas from components
            if "schemas" in components:
                del components["schemas"]

        # Also handle legacy Swagger v2 "definitions" if they exist
        definitions = spec.get("definitions", {})
        if definitions:
            spec["$defs"].update(definitions)
            # Remove definitions from root
            if "definitions" in spec:
                del spec["definitions"]

        # Update all $ref paths to use $defs
        def update_schema_refs(obj):
            if isinstance(obj, dict):
                if "$ref" in obj and isinstance(obj["$ref"], str):
                    ref = obj["$ref"]
                    if ref.startswith("#/components/schemas/"):
                        schema_name = ref.split("/")[-1]
                        obj["$ref"] = f"#/$defs/{schema_name}"
                    elif ref.startswith("#/definitions/"):
                        schema_name = ref.split("/")[-1]
                        obj["$ref"] = f"#/$defs/{schema_name}"
                else:
                    for value in obj.values():
                        update_schema_refs(value)
            elif isinstance(obj, list):
                for item in obj:
                    update_schema_refs(item)

        update_schema_refs(spec)
        return spec

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
