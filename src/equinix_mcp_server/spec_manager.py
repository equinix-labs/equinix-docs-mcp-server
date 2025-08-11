"""Manages the lifecycle of OpenAPI specifications.

This module is responsible for fetching, caching, merging, and applying
overlays to OpenAPI specifications as defined in the configuration. It
handles multiple spec sources per API namespace and combines them into a
single, coherent specification.
"""

import asyncio
import collections.abc
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import prance
import yaml
from openapi_spec_validator import validate
from prance.convert import convert_spec

from .config import APIConfig, Config
from .openapi_overlays.overlay_manager import OverlayManager

logger = logging.getLogger(__name__)


def deep_merge(d: Dict[str, Any], u: collections.abc.Mapping) -> Dict[str, Any]:
    """
    Merge two dictionaries recursively.

    :param d: The dictionary to merge into.
    :param u: The dictionary to merge from.
    :return: The merged dictionary.
    """
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = deep_merge(d.get(k, {}), v)
        else:
            d[k] = v
    return d


def _update_references(obj: Any) -> Any:
    """Recursively update '$ref' values in the spec."""
    # This function is currently not used, but is kept for future reference.
    if isinstance(obj, dict):
        for key, value in obj.items():
            if (
                key == "$ref"
                and isinstance(value, str)
                and value.startswith("#/components/schemas/")
            ):
                new_ref = f"#/$defs/{value.split('/')[-1]}"
                obj[key] = new_ref
            else:
                _update_references(value)
    elif isinstance(obj, list):
        for item in obj:
            _update_references(item)
    return obj


