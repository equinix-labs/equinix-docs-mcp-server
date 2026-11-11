"""Tests for the llms.txt page index, its sitemap fallback, and cache expiry."""

import os
import time
from unittest.mock import patch

import httpx2
import pytest

from equinix_docs_mcp_server.config import Config
from equinix_docs_mcp_server.docs import DocsManager, _category_name, _page_url

LLMS_URL = "https://docs.equinix.com/llms.txt"
SITEMAP_URL = "https://docs.equinix.com/sitemap.xml"
SEARCH_URL = "https://docs.equinix.com/search-index.json"

# Shaped like docs.equinix.com/llms.txt, including a multi-line description
LLMS_TXT = """# Equinix Product Documentation

- [Equinix product documentation](/index.md)

## api-catalog

- [Cross Connect (2.0)](/api-catalog/crossconnectsv2.md): A Cross Connect is a physical cable connection.
- [Equinix Messaging Gateway API](/api-catalog/emgv1.md): This AsyncAPI describes the gateway,
which provides near real-time notifications.

## fabric-cloud-router

- [Fabric Cloud Router](/fabric-cloud-router/index.md): Route traffic between clouds without hairpinning.
- [Search the documentation](/search.md)
"""

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.equinix.com/fabric/ports</loc><lastmod>2026-10-01</lastmod></url>
</urlset>
"""


class FakeSite:
    """Stand-in for httpx2.AsyncClient that serves fixed responses by URL."""

    def __init__(self, responses):
        self.responses = responses  # url -> text, or an exception to raise
        self.requests = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, url, **kwargs):
        self.requests.append(url)
        body = self.responses.get(url, httpx2.ConnectError("unreachable"))
        if isinstance(body, Exception):
            raise body
        return httpx2.Response(200, text=body, request=httpx2.Request("GET", url))


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUINIX_MCP_CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def manager(cache_dir):
    return DocsManager(Config.load())


def _site(responses):
    site = FakeSite(responses)
    return site, patch("equinix_docs_mcp_server.docs.httpx2.AsyncClient", site)


def _age(path, hours):
    stamp = time.time() - hours * 3600
    os.utime(path, (stamp, stamp))


@pytest.mark.parametrize(
    "heading, name",
    [
        ("api-catalog", "API Catalog"),
        ("fabric-cloud-router", "Fabric Cloud Router"),
        ("iam", "IAM"),
        ("AGENTS", "AGENTS"),
    ],
)
def test_category_name(heading, name):
    assert _category_name(heading) == name


@pytest.mark.parametrize(
    "link, url",
    [
        ("/fabric/ports.md", "https://docs.equinix.com/fabric/ports"),
        ("/index.md", "https://docs.equinix.com/"),
        (
            "/fabric-cloud-router/index.md",
            "https://docs.equinix.com/fabric-cloud-router/",
        ),
        ("https://docs.equinix.com/fabric.md", "https://docs.equinix.com/fabric"),
    ],
)
def test_page_url_drops_md(link, url):
    assert _page_url(link) == url


@pytest.mark.asyncio
async def test_parse_llms_txt(manager):
    await manager._parse_llms_txt(LLMS_TXT)
    docs = {d["title"]: d for d in manager.sitemap_cache}

    assert len(docs) == 5
    assert docs["Equinix product documentation"]["category"] == "General"

    xc = docs["Cross Connect (2.0)"]
    assert xc["category"] == "API Catalog"
    assert xc["url"] == "https://docs.equinix.com/api-catalog/crossconnectsv2"
    assert xc["description"] == "A Cross Connect is a physical cable connection."

    emg = docs["Equinix Messaging Gateway API"]
    assert emg["description"] == (
        "This AsyncAPI describes the gateway, which provides near real-time "
        "notifications."
    )

    assert docs["Fabric Cloud Router"]["category"] == "Fabric Cloud Router"
    assert docs["Search the documentation"]["description"] == ""


@pytest.mark.asyncio
async def test_index_prefers_llms_txt_and_caches_it(manager, cache_dir):
    site, patched = _site({LLMS_URL: LLMS_TXT, SITEMAP_URL: SITEMAP_XML})
    with patched:
        result = await manager.find_docs("notifications")

    assert site.requests == [LLMS_URL]
    assert "Equinix Messaging Gateway API" in result
    assert "near real-time notifications" in result
    assert (cache_dir / "docs" / "sitemap_cache.txt").read_text() == LLMS_TXT


@pytest.mark.asyncio
async def test_index_falls_back_to_sitemap(manager):
    site, patched = _site({SITEMAP_URL: SITEMAP_XML})
    with patched:
        result = await manager.list_docs()

    assert site.requests == [LLMS_URL, SITEMAP_URL]
    assert "https://docs.equinix.com/fabric/ports" in result


@pytest.mark.asyncio
async def test_empty_llms_txt_falls_back_to_sitemap(manager):
    site, patched = _site({LLMS_URL: "# Nothing here\n", SITEMAP_URL: SITEMAP_XML})
    with patched:
        await manager.list_docs()

    assert site.requests == [LLMS_URL, SITEMAP_URL]
    assert manager.sitemap_cache[0]["url"] == "https://docs.equinix.com/fabric/ports"


@pytest.mark.asyncio
async def test_fresh_cache_is_used_without_fetching(manager, cache_dir):
    cached = cache_dir / "docs" / "sitemap_cache.txt"
    cached.parent.mkdir(parents=True)
    cached.write_text(LLMS_TXT)

    site, patched = _site({})
    with patched:
        result = await manager.list_docs("cross connect")

    assert site.requests == []
    assert "Cross Connect (2.0)" in result


@pytest.mark.asyncio
async def test_stale_cache_is_refreshed(manager, cache_dir):
    cached = cache_dir / "docs" / "sitemap_cache.txt"
    cached.parent.mkdir(parents=True)
    cached.write_text("# Old\n\n## old\n\n- [Old Page](/old.md)\n")
    _age(cached, 25)

    site, patched = _site({LLMS_URL: LLMS_TXT})
    with patched:
        await manager.list_docs()

    assert site.requests == [LLMS_URL]
    assert cached.read_text() == LLMS_TXT
    assert "Old Page" not in {d["title"] for d in manager.sitemap_cache}


@pytest.mark.asyncio
async def test_stale_cache_is_kept_when_refresh_fails(manager, cache_dir):
    cached = cache_dir / "docs" / "sitemap_cache.txt"
    cached.parent.mkdir(parents=True)
    cached.write_text(LLMS_TXT)
    _age(cached, 25)

    site, patched = _site({})
    with patched:
        result = await manager.find_docs("cross connect")

    assert site.requests == [LLMS_URL, SITEMAP_URL]
    assert "Cross Connect (2.0)" in result


@pytest.mark.asyncio
async def test_no_cache_and_no_network_raises(manager):
    _, patched = _site({})
    with patched, pytest.raises(httpx2.ConnectError):
        await manager.list_docs()


@pytest.mark.asyncio
async def test_newest_cache_wins(manager, cache_dir):
    """A sitemap fetched after llms.txt failed is newer than the old llms.txt."""
    docs_dir = cache_dir / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "sitemap_cache.txt").write_text(LLMS_TXT)
    _age(docs_dir / "sitemap_cache.txt", 30)
    (docs_dir / "sitemap_cache.xml").write_text(SITEMAP_XML)

    _, patched = _site({})
    with patched:
        await manager.list_docs()

    assert [d["url"] for d in manager.sitemap_cache] == [
        "https://docs.equinix.com/fabric/ports"
    ]


@pytest.mark.asyncio
async def test_zero_max_age_never_refreshes(cache_dir):
    config = Config.load()
    config.docs.cache_max_age_hours = 0
    manager = DocsManager(config)
    cached = cache_dir / "docs" / "sitemap_cache.txt"
    cached.parent.mkdir(parents=True)
    cached.write_text(LLMS_TXT)
    _age(cached, 24 * 365)

    site, patched = _site({})
    with patched:
        await manager.list_docs()

    assert site.requests == []


@pytest.mark.asyncio
async def test_in_memory_index_reloads_after_max_age(manager, cache_dir):
    site, patched = _site({LLMS_URL: LLMS_TXT})
    with patched:
        await manager.list_docs()
        await manager.list_docs()
        assert site.requests == [LLMS_URL]

        manager._index_loaded_at = time.time() - 25 * 3600
        _age(cache_dir / "docs" / "sitemap_cache.txt", 25)
        await manager.list_docs()

    assert site.requests == [LLMS_URL, LLMS_URL]


@pytest.mark.asyncio
async def test_summary_names_the_source(manager):
    _, patched = _site({LLMS_URL: LLMS_TXT})
    with patched:
        result = await manager.get_docs_summary()

    assert "Total documents: 5" in result
    assert "**API Catalog**: 2 documents" in result
    assert "Source: sitemap_cache.txt, last updated" in result


@pytest.mark.asyncio
async def test_stale_search_index_is_refreshed(manager, cache_dir):
    index = cache_dir / "search" / "search-index.json"
    index.parent.mkdir(parents=True)
    index.write_text("[]")
    _age(index, 25)

    site, patched = _site({SEARCH_URL: "[]"})
    with patched:
        result = await manager.search_docs("ports")

    assert site.requests == [SEARCH_URL]
    assert "No search results found" in result


@pytest.mark.asyncio
async def test_stale_search_index_is_kept_when_refresh_fails(manager, cache_dir):
    index = cache_dir / "search" / "search-index.json"
    index.parent.mkdir(parents=True)
    index.write_text("[]")
    _age(index, 25)

    site, patched = _site({})
    with patched:
        result = await manager.search_docs("ports")

    assert site.requests == [SEARCH_URL]
    assert "No search results found" in result


PAGES = [
    {
        "url": "https://docs.equinix.com/customer-portal/",
        "title": "About the Customer Portal",
        "category": "Customer Portal",
        "description": "Manage products, orders and account information.",
    },
    {
        "url": "https://docs.equinix.com/fabric/ports/fabric-port-utilization",
        "title": "Monitoring Port Utilization",
        "category": "Fabric",
        "description": "Bandwidth usage graphs for your ports.",
    },
    {
        "url": "https://docs.equinix.com/fabric/ports/creating-ports",
        "title": "Creating Ports",
        "category": "Fabric",
        "description": "Order a new Fabric port.",
    },
    {
        "url": "https://docs.equinix.com/fabric/managing-connections/fabric-connection-traffic",
        "title": "Monitoring Connection Utilization",
        "category": "Fabric",
        "description": "Traffic statistics for a virtual connection.",
    },
    {
        "url": "https://docs.equinix.com/network-edge/vendors-devices/",
        "title": "Supported Vendors",
        "category": "Network Edge",
        "description": "Port counts and licensing for each device type.",
    },
]


def _with_pages(manager, pages):
    manager.sitemap_cache = [dict(p, lastmod="") for p in pages]
    manager._index_loaded_at = time.time()


def _titles(result):
    return [
        line[2:].split("**", 1)[0]
        for line in result.splitlines()
        if line.startswith("**")
    ]


@pytest.mark.asyncio
async def test_find_docs_matches_whole_words(manager):
    """'port' must not find 'Portal' (the substring matcher did)."""
    _with_pages(manager, PAGES)

    titles = _titles(await manager.find_docs("port"))

    assert "About the Customer Portal" not in titles
    assert "Monitoring Port Utilization" in titles
    assert "Creating Ports" in titles  # same stem


@pytest.mark.asyncio
async def test_find_docs_ranks_titles_above_descriptions(manager):
    _with_pages(manager, PAGES)

    titles = _titles(await manager.find_docs("port utilization"))

    assert titles[0] == "Monitoring Port Utilization"
    # Mentions "port" only in its description, so it ranks below title hits
    assert titles.index("Supported Vendors") > titles.index("Creating Ports")


@pytest.mark.asyncio
async def test_find_docs_matches_category_and_url_path(manager):
    _with_pages(manager, PAGES)

    assert _titles(await manager.find_docs("network edge")) == ["Supported Vendors"]
    assert _titles(await manager.find_docs("managing connections"))[0] == (
        "Monitoring Connection Utilization"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "+++", "the", "kubernetes"])
async def test_find_docs_no_match(manager, query):
    _with_pages(manager, PAGES)

    assert "No documentation found" in await manager.find_docs(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ['"port"', "title:port", "-port", "port~2"])
async def test_find_docs_ignores_query_syntax(manager, query):
    _with_pages(manager, PAGES)

    assert "Monitoring Port Utilization" in _titles(await manager.find_docs(query))


@pytest.mark.asyncio
async def test_list_docs_filter_uses_word_matching(manager):
    _with_pages(manager, PAGES)

    result = await manager.list_docs("ports")

    assert "Creating Ports" in result
    assert "Customer Portal" not in result


@pytest.mark.asyncio
async def test_page_index_rebuilds_when_index_reloads(manager):
    _with_pages(manager, PAGES)
    await manager.find_docs("port")

    _with_pages(manager, [dict(PAGES[0], title="Port Reference")])

    assert _titles(await manager.find_docs("port")) == ["Port Reference"]


@pytest.mark.asyncio
async def test_list_docs_filter_orders_categories_by_best_match(manager):
    _with_pages(manager, PAGES)

    result = await manager.list_docs("port utilization")
    headings = [line for line in result.splitlines() if line.startswith("## ")]

    # Fabric holds the best match; Network Edge only mentions "port" in a
    # description and is not listed A-Z first.
    assert headings[0] == "## Fabric"


@pytest.mark.asyncio
async def test_list_docs_filter_drops_weak_matches(manager):
    pages = PAGES + [
        {
            "url": f"https://docs.equinix.com/misc/page-{n}",
            "title": f"Misc {n}",
            "category": "Misc",
            "description": "Mentions utilization once among many other words "
            + " ".join(f"filler{m}" for m in range(40)),
        }
        for n in range(5)
    ]
    _with_pages(manager, pages)

    result = await manager.list_docs("port utilization")

    assert "Monitoring Port Utilization" in result
    assert "Misc 0" not in result
