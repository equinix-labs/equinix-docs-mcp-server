"""Test fixtures and configuration."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Test this checkout's sources: an editable install in a shared venv points
# at whichever worktree ran `pip install -e .`, so prepend the local src.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


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


@pytest.fixture(autouse=True)
def production_endpoint(monkeypatch):
    """Tests expect api.equinix.com, whatever the developer's shell sets."""
    monkeypatch.delenv("EQUINIX_API_ENDPOINT", raising=False)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_env_vars():
    """Mock environment variables for testing."""
    return {
        "EQUINIX_API_CLIENTID": "test_client_id",
        "EQUINIX_API_CLIENTSECRET": "test_client_secret",
    }
