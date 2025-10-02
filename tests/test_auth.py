"""Test authentication functionality."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from equinix_docs_mcp_server.auth import AuthManager
from equinix_docs_mcp_server.config import Config


@pytest.fixture
def config():
    """Load test configuration."""
    return Config.load("config/apis.yaml")


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
async def test_metal_auth_missing_token(auth_manager):
    """Test Metal API authentication with missing token."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="EQUINIX_METAL_TOKEN"):
            await auth_manager.get_auth_header("metal")


@patch.dict(
    os.environ,
    {
        "EQUINIX_CLIENT_ID": "test_client_id",
        "EQUINIX_CLIENT_SECRET": "test_client_secret",
    },
)
@pytest.mark.asyncio
async def test_client_credentials_auth_missing_vars(auth_manager):
    """Test client credentials authentication setup."""
    # This will fail during token fetch, but should get past credential check
    with pytest.raises(Exception):  # Will fail on actual HTTP request
        await auth_manager.get_auth_header("fabric")


@pytest.mark.asyncio
async def test_client_credentials_auth_missing_credentials(auth_manager):
    """Test client credentials authentication with missing credentials."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="EQUINIX_CLIENT_ID"):
            await auth_manager.get_auth_header("fabric")


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
