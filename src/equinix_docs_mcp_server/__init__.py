"""Package initialization for equinix_docs_mcp_server."""

__version__ = "0.1.0"
__author__ = "Equinix Labs"
__description__ = "Equinix Docs and API specifications MCP Server (experimental)"

from .auth import AuthManager
from .config import Config
from .docs import DocsManager

__all__ = [
    "Config",
    "AuthManager",
    "DocsManager",
]
