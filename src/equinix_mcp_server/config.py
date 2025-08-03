"""Configuration management for the Equinix MCP Server."""

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


class APIConfig(BaseModel):
    """Configuration for a single API."""

    url: str = Field(..., description="URL to the OpenAPI spec")
    overlay: str = Field(..., description="Path to overlay file")
    auth_type: str = Field(..., description="Authentication type")
    service_name: str = Field(..., description="Service name")
    version: str = Field(..., description="API version")


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
                apis[name] = APIConfig(**api_data)
            config_data["apis"] = apis

        return cls(**config_data)

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
