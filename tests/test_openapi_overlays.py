"""Test OpenAPI overlay functionality."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from equinix_docs_mcp_server.config import Config
from equinix_docs_mcp_server.openapi_overlays import OverlayManager


@pytest.fixture
def config():
    """Load test configuration."""
    return Config.load("config/apis.yaml")


@pytest.fixture
def overlay_manager(config):
    """Create overlay manager instance."""
    return OverlayManager(config)


def test_overlay_manager_init(overlay_manager):
    """Test OverlayManager initialization."""
    assert overlay_manager is not None
    assert isinstance(overlay_manager.overlays_cache, dict)


@pytest.mark.asyncio
async def test_load_overlay_nonexistent(overlay_manager, tmp_path):
    """Test loading a non-existent overlay file."""
    overlay_path = tmp_path / "nonexistent.yaml"

    result = await overlay_manager.load_overlay(str(overlay_path))

    assert result is None
    assert str(overlay_path) not in overlay_manager.overlays_cache


@pytest.mark.asyncio
async def test_load_overlay_existing(overlay_manager, tmp_path):
    """Test loading an existing overlay file."""
    overlay_path = tmp_path / "test_overlay.yaml"

    # Create a test overlay file
    overlay_content = """
overlay: "1.0.0"
info:
  title: "Test Overlay"
  version: "1.0.0"
actions:
  - target: "$.info.title"
    update: "Updated Title"
"""
    overlay_path.write_text(overlay_content)

    result = await overlay_manager.load_overlay(str(overlay_path))

    assert result is not None
    assert result["overlay"] == "1.0.0"
    assert result["info"]["title"] == "Test Overlay"
    assert str(overlay_path) in overlay_manager.overlays_cache


@pytest.mark.asyncio
async def test_create_overlay_template_basic(overlay_manager, tmp_path):
    """Test creating a basic overlay template."""
    overlay_path = tmp_path / "basic_overlay.yaml"

    await overlay_manager.create_overlay_template(str(overlay_path), "fabric", "fabric")

    assert overlay_path.exists()

    # Load and verify content
    content = overlay_path.read_text()
    assert "overlay: 1.0.0" in content
    assert "Equinix Fabric API" in content
    assert "https://api.equinix.com" in content


@pytest.mark.asyncio
async def test_create_overlay_template_metal(overlay_manager, tmp_path):
    """Test creating overlay template for Metal API with special handling."""
    overlay_path = tmp_path / "metal_overlay.yaml"

    await overlay_manager.create_overlay_template(str(overlay_path), "metal", "metal")

    assert overlay_path.exists()

    # Load and verify content
    content = overlay_path.read_text()
    assert "overlay: 1.0.0" in content
    assert "Equinix Metal API" in content


def test_apply_overlay_simple(overlay_manager):
    """Test applying a simple overlay to a spec."""
    spec = {
        "info": {"title": "Original Title", "version": "1.0.0"},
        "servers": [{"url": "https://old.example.com"}],
        "paths": {},
    }

    overlay = {
        "actions": [
            {"target": "$.info.title", "update": "New Title"},
            {"target": "$.servers", "update": [{"url": "https://new.example.com"}]},
        ]
    }

    result = overlay_manager.apply(spec, "test", overlay)

    assert result["info"]["title"] == "New Title"
    assert result["info"]["version"] == "1.0.0"  # Unchanged
    assert result["servers"] == [{"url": "https://new.example.com"}]
    assert "paths" in result  # Unchanged


def test_apply_overlay_no_actions(overlay_manager):
    """Test applying an overlay with no actions."""
    spec = {
        "info": {"title": "Original Title"},
        "servers": [{"url": "https://example.com"}],
    }

    overlay = {"actions": []}

    result = overlay_manager.apply(spec, "test", overlay)

    # Should be unchanged
    assert result["info"]["title"] == "Original Title"
    assert result["servers"] == [{"url": "https://example.com"}]


def test_apply_overlay_missing_target(overlay_manager):
    """Test applying an overlay to a spec that doesn't have the target."""
    spec = {"paths": {}}  # No info section

    overlay = {"actions": [{"target": "$.info.title", "update": "New Title"}]}

    result = overlay_manager.apply(spec, "test", overlay)

    # Should create the missing intermediate node and set the value
    assert "info" in result
    assert result["info"]["title"] == "New Title"
    assert "paths" in result


def test_get_cached_overlay(overlay_manager):
    """Test getting cached overlay."""
    # Add something to cache
    test_overlay = {"overlay": "1.0.0"}
    overlay_manager.overlays_cache["test_path"] = test_overlay

    result = overlay_manager.get_cached_overlay("test_path")
    assert result == test_overlay

    # Test non-existent path
    result = overlay_manager.get_cached_overlay("nonexistent_path")
    assert result is None


def test_clear_cache(overlay_manager):
    """Test clearing the overlay cache."""
    # Add something to cache
    overlay_manager.overlays_cache["test_path"] = {"overlay": "1.0.0"}
    assert len(overlay_manager.overlays_cache) == 1

    overlay_manager.clear_cache()
    assert len(overlay_manager.overlays_cache) == 0
