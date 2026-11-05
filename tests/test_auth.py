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
                "EQUINIX_API_CLIENTID": "test_client_id",
                "EQUINIX_API_CLIENTSECRET": "test_client_secret",
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
                "EQUINIX_API_CLIENTID": "env_id",
                "EQUINIX_API_CLIENTSECRET": "env_secret",
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
    auth_manager._token_cache["test"] = ("token", None)
    assert len(auth_manager._token_cache) == 1

    auth_manager.clear_token_cache()
    assert len(auth_manager._token_cache) == 0


@pytest.mark.asyncio
async def test_unknown_service(auth_manager):
    """Test handling unknown service."""
    with pytest.raises(ValueError, match="Unknown service"):
        await auth_manager.get_auth_header("unknown_service")


def credentialed_manager(config):
    """An AuthManager with test credentials and no real token endpoint."""
    with (
        patch.dict(
            os.environ,
            {"EQUINIX_API_CLIENTID": "id", "EQUINIX_API_CLIENTSECRET": "secret"},
            clear=True,
        ),
        patch("equinix_docs_mcp_server.auth._load_equinix_yaml", return_value={}),
    ):
        return AuthManager(config)


@pytest.mark.asyncio
async def test_token_reused_until_near_expiry(config):
    """A cached token is refreshed shortly before its expires_in runs out."""
    am = credentialed_manager(config)
    am._get_access_token = AsyncMock(
        side_effect=[("first", 3600.0), ("second", 3600.0)]
    )

    with patch("equinix_docs_mcp_server.auth.time.monotonic", return_value=1000.0):
        assert await am.get_auth_header("fabric") == {"Authorization": "Bearer first"}
    # Still inside the lifetime minus the safety margin
    with patch("equinix_docs_mcp_server.auth.time.monotonic", return_value=4500.0):
        assert await am.get_auth_header("fabric") == {"Authorization": "Bearer first"}
    # Within TOKEN_EXPIRY_MARGIN of expiry
    with patch("equinix_docs_mcp_server.auth.time.monotonic", return_value=4560.0):
        assert await am.get_auth_header("fabric") == {"Authorization": "Bearer second"}
    assert am._get_access_token.await_count == 2


@pytest.mark.asyncio
async def test_token_without_lifetime_is_cached(config):
    """Without expires_in the token is kept; a 401 retry replaces it."""
    am = credentialed_manager(config)
    am._get_access_token = AsyncMock(return_value=("tok", None))

    await am.get_auth_header("fabric")
    await am.get_auth_header("fabric")

    am._get_access_token.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, expires_in",
    [
        ({"access_token": "t", "token_timeout": "3599"}, 3599.0),
        ({"access_token": "t", "expires_in": "3599"}, 3599.0),
        ({"access_token": "t", "expires_in": 3599}, 3599.0),
        ({"access_token": "t"}, None),
        ({"access_token": "t", "expires_in": "soon"}, None),
    ],
)
async def test_access_token_lifetime_parsed(config, body, expires_in):
    """The lifetime is read from token_timeout (Equinix) or expires_in."""
    import httpx2

    am = credentialed_manager(config)
    response = httpx2.Response(
        200, json=body, request=httpx2.Request("POST", "https://example.com")
    )
    with patch(
        "equinix_docs_mcp_server.auth.httpx2.AsyncClient.post",
        AsyncMock(return_value=response),
    ):
        assert await am._get_access_token() == ("t", expires_in)


def test_deprecated_credential_names_still_work(config, caplog):
    """EQUINIX_CLIENT_ID / _SECRET are read with a deprecation warning."""
    with (
        patch.dict(
            os.environ,
            {"EQUINIX_CLIENT_ID": "old_id", "EQUINIX_CLIENT_SECRET": "old_secret"},
            clear=True,
        ),
        patch("equinix_docs_mcp_server.auth._load_equinix_yaml", return_value={}),
        caplog.at_level("WARNING", logger="equinix_docs_mcp_server.auth"),
    ):
        am = AuthManager(config)
    assert (am.client_id, am.client_secret) == ("old_id", "old_secret")
    assert "EQUINIX_CLIENT_ID is deprecated; set EQUINIX_API_CLIENTID" in caplog.text
    assert "old_secret" not in caplog.text


def test_new_credential_names_win(config, caplog):
    """The Terraform-style names take priority over the deprecated ones."""
    with (
        patch.dict(
            os.environ,
            {
                "EQUINIX_API_CLIENTID": "new_id",
                "EQUINIX_API_CLIENTSECRET": "new_secret",
                "EQUINIX_CLIENT_ID": "old_id",
                "EQUINIX_CLIENT_SECRET": "old_secret",
            },
            clear=True,
        ),
        patch("equinix_docs_mcp_server.auth._load_equinix_yaml", return_value={}),
        caplog.at_level("WARNING", logger="equinix_docs_mcp_server.auth"),
    ):
        am = AuthManager(config)
    assert (am.client_id, am.client_secret) == ("new_id", "new_secret")
    assert "deprecated" not in caplog.text


class _TokenResponse:
    status_code = 200
    text = ""
    headers: dict = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {"access_token": "tok", "expires_in": 3600}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint, expected",
    [
        (None, "https://api.equinix.com/oauth2/v1/token"),
        (
            "https://mockapi.example.com/",
            "https://mockapi.example.com/oauth2/v1/token",
        ),
    ],
)
async def test_token_url_follows_endpoint(config, monkeypatch, endpoint, expected):
    """EQUINIX_API_ENDPOINT moves the token request too."""
    am = credentialed_manager(config)
    if endpoint:
        monkeypatch.setenv("EQUINIX_API_ENDPOINT", endpoint)
    else:
        monkeypatch.delenv("EQUINIX_API_ENDPOINT", raising=False)
    post = AsyncMock(return_value=_TokenResponse())
    with patch("equinix_docs_mcp_server.auth.httpx2.AsyncClient.post", post):
        assert await am._get_access_token() == ("tok", 3600)
    assert post.await_args.args[0] == expected
