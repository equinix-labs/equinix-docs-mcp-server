"""Authentication management for Equinix APIs."""

import base64
import os
from typing import Dict, Optional

import httpx
from pydantic import BaseModel

from .config import Config


class AuthManager:
    """Manages authentication for different Equinix APIs."""

    def __init__(self, config: Config):
        """Initialize with configuration."""
        self.config = config
        self._token_cache: Dict[str, str] = {}

        # Get credentials from environment
        self.client_id = os.getenv("EQUINIX_CLIENT_ID")
        self.client_secret = os.getenv("EQUINIX_CLIENT_SECRET")
        self.metal_token = os.getenv("EQUINIX_METAL_TOKEN")

    async def get_auth_header(self, service_name: str) -> Dict[str, str]:
        """Get authentication header for a service."""
        api_config = self.config.get_api_config(service_name)
        if not api_config:
            raise ValueError(f"Unknown service: {service_name}")

        auth_type = api_config.auth_type

        if auth_type == "metal_token":
            return await self._get_metal_auth_header()
        elif auth_type == "client_credentials":
            return await self._get_client_credentials_auth_header()
        else:
            raise ValueError(f"Unknown auth type: {auth_type}")

    async def _get_metal_auth_header(self) -> Dict[str, str]:
        """Get Metal API authentication header."""
        if not self.metal_token:
            raise ValueError(
                "EQUINIX_METAL_TOKEN environment variable is required for Metal API"
            )

        header_name = self.config.auth.metal_token.get("header_name", "X-Auth-Token")
        return {header_name: self.metal_token}

    async def _get_client_credentials_auth_header(self) -> Dict[str, str]:
        """Get Client Credentials authentication header."""
        if not self.client_id or not self.client_secret:
            raise ValueError(
                "EQUINIX_CLIENT_ID and EQUINIX_CLIENT_SECRET environment variables are required"
            )

        # Check cache first
        cache_key = f"{self.client_id}:{self.client_secret}"
        if cache_key in self._token_cache:
            return {"Authorization": f"Bearer {self._token_cache[cache_key]}"}

        # Get new token
        token = await self._get_access_token()
        self._token_cache[cache_key] = token

        return {"Authorization": f"Bearer {token}"}

    async def _get_access_token(self) -> str:
        """Get access token using client credentials flow."""
        token_url = self.config.auth.client_credentials.get(
            "token_url", "https://api.equinix.com/oauth2/v1/token"
        )

        # Equinix uses JSON body for client credentials flow (non-standard)
        headers = {
            "Content-Type": "application/json",
        }

        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, headers=headers, json=data)
            response.raise_for_status()

            token_data = response.json()
            return token_data["access_token"]

    def clear_token_cache(self) -> None:
        """Clear the token cache."""
        self._token_cache.clear()
