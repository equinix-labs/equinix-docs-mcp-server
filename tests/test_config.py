"""Test configuration loading and validation."""

from pathlib import Path

import pytest

from equinix_docs_mcp_server.config import APIConfig, Config


def test_config_loading():
    """Test loading configuration from YAML file."""
    config = Config.load()

    assert config is not None
    assert len(config.apis) > 0
    assert "fabric" in config.apis
    assert "network-edge" in config.apis


def test_api_config_structure():
    """Test API configuration structure."""
    config = Config.load()

    fabric_config = config.get_api_config("fabric")
    assert fabric_config is not None
    assert fabric_config.service_name == "fabric"
    assert fabric_config.auth_type == "client_credentials"


def test_config_api_names():
    """Test getting API names."""
    config = Config.load()

    api_names = config.get_api_names()
    assert isinstance(api_names, list)
    assert "fabric" in api_names
    assert "billing" in api_names
    # assert "network-edge" in api_names


def test_config_save_load_roundtrip(tmp_path):
    """Test saving and loading configuration."""
    config = Config.load()

    # Save to temporary file
    temp_config_path = tmp_path / "test_config.yaml"
    config.save(str(temp_config_path))

    # Load from temporary file
    loaded_config = Config.load(str(temp_config_path))

    # Compare
    assert loaded_config.get_api_names() == config.get_api_names()
    assert loaded_config.auth.client_credentials == config.auth.client_credentials
