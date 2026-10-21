"""Test authentication functionality."""

import os
from unittest.mock import AsyncMock, mock_open, patch

import pytest

from equinix_docs_mcp_server.auth import AuthManager, _load_equinix_yaml
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


@pytest.mark.asyncio
async def test_client_credentials_auth_missing_vars(config):
    """Test client credentials authentication fails during token fetch when credentials present."""
    with (
        patch.dict(
            os.environ,
            {
                "EQUINIX_CLIENT_ID": "test_client_id",
                "EQUINIX_CLIENT_SECRET": "test_client_secret",
            },
            clear=True,
        ),
        patch("equinix_docs_mcp_server.auth._load_equinix_yaml", return_value={}),
    ):
        am = AuthManager(config)
        with pytest.raises(Exception):  # Will fail on actual HTTP request
            await am.get_auth_header("fabric")


@pytest.mark.asyncio
async def test_client_credentials_auth_missing_credentials(config):
    """Test client credentials authentication with missing credentials."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("equinix_docs_mcp_server.auth._load_equinix_yaml", return_value={}),
    ):
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
        "equinix_client_id: my_id\nequinix_client_secret: my_secret\n"
    )
    with patch("equinix_docs_mcp_server.auth.Path.home", return_value=tmp_path):
        result = _load_equinix_yaml()
    assert result == {
        "equinix_client_id": "my_id",
        "equinix_client_secret": "my_secret",
    }


def test_auth_manager_falls_back_to_equinix_yaml(config, tmp_path):
    """AuthManager reads client_id/secret from equinix.yaml when env vars absent."""
    cfg_dir = tmp_path / ".config" / "equinix"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "equinix.yaml").write_text(
        "equinix_client_id: file_id\nequinix_client_secret: file_secret\n"
    )
    with (
        patch.dict(os.environ, {}, clear=True),
        patch(
            "equinix_docs_mcp_server.auth._load_equinix_yaml",
            return_value={
                "equinix_client_id": "file_id",
                "equinix_client_secret": "file_secret",
            },
        ),
    ):
        am = AuthManager(config)
    assert am.client_id == "file_id"
    assert am.client_secret == "file_secret"


def test_auth_manager_env_takes_priority_over_files(config):
    """Env vars take priority over file-based credentials."""
    with (
        patch.dict(
            os.environ,
            {
                "EQUINIX_CLIENT_ID": "env_id",
                "EQUINIX_CLIENT_SECRET": "env_secret",
            },
            clear=True,
        ),
        patch(
            "equinix_docs_mcp_server.auth._load_equinix_yaml",
            return_value={
                "equinix_client_id": "file_id",
                "equinix_client_secret": "file_secret",
            },
        ),
    ):
        am = AuthManager(config)
    assert am.client_id == "env_id"
    assert am.client_secret == "env_secret"


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
