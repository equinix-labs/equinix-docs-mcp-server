"""Test authentication functionality."""

import os
from unittest.mock import AsyncMock, mock_open, patch

import pytest

from equinix_docs_mcp_server.auth import (
    AuthManager,
    _load_equinix_yaml,
    _load_metal_yaml,
)
from equinix_docs_mcp_server.config import Config


@pytest.fixture
def config():
    """Load test configuration."""
    return Config.load()


@pytest.fixture
def auth_manager(config):
    """Create auth manager instance."""
    return AuthManager(config)


def test_auth_manager_init(auth_manager):
    """Test AuthManager initialization."""
    assert auth_manager is not None
    assert auth_manager.config is not None


@patch.dict(os.environ, {"EQUINIX_METAL_TOKEN": "test_metal_token"}, clear=True)
@pytest.mark.asyncio
async def test_metal_auth_header(auth_manager):
    """Test Metal API authentication header."""
    # Reinitialize the auth manager with the patched environment
    auth_manager.metal_token = os.getenv("EQUINIX_METAL_TOKEN")

    header = await auth_manager.get_auth_header("metal")

    assert "X-Auth-Token" in header
    assert header["X-Auth-Token"] == "test_metal_token"


@pytest.mark.asyncio
async def test_metal_auth_missing_token(config):
    """Test Metal API authentication with missing token."""
    with patch.dict(os.environ, {}, clear=True), patch(
        "equinix_docs_mcp_server.auth._load_equinix_yaml", return_value={}
    ), patch("equinix_docs_mcp_server.auth._load_metal_yaml", return_value={}):
        am = AuthManager(config)
        with pytest.raises(ValueError, match="Metal token not found"):
            await am.get_auth_header("metal")


@pytest.mark.asyncio
async def test_client_credentials_auth_missing_vars(config):
    """Test client credentials authentication fails during token fetch when credentials present."""
    with patch.dict(
        os.environ,
        {
            "EQUINIX_CLIENT_ID": "test_client_id",
            "EQUINIX_CLIENT_SECRET": "test_client_secret",
        },
        clear=True,
    ), patch("equinix_docs_mcp_server.auth._load_equinix_yaml", return_value={}), patch(
        "equinix_docs_mcp_server.auth._load_metal_yaml", return_value={}
    ):
        am = AuthManager(config)
        with pytest.raises(Exception):  # Will fail on actual HTTP request
            await am.get_auth_header("fabric")


@pytest.mark.asyncio
async def test_client_credentials_auth_missing_credentials(config):
    """Test client credentials authentication with missing credentials."""
    with patch.dict(os.environ, {}, clear=True), patch(
        "equinix_docs_mcp_server.auth._load_equinix_yaml", return_value={}
    ), patch("equinix_docs_mcp_server.auth._load_metal_yaml", return_value={}):
        am = AuthManager(config)
        with pytest.raises(ValueError, match="Client credentials not found"):
            await am.get_auth_header("fabric")


def test_load_equinix_yaml_missing(tmp_path):
    """Returns empty dict when equinix.yaml does not exist."""
    with patch("equinix_docs_mcp_server.auth.Path.home", return_value=tmp_path):
        result = _load_equinix_yaml()
    assert result == {}


def test_load_equinix_yaml_reads_keys(tmp_path):
    """Reads equinix_client_id and equinix_client_secret from equinix.yaml."""
    cfg_dir = tmp_path / ".config" / "equinix"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "equinix.yaml").write_text(
        "equinix_client_id: my_id\nequinix_client_secret: my_secret\nmetal_auth_token: my_metal\n"
    )
    with patch("equinix_docs_mcp_server.auth.Path.home", return_value=tmp_path):
        result = _load_equinix_yaml()
    assert result == {
        "equinix_client_id": "my_id",
        "equinix_client_secret": "my_secret",
        "metal_auth_token": "my_metal",
    }


def test_load_metal_yaml_reads_token(tmp_path):
    """Reads token from ~/.config/equinix/metal.yaml (metal-cli format)."""
    cfg_dir = tmp_path / ".config" / "equinix"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "metal.yaml").write_text("token: my_metal_token\n")
    with patch("equinix_docs_mcp_server.auth.Path.home", return_value=tmp_path):
        result = _load_metal_yaml()
    assert result == {"token": "my_metal_token"}


def test_auth_manager_falls_back_to_equinix_yaml(config, tmp_path):
    """AuthManager reads client_id/secret from equinix.yaml when env vars absent."""
    cfg_dir = tmp_path / ".config" / "equinix"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "equinix.yaml").write_text(
        "equinix_client_id: file_id\nequinix_client_secret: file_secret\n"
    )
    with patch.dict(os.environ, {}, clear=True), patch(
        "equinix_docs_mcp_server.auth._load_equinix_yaml",
        return_value={
            "equinix_client_id": "file_id",
            "equinix_client_secret": "file_secret",
        },
    ), patch(
        "equinix_docs_mcp_server.auth._load_metal_yaml",
        return_value={},
    ):
        am = AuthManager(config)
    assert am.client_id == "file_id"
    assert am.client_secret == "file_secret"


def test_auth_manager_falls_back_to_metal_yaml(config):
    """AuthManager reads metal token from metal.yaml when env and equinix.yaml are absent."""
    with patch.dict(os.environ, {}, clear=True), patch(
        "equinix_docs_mcp_server.auth._load_equinix_yaml",
        return_value={},
    ), patch(
        "equinix_docs_mcp_server.auth._load_metal_yaml",
        return_value={"token": "metal_file_token"},
    ):
        am = AuthManager(config)
    assert am.metal_token == "metal_file_token"


def test_auth_manager_equinix_yaml_metal_takes_priority_over_metal_yaml(config):
    """metal_auth_token in equinix.yaml takes priority over token in metal.yaml."""
    with patch.dict(os.environ, {}, clear=True), patch(
        "equinix_docs_mcp_server.auth._load_equinix_yaml",
        return_value={"metal_auth_token": "equinix_yaml_token"},
    ), patch(
        "equinix_docs_mcp_server.auth._load_metal_yaml",
        return_value={"token": "metal_yaml_token"},
    ):
        am = AuthManager(config)
    assert am.metal_token == "equinix_yaml_token"


def test_auth_manager_env_takes_priority_over_files(config):
    """Env vars take priority over file-based credentials."""
    with patch.dict(
        os.environ,
        {
            "EQUINIX_CLIENT_ID": "env_id",
            "EQUINIX_CLIENT_SECRET": "env_secret",
            "EQUINIX_METAL_TOKEN": "env_metal",
        },
        clear=True,
    ), patch(
        "equinix_docs_mcp_server.auth._load_equinix_yaml",
        return_value={
            "equinix_client_id": "file_id",
            "equinix_client_secret": "file_secret",
            "metal_auth_token": "file_metal",
        },
    ):
        am = AuthManager(config)
    assert am.client_id == "env_id"
    assert am.client_secret == "env_secret"
    assert am.metal_token == "env_metal"


def test_clear_token_cache(auth_manager):
    """Test clearing token cache."""
    auth_manager._token_cache["test"] = "token"
    assert len(auth_manager._token_cache) == 1

    auth_manager.clear_token_cache()
    assert len(auth_manager._token_cache) == 0


@pytest.mark.asyncio
async def test_unknown_service(auth_manager):
    """Test handling unknown service."""
    with pytest.raises(ValueError, match="Unknown service"):
        await auth_manager.get_auth_header("unknown_service")