class SpecManager:
    """Manages fetching, caching, and merging of OpenAPI specifications."""

    def __init__(self, config: Config, cache_dir: str = "cache/specs"):
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.overlay_manager = OverlayManager(config)
        self.http_client = httpx.AsyncClient()

    async def update_specs(self) -> None:
        """Update all API specifications based on the configuration."""
        tasks = []
        for api_name, api_config in self.config.apis.items():
            if api_config.enabled:
                tasks.append(self._process_api_group(api_name, api_config))
        await asyncio.gather(*tasks)

    async def _process_api_group(self, api_name: str, api_config: APIConfig) -> None:
        """Process a single group of API specifications."""
        logger.info(f"Processing API group: {api_name}")
        base_spec: Optional[Dict[str, Any]] = None
        for i, spec_source in enumerate(api_config.specs):
            spec = await self._fetch_and_cache_spec(f"{api_name}_{i}", spec_source.url)
            if spec:
                # Extract base path from server URL
                base_path = ""
                if "servers" in spec and spec["servers"]:
                    server_url = spec["servers"][0].get("url", "")
                    if server_url:
                        from urllib.parse import urlparse

                        parsed_url = urlparse(server_url)
                        base_path = parsed_url.path
                        # remove trailing slash if present
                        if base_path.endswith("/"):
                            base_path = base_path[:-1]

                # Prepend base_path to all paths
                if base_path and "paths" in spec:
                    new_paths = {}
                    for path, path_item in spec["paths"].items():
                        new_paths[f"{base_path}{path}"] = path_item
                    spec["paths"] = new_paths

                if base_spec is None:
                    base_spec = spec
                else:
                    # Merge subsequent specs into the base spec
                    base_spec["paths"].update(spec.get("paths", {}))
                    if "components" in spec:
                        logger.debug(
                            f"Merging components for {api_name}: {spec.get('components')}"
                        )
                        deep_merge(
                            base_spec.setdefault("components", {}), spec["components"]
                        )
                    if "$defs" in spec:
                        logger.debug(
                            f"Merging $defs for {api_name}: {spec.get('$defs')}"
                        )
                        deep_merge(base_spec.setdefault("$defs", {}), spec["$defs"])

        if base_spec:
            merged_spec = self._merge_api_spec(base_spec, api_config)
            self.save_merged_spec(api_name, merged_spec)

    async def _fetch_and_cache_spec(
        self, spec_key: str, url: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a spec from a URL, validate, and cache it."""
        try:
            response = await self.http_client.get(url)
            response.raise_for_status()
            spec_content = response.text

            # The fabric spec has a weird tag that pyyaml doesn't like.
            # It's a bug in their spec. We'll just remove it.
            spec_content = spec_content.replace("!!value", "")
            spec_content = spec_content.replace("example: =", "example: ''")
            spec_content = spec_content.replace("operator: =", "operator: ''")
            spec_content = spec_content.replace("- =", "- ''")

            # Use FullLoader to handle more complex YAML tags.
            spec = yaml.load(spec_content, Loader=yaml.FullLoader)

            if spec.get("swagger") == "2.0":
                logger.info(f"Converting Swagger 2.0 spec to OpenAPI 3.x for {url}")
                parser = convert_spec(spec, prance.BaseParser)
                spec = parser.specification

            # Basic validation, more can be added if needed
            if not isinstance(spec, dict) or "openapi" not in spec:
                raise ValueError("Invalid OpenAPI spec structure")

            validate(spec)
            self.save_cached_spec(spec_key, spec)
            logger.info(f"Successfully fetched and validated spec from {url}")
            return spec
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching spec from {url}: {e}")
        except yaml.YAMLError as e:
            logger.error(f"YAML parsing error for spec from {url}: {e}")
            return None  # Return None to prevent malformed data from proceeding
        except Exception as e:
            logger.error(f"Failed to fetch or validate spec from {url}: {e}")
            return None

        # If all fails, try to load from cache as a last resort.
        return self.load_cached_spec(spec_key)

    def _merge_api_spec(
        self, spec: Dict[str, Any], api_config: APIConfig
    ) -> Dict[str, Any]:
        """Apply overlays and transformations to a specification."""
        if not api_config.name:
            return spec

        for spec_source in api_config.specs:
            if spec_source.overlay and self.config.config_path:
                overlay_path = (
                    Path(self.config.config_path).parent / spec_source.overlay
                )
                if overlay_path.exists():
                    with open(overlay_path, "r") as f:
                        overlay = yaml.safe_load(f)
                        spec = self.overlay_manager.apply(
                            spec, api_config.name, overlay
                        )
        return spec

    def get_merged_spec(self) -> Dict[str, Any]:
        """Merge all individual API specs into a single spec."""
        all_specs = self.get_all_merged_specs()

        # Start with a base structure for the merged spec
        merged_spec: Dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {
                "title": "Equinix MCP Combined API",
                "version": "1.0.0",
                "description": "A merged API of all enabled Equinix services.",
            },
            "servers": [{"url": "https://api.equinix.com"}],
            "paths": {},
            "components": {
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
            "$defs": {},  # for schema components
        }

        if not all_specs:
            # Clean up empty keys before returning
            if not merged_spec["components"]:
                del merged_spec["components"]
            if not merged_spec["$defs"]:
                del merged_spec["$defs"]
            return merged_spec

        # Merge all specs
        for api_name, spec in all_specs.items():
            if not spec:
                continue

            # Prefix all operationIds with the API name
            if "paths" in spec:
                for path, path_item in spec["paths"].items():
                    for method, operation in path_item.items():
                        if "operationId" in operation:
                            operation["operationId"] = (
                                f"{api_name}_{operation['operationId']}"
                            )

            if "paths" in spec:
                merged_spec.setdefault("paths", {}).update(spec["paths"])

            if "components" in spec:
                # Schemas go into $defs
                if "schemas" in spec["components"]:
                    deep_merge(
                        merged_spec.setdefault("$defs", {}),
                        spec["components"]["schemas"],
                    )

                # Other components go into components
                for component_type, components in spec["components"].items():
                    if component_type != "schemas":
                        deep_merge(
                            merged_spec.setdefault("components", {}).setdefault(
                                component_type, {}
                            ),
                            components,
                        )

            if "$defs" in spec:
                deep_merge(merged_spec.setdefault("$defs", {}), spec["$defs"])

        # Update all schema references to point to $defs
        merged_spec = _update_references(merged_spec)

        # Clean up empty keys before returning
        if not merged_spec.get("components"):
            merged_spec.pop("components", None)
        if not merged_spec.get("$defs"):
            merged_spec.pop("$defs", None)

        return merged_spec

    def get_merged_spec_path(self, api_name: str) -> Path:
        """Get the path to the merged spec file."""
        return self.cache_dir / f"{api_name}-merged.yaml"

    def save_merged_spec(self, api_name: str, spec: Dict[str, Any]) -> None:
        """Save a merged specification to the cache."""
        path = self.get_merged_spec_path(api_name)
        with open(path, "w") as f:
            yaml.dump(spec, f, sort_keys=False)
        logger.info(f"Saved merged spec for {api_name} to {path}")

    def load_merged_spec(self, api_name: str) -> Optional[Dict[str, Any]]:
        """Load a merged specification from the cache."""
        path = self.get_merged_spec_path(api_name)
        if path.exists():
            with open(path, "r") as f:
                return yaml.safe_load(f)
        return None

    def get_cached_spec_path(self, spec_key: str) -> Path:
        """Get the path to a cached specification file."""
        return self.cache_dir / f"{spec_key}.yaml"

    def save_cached_spec(self, spec_key: str, spec: Dict[str, Any]) -> None:
        """Save a fetched specification to the cache."""
        path = self.get_cached_spec_path(spec_key)
        with open(path, "w") as f:
            yaml.dump(spec, f, sort_keys=False)
        logger.info(f"Cached spec {spec_key} to {path}")

    def load_cached_spec(self, spec_key: str) -> Optional[Dict[str, Any]]:
        """Load a cached specification."""
        path = self.get_cached_spec_path(spec_key)
        if path.exists():
            logger.warning(f"Using cached spec for {spec_key} from {path}")
            with open(path, "r") as f:
                return yaml.safe_load(f)
        logger.error(f"No cached spec found for {spec_key}")
        return None

    def get_all_merged_specs(self) -> Dict[str, Dict[str, Any]]:
        """Load all merged specifications from the cache."""
        specs = {}
        for api_name in self.config.apis.keys():
            spec = self.load_merged_spec(api_name)
            if spec:
                specs[api_name] = spec
        return specs
