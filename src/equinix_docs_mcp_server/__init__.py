"""Package initialization for equinix_docs_mcp_server."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Set from the repository's version file at build time
    __version__ = version("equinix-docs-mcp-server")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"
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
