"""Authentication management for Equinix APIs."""

import base64
import logging
import os
from pathlib import Path
from typing import Dict, Optional

import httpx2
import yaml
from pydantic import BaseModel

from .config import Config

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


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


def _load_metal_yaml() -> dict:
    """Load ~/.config/equinix/metal.yaml if it exists (metal-cli config)."""
    path = Path.home() / ".config" / "equinix" / "metal.yaml"
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
        self._token_cache: Dict[str, str] = {}

        # Resolve credentials: env vars take priority, then equinix.yaml,
        # then metal.yaml (for metal token only) — matching equinix-cli/cmd/root.go
        equinix_cfg = _load_equinix_yaml()

        self.client_id = os.getenv("EQUINIX_CLIENT_ID") or equinix_cfg.get(
            "equinix_client_id"
        )
        self.client_secret = os.getenv("EQUINIX_CLIENT_SECRET") or equinix_cfg.get(
            "equinix_client_secret"
        )

        metal_token = os.getenv("EQUINIX_METAL_TOKEN") or equinix_cfg.get(
            "metal_auth_token"
        )
        if not metal_token:
            metal_token = _load_metal_yaml().get("token")
        self.metal_token = metal_token

        # Log credential availability (without exposing values)
        logger.info(f"AuthManager initialized:")
        logger.info(f"  - CLIENT_ID available: {bool(self.client_id)}")
        logger.info(f"  - CLIENT_SECRET available: {bool(self.client_secret)}")
        logger.info(f"  - METAL_TOKEN available: {bool(self.metal_token)}")
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

        if auth_type == "metal_token":
            return await self._get_metal_auth_header()
        elif auth_type == "client_credentials":
            return await self._get_client_credentials_auth_header()
        else:
            logger.error(f"Unknown auth type: {auth_type}")
            raise ValueError(f"Unknown auth type: {auth_type}")

    async def _get_metal_auth_header(self) -> Dict[str, str]:
        """Get Metal API authentication header."""
        logger.debug("Getting Metal auth header")

        if not self.metal_token:
            logger.error(
                "Metal token not found. Set EQUINIX_METAL_TOKEN, or add metal_auth_token "
                "to ~/.config/equinix/equinix.yaml, or token to ~/.config/equinix/metal.yaml"
            )
            raise ValueError(
                "Metal token not found. Set EQUINIX_METAL_TOKEN, or add metal_auth_token "
                "to ~/.config/equinix/equinix.yaml, or token to ~/.config/equinix/metal.yaml"
            )

        header_name = self.config.auth.metal_token.get("header_name", "X-Auth-Token")
        logger.debug(f"Using Metal token header: {header_name}")
        return {header_name: self.metal_token}

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
        if cache_key in self._token_cache:
            logger.debug("Using cached access token")
            token = self._token_cache[cache_key]
            # Log token info without exposing the full token
            logger.debug(f"Cached token length: {len(token)}")
            logger.debug(f"Cached token starts with: {token[:10]}...")
            return {"Authorization": f"Bearer {token}"}

        # Get new token
        logger.debug("Fetching new access token")
        token = await self._get_access_token()
        self._token_cache[cache_key] = token

        # Log token info without exposing the full token
        logger.debug(f"New token length: {len(token)}")
        logger.debug(f"New token starts with: {token[:10]}...")
        logger.info("Successfully obtained and cached new access token")

        return {"Authorization": f"Bearer {token}"}

    async def _get_access_token(self) -> str:
        """Get access token using client credentials flow."""
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
                logger.info("Successfully obtained access token from Equinix")
                return access_token

            except Exception as e:
                logger.error(f"Error getting access token: {e}")
                raise

    def clear_token_cache(self) -> None:
        """Clear the token cache."""
        logger.info("Clearing token cache")
        self._token_cache.clear()
