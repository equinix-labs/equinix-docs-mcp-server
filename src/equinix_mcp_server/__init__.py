"""Package initialization for equinix_docs_mcp_server."""

__version__ = "0.1.0"
__author__ = "displague"
__email__ = "displague@example.com"
__description__ = "MCP Server for agentive tasks against Equinix APIs"

from .auth import AuthManager
from .config import Config
from .docs import DocsManager

__all__ = [
    "Config",
    "AuthManager",
    "DocsManager",
]
