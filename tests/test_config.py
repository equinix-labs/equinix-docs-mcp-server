"""Test configuration loading and validation."""

from pathlib import Path

import pytest

from equinix_docs_mcp_server.config import APIConfig, Config


def test_config_loading():
    """Test loading configuration from YAML file."""
    config = Config.load("config/apis.yaml")

    assert config is not None
    assert len(config.apis) > 0
    assert "metal" in config.apis
    assert "fabric" in config.apis


def test_api_config_structure():
    """Test API configuration structure."""
    config = Config.load("config/apis.yaml")

    metal_config = config.get_api_config("metal")
    assert metal_config is not None
    assert metal_config.service_name == "metal"
    assert metal_config.auth_type == "metal_token"

    fabric_config = config.get_api_config("fabric")
    assert fabric_config is not None
    assert fabric_config.service_name == "fabric"
    assert fabric_config.auth_type == "client_credentials"


def test_config_api_names():
    """Test getting API names."""
    config = Config.load("config/apis.yaml")

    api_names = config.get_api_names()
    assert isinstance(api_names, list)
    assert "metal" in api_names
    assert "fabric" in api_names
    assert "billing" in api_names
    # assert "network-edge" in api_names


def test_config_save_load_roundtrip(tmp_path):
    """Test saving and loading configuration."""
    config = Config.load("config/apis.yaml")

    # Save to temporary file
    temp_config_path = tmp_path / "test_config.yaml"
    config.save(str(temp_config_path))

    # Load from temporary file
    loaded_config = Config.load(str(temp_config_path))

    # Compare
    assert loaded_config.get_api_names() == config.get_api_names()
    assert loaded_config.auth.client_credentials == config.auth.client_credentials
