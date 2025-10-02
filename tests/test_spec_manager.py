"""Test spec manager functionality."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from equinix_docs_mcp_server.config import Config
from equinix_docs_mcp_server.spec_manager import SpecManager


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
    assert spec_manager.overlay_manager is not None


@pytest.mark.asyncio
async def test_create_overlay_template(spec_manager, tmp_path):
    """Test overlay template creation."""
    overlay_path = tmp_path / "test_overlay.yaml"

    await spec_manager.overlay_manager.create_overlay_template(
        str(overlay_path), "metal", "metal"
    )

    assert overlay_path.exists()

    # Check content
    with open(overlay_path) as f:
        content = f.read()
        assert "Metal" in content
        assert "overlay: 1.0.0" in content


def test_apply_simple_overlay(spec_manager):
    """Test applying a simple overlay."""
    spec = {
        "info": {"title": "Original Title"},
        "servers": [{"url": "https://old.example.com"}],
    }

    overlay = {
        "actions": [
            {"target": "$.info.title", "update": "New Title"},
            {"target": "$.servers", "update": [{"url": "https://new.example.com"}]},
        ]
    }

    result = spec_manager.overlay_manager.apply(spec, "test", overlay)

    assert result["info"]["title"] == "New Title"
    assert result["servers"] == [{"url": "https://new.example.com"}]


def test_overlay_files_exist(config):
    """Test that overlay files exist for all configured APIs."""
    for api_name in config.get_api_names():
        api_config = config.get_api_config(api_name)
        if not api_config.specs:
            continue
        for spec in api_config.specs:
            if spec.overlay:
                overlay_path = Path(spec.overlay)
                assert (
                    overlay_path.exists()
                ), f"Overlay file missing for {api_name}: {overlay_path}"
