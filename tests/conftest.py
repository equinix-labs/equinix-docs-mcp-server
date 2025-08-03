"""Test fixtures and configuration."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def test_environment():
    """Set up test environment."""
    # Ensure we're in the right directory
    original_cwd = os.getcwd()
    repo_root = Path(__file__).parent.parent
    os.chdir(repo_root)

    yield

    # Cleanup
    os.chdir(original_cwd)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_env_vars():
    """Mock environment variables for testing."""
    return {
        "EQUINIX_CLIENT_ID": "test_client_id",
        "EQUINIX_CLIENT_SECRET": "test_client_secret",
        "EQUINIX_METAL_TOKEN": "test_metal_token",
    }
