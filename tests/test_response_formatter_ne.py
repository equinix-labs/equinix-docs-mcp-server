import sys
from pathlib import Path

import pytest

# Ensure repo "src" is on path when running tests directly
sys.path.insert(0, str(Path(__file__).parent / "src"))

from equinix_mcp_server.config import APIConfig, Config
from equinix_mcp_server.response_formatter import ResponseFormatter


def test_network_edge_get_metros_format_lookup():
    """Ensure the ResponseFormatter finds the Network Edge getMetros format
    for multiple operation id variants (double-underscore, single underscore,
    and underscore-normalized forms). This test constructs a minimal in-memory
    Config so it doesn't depend on the repo's yaml file being present.
    """

    # Minimal fake format (content isn't executed in this test)
    fake_format = [".data | map(.metroCode)"]

    api_cfg = APIConfig(name="network-edge", format={"getMetrosUsingGET": fake_format})
    cfg = Config(apis={"network-edge": api_cfg})
    rf = ResponseFormatter(cfg)

    candidates = [
        "network-edge__getMetrosUsingGET",
        "network-edge_getMetrosUsingGET",
        "network_edge__getMetrosUsingGET",
    ]

    for op in candidates:
        fmt = rf._get_format_config(op)
        assert fmt is not None, f"Formatter did not find format for operation id: {op}"
        # We set the fake format as a list above
        assert isinstance(fmt, list), f"Expected list-format for {op}, got {type(fmt)}"
