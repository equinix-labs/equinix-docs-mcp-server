"""Snapshot the text search_tools ranks, for the ranking regression test.

Writes one entry per tool that search_tools can return (every catalog tool
except the pinned docs tools and call_api_filtered): its name, title,
description and tags. tests/test_search_ranking.py rebuilds the search index
from this file, so ranking changes are tested against the real catalog
without network access or a spec cache.

Usage:
    python scripts/export_search_corpus.py [--refresh] [OUT]

OUT defaults to tests/fixtures/search_corpus.json. --refresh downloads the
specs first and leaves out families the API catalog no longer lists, as the
OpenRPC export does; without it the local spec cache is used.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from equinix_docs_mcp_server.main import (  # noqa: E402
    DOCS_TOOL_NAMES,
    FILTER_TOOL_NAME,
    EquinixMCPServer,
)
from equinix_docs_mcp_server.tool_search import tool_title  # noqa: E402

DEFAULT_OUT = ROOT / "tests" / "fixtures" / "search_corpus.json"
PINNED = {*DOCS_TOOL_NAMES, FILTER_TOOL_NAME}


async def export(out: Path, refresh: bool) -> int:
    server = EquinixMCPServer(None, tool_catalog="full")
    if refresh:
        from equinix_docs_mcp_server.catalog_discovery import (
            discover_catalog_apis,
            uncataloged_families,
        )

        catalog = await discover_catalog_apis()
        for family in uncataloged_families(server.config, catalog):
            server.config.apis[family].enabled = False
    await server.initialize(force_update_specs=refresh)
    assert server.mcp is not None

    corpus = [
        {
            "name": tool.name,
            "title": tool_title(tool),
            "description": tool.description or "",
            "tags": sorted(tool.tags or ()),
        }
        for tool in await server.mcp.list_tools()
        if tool.name not in PINNED
    ]
    corpus.sort(key=lambda entry: entry["name"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(corpus, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return len(corpus)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", nargs="?", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--refresh", action="store_true", help="download the specs first"
    )
    args = parser.parse_args()
    # The server logs every spec and request at INFO and below
    logging.disable(logging.WARNING)
    count = asyncio.run(export(args.out, args.refresh))
    print(f"Wrote {count} tools to {args.out}")


if __name__ == "__main__":
    main()
