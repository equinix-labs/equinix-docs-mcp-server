"""Authentication management for Equinix APIs."""

import base64
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx2
import yaml
from pydantic import BaseModel

from .config import Config

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Refresh tokens this many seconds before they expire, so a token isn't
# sent just as it lapses
TOKEN_EXPIRY_MARGIN = 60


def _load_equinix_yaml() -> dict:
    """Load ~/.config/equinix/equinix.yaml if it exists."""
    path = Path.home() / ".config" / "equinix" / "equinix.yaml"
    if path.exists():
        try:
            with open(path) as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.debug(f"Could not read {path}: {e}")
    return {}


class AuthManager:
    """Manages authentication for different Equinix APIs."""

    def __init__(self, config: Config):
        """Initialize with configuration."""
        self.config = config
        # Token and its expiry (time.monotonic()), or None if the token
        # response gave no lifetime
        self._token_cache: Dict[str, Tuple[str, Optional[float]]] = {}

        # Resolve credentials: env vars take priority, then equinix.yaml —
        # matching equinix-cli/cmd/root.go
        equinix_cfg = _load_equinix_yaml()

        self.client_id = os.getenv("EQUINIX_CLIENT_ID") or equinix_cfg.get(
            "equinix_client_id"
        )
        self.client_secret = os.getenv("EQUINIX_CLIENT_SECRET") or equinix_cfg.get(
            "equinix_client_secret"
        )

        # Log credential availability (without exposing values)
        logger.info(f"AuthManager initialized:")
        logger.info(f"  - CLIENT_ID available: {bool(self.client_id)}")
        logger.info(f"  - CLIENT_SECRET available: {bool(self.client_secret)}")
        if self.client_id:
            logger.info(f"  - CLIENT_ID length: {len(self.client_id)}")
        if self.client_secret:
            logger.info(f"  - CLIENT_SECRET length: {len(self.client_secret)}")

    async def get_auth_header(self, service_name: str) -> Dict[str, str]:
        """Get authentication header for a service."""
        logger.debug(f"Getting auth header for service: {service_name}")

        api_config = self.config.get_api_config(service_name)
        if not api_config:
            logger.error(f"Unknown service: {service_name}")
            raise ValueError(f"Unknown service: {service_name}")

        auth_type = api_config.auth_type
        logger.debug(f"Service {service_name} uses auth type: {auth_type}")

        if auth_type == "client_credentials":
            return await self._get_client_credentials_auth_header()
        else:
            logger.error(f"Unknown auth type: {auth_type}")
            raise ValueError(f"Unknown auth type: {auth_type}")

    async def _get_client_credentials_auth_header(self) -> Dict[str, str]:
        """Get Client Credentials authentication header."""
        logger.debug("Getting client credentials auth header")

        if not self.client_id or not self.client_secret:
            logger.error(
                "Client credentials not found. Set EQUINIX_CLIENT_ID / EQUINIX_CLIENT_SECRET, "
                "or add equinix_client_id / equinix_client_secret to ~/.config/equinix/equinix.yaml"
            )
            raise ValueError(
                "Client credentials not found. Set EQUINIX_CLIENT_ID / EQUINIX_CLIENT_SECRET, "
                "or add equinix_client_id / equinix_client_secret to ~/.config/equinix/equinix.yaml"
            )

        # Check cache first
        cache_key = f"{self.client_id}:{self.client_secret}"
        cached = self._token_cache.get(cache_key)
        if cached:
            token, expires_at = cached
            if expires_at is None or time.monotonic() < expires_at:
                logger.debug("Using cached access token")
                # Log token info without exposing the full token
                logger.debug(f"Cached token length: {len(token)}")
                logger.debug(f"Cached token starts with: {token[:10]}...")
                return {"Authorization": f"Bearer {token}"}
            logger.info("Cached access token is about to expire; refreshing")

        # Get new token
        logger.debug("Fetching new access token")
        token, expires_in = await self._get_access_token()
        expires_at = (
            time.monotonic() + max(expires_in - TOKEN_EXPIRY_MARGIN, 0)
            if expires_in is not None
            else None
        )
        self._token_cache[cache_key] = (token, expires_at)

        # Log token info without exposing the full token
        logger.debug(f"New token length: {len(token)}")
        logger.debug(f"New token starts with: {token[:10]}...")
        logger.info("Successfully obtained and cached new access token")

        return {"Authorization": f"Bearer {token}"}

    async def _get_access_token(self) -> Tuple[str, Optional[float]]:
        """Get an access token and its lifetime in seconds (None if not given)
        using the client credentials flow."""
        token_url = self.config.auth.client_credentials.get(
            "token_url", "https://api.equinix.com/oauth2/v1/token"
        )

        logger.info(f"Requesting access token from: {token_url}")

        # Equinix uses JSON body for client credentials flow (non-standard)
        headers = {
            "Content-Type": "application/json",
        }

        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        logger.debug(f"Token request headers: {headers}")
        logger.debug(
            f"Token request data: {{'grant_type': 'client_credentials', 'client_id': '{self.client_id}', 'client_secret': '[REDACTED]'}}"
        )

        async with httpx2.AsyncClient() as client:
            try:
                logger.debug("Sending token request...")
                response = await client.post(token_url, headers=headers, json=data)

                logger.debug(f"Token response status: {response.status_code}")
                logger.debug(f"Token response headers: {dict(response.headers)}")

                if response.status_code != 200:
                    logger.error(
                        f"Token request failed with status {response.status_code}"
                    )
                    logger.error(f"Response body: {response.text}")

                response.raise_for_status()

                token_data = response.json()
                logger.debug(f"Token response keys: {list(token_data.keys())}")

                if "access_token" not in token_data:
                    logger.error(f"No access_token in response: {token_data}")
                    raise ValueError("No access_token in response")

                access_token = token_data["access_token"]
                # Equinix returns the lifetime as token_timeout, a string such
                # as "3599", rather than the standard expires_in
                lifetime = token_data.get("token_timeout", token_data.get("expires_in"))
                try:
                    expires_in: Optional[float] = float(lifetime)
                except (TypeError, ValueError):
                    expires_in = None
                logger.info("Successfully obtained access token from Equinix")
                return access_token, expires_in

            except Exception as e:
                logger.error(f"Error getting access token: {e}")
                raise

    def clear_token_cache(self) -> None:
        """Clear the token cache."""
        logger.info("Clearing token cache")
        self._token_cache.clear()
