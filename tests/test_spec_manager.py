"""Test spec manager functionality."""
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from equinix_mcp_server.config import Config
from equinix_mcp_server.spec_manager import SpecManager


@pytest.fixture
def config():
    """Load test configuration."""
    return Config.load("config/apis.yaml")


@pytest.fixture
def spec_manager(config):
    """Create spec manager instance."""
    return SpecManager(config)


def test_spec_manager_init(spec_manager):
    """Test SpecManager initialization."""
    assert spec_manager is not None
    assert spec_manager.config is not None
    assert isinstance(spec_manager.specs_cache, dict)
    assert isinstance(spec_manager.overlays_cache, dict)


@pytest.mark.asyncio
async def test_normalize_path_metal(spec_manager):
    """Test path normalization for Metal API."""
    # Metal API should use /metal/v1 prefix
    normalized = await spec_manager._normalize_path("metal", "/devices")
    assert normalized == "/metal/v1/devices"
    
    # Already normalized path should remain unchanged
    normalized = await spec_manager._normalize_path("metal", "/metal/v1/devices")
    assert normalized == "/metal/v1/devices"


@pytest.mark.asyncio
async def test_normalize_path_fabric(spec_manager):
    """Test path normalization for Fabric API."""
    # Fabric API should use /fabric/v4 prefix
    normalized = await spec_manager._normalize_path("fabric", "/connections")
    assert normalized == "/fabric/v4/connections"
    
    # Already normalized path should remain unchanged
    normalized = await spec_manager._normalize_path("fabric", "/fabric/v4/connections")
    assert normalized == "/fabric/v4/connections"


@pytest.mark.asyncio
async def test_normalize_path_unknown(spec_manager):
    """Test path normalization for unknown API."""
    # Unknown API should return original path
    normalized = await spec_manager._normalize_path("unknown", "/test")
    assert normalized == "/test"


@pytest.mark.asyncio
async def test_create_overlay_template(spec_manager, tmp_path):
    """Test overlay template creation."""
    overlay_path = tmp_path / "test_overlay.yaml"
    
    await spec_manager._create_overlay_template("metal", overlay_path)
    
    assert overlay_path.exists()
    
    # Check content
    with open(overlay_path) as f:
        content = f.read()
        assert "Metal" in content
        assert "overlay: 1.0.0" in content


@pytest.mark.asyncio
async def test_apply_simple_overlay(spec_manager):
    """Test applying a simple overlay."""
    spec = {
        "info": {"title": "Original Title"},
        "servers": [{"url": "https://old.example.com"}]
    }
    
    overlay = {
        "actions": [
            {
                "target": "$.info.title",
                "update": "New Title"
            },
            {
                "target": "$.servers",
                "update": [{"url": "https://new.example.com"}]
            }
        ]
    }
    
    result = await spec_manager._apply_overlay(spec, overlay)
    
    assert result["info"]["title"] == "New Title"
    assert result["servers"] == [{"url": "https://new.example.com"}]


def test_overlay_files_exist():
    """Test that overlay files exist for all configured APIs."""
    config = Config.load("config/apis.yaml")
    
    for api_name in config.get_api_names():
        api_config = config.get_api_config(api_name)
        overlay_path = Path(api_config.overlay)
        assert overlay_path.exists(), f"Overlay file missing for {api_name}: {overlay_path}"