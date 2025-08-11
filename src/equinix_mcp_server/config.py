"""Configuration management for the Equinix MCP Server.

This module handles loading and validating the server's configuration,
which defines the API specifications to be merged. It supports grouping
multiple spec sources (URL and an optional overlay) under a single API
namespace.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


class SpecSource(BaseModel):
    """Single spec source (URL + optional overlay)."""

    url: str = Field(..., description="URL to the OpenAPI spec")
    overlay: Optional[str] = Field(
        default=None, description="Optional path to overlay file"
    )


class APIConfig(BaseModel):
    """Configuration for an API group (namespace)."""

    # Not part of the YAML; set by loader for convenience/back-compat
    name: Optional[str] = Field(default=None, description="API key/name in config")

    specs: List[SpecSource] = Field(
        default_factory=list,
        description="List of spec sources to merge under this API namespace",
    )
    auth_type: Optional[str] = Field(
        default=None, description="Authentication type for this API group"
    )
    service_name: Optional[str] = Field(
        default=None, description="Service name (used for path normalization)"
    )
    enabled: bool = Field(default=True, description="Enable/disable this API")
    include: List[str] = Field(
        default_factory=list,
        description="Regex patterns of operationIds to include (after prefix)",
    )
    exclude: List[str] = Field(
        default_factory=list,
        description="Regex patterns of operationIds to exclude (after prefix)",
    )

    # Backward-compat convenience properties (legacy single-spec shape)
    @property
    def url(self) -> str:
        return self.specs[0].url if self.specs else ""

    @property
    def overlay(self) -> Optional[str]:
        # Prefer overlay from first spec if present
        if self.specs and self.specs[0].overlay:
            return self.specs[0].overlay
        # No overlay defined for this API group
        return None


class AuthConfig(BaseModel):
    """Authentication configuration."""

    client_credentials: Dict[str, Any] = Field(default_factory=dict)
    metal_token: Dict[str, Any] = Field(default_factory=dict)


class OutputConfig(BaseModel):
    """Output configuration."""

    merged_spec_path: str = Field(default="merged-openapi.yaml")


class DocsConfig(BaseModel):
    """Documentation configuration."""

    sitemap_url: str = Field(default="https://docs.equinix.com/sitemap.xml")
    cache_path: str = Field(default="docs/sitemap_cache.xml")


class Config(BaseModel):
    """Main configuration class."""

    config_path: Optional[str] = Field(
        default=None, description="Path to the loaded config file"
    )
    apis: Dict[str, APIConfig] = Field(default_factory=dict)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    docs: DocsConfig = Field(default_factory=DocsConfig)

    @classmethod
    def load(cls, config_path: str) -> "Config":
        """Load configuration from a YAML file."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(path, "r") as f:
            config_data = yaml.safe_load(f)

        # Convert API configs to APIConfig objects
        if "apis" in config_data:
            apis = {}
            for name, api_data in config_data["apis"].items():
                if not isinstance(api_data, dict):
                    raise ValueError(f"Invalid API config for {name}: {api_data}")

                # Back-compat: allow top-level url/overlay by converting to specs list
                specs_list: List[SpecSource] = []
                if "specs" in api_data and isinstance(api_data["specs"], list):
                    for item in api_data["specs"]:
                        if not isinstance(item, dict) or "url" not in item:
                            raise ValueError(f"Invalid spec entry for {name}: {item}")
                        specs_list.append(SpecSource(**item))
                else:
                    # Legacy keys
                    if "url" in api_data:
                        specs_list.append(
                            SpecSource(
                                url=api_data["url"], overlay=api_data.get("overlay")
                            )
                        )

                # Build model dict
                model_data: Dict[str, Any] = {
                    "name": name,
                    "specs": specs_list,
                    "auth_type": api_data.get("auth_type"),
                    "service_name": api_data.get("service_name"),
                    "enabled": api_data.get("enabled", True),
                    "include": api_data.get("include", []) or [],
                    "exclude": api_data.get("exclude", []) or [],
                }

                apis[name] = APIConfig(**model_data)
            config_data["apis"] = apis

        config = cls(**config_data)
        config.config_path = config_path
        return config

    def save(self, config_path: str) -> None:
        """Save configuration to a YAML file."""
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dict for YAML serialization
        config_dict = self.model_dump()

        with open(path, "w") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

    def get_api_names(self) -> List[str]:
        """Get list of configured API names."""
        return list(self.apis.keys())

    def get_api_config(self, name: str) -> Optional[APIConfig]:
        """Get configuration for a specific API."""
        return self.apis.get(name)
