"""Test documentation manager functionality."""

from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest

from equinix_mcp_server.config import Config
from equinix_mcp_server.docs import DocsManager


@pytest.fixture
def config():
    """Load test configuration."""
    return Config.load("config/apis.yaml")


@pytest.fixture
def docs_manager(config):
    """Create docs manager instance."""
    return DocsManager(config)


def test_docs_manager_init(docs_manager):
    """Test DocsManager initialization."""
    assert docs_manager is not None
    assert docs_manager.config is not None
    assert isinstance(docs_manager.sitemap_cache, list)


def test_extract_title_from_url(docs_manager):
    """Test URL title extraction."""
    # Test basic URL
    title = docs_manager._extract_title_from_url(
        "https://docs.equinix.com/metal/getting-started"
    )
    assert title == "Getting Started"

    # Test URL with dashes
    title = docs_manager._extract_title_from_url(
        "https://docs.equinix.com/api-catalog/metalv1"
    )
    assert title == "Metalv1"

    # Test root URL
    title = docs_manager._extract_title_from_url("https://docs.equinix.com/")
    assert title == "Home"


def test_categorize_url(docs_manager):
    """Test URL categorization."""
    # Test API catalog URLs
    category = docs_manager._categorize_url(
        "https://docs.equinix.com/api-catalog/metalv1"
    )
    assert category == "API"

    # Test Metal URLs
    category = docs_manager._categorize_url("https://docs.equinix.com/metal/overview")
    assert category == "Metal"

    # Test Fabric URLs
    category = docs_manager._categorize_url(
        "https://docs.equinix.com/fabric/getting-started"
    )
    assert category == "Fabric"

    # Test getting started URLs (without service prefix)
    category = docs_manager._categorize_url(
        "https://docs.equinix.com/getting-started/overview"
    )
    assert category == "Getting Started"

    # Test general URLs
    category = docs_manager._categorize_url("https://docs.equinix.com/general/overview")
    assert category == "General"


@pytest.mark.asyncio
async def test_parse_sitemap(docs_manager):
    """Test sitemap parsing."""
    sample_sitemap = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url>
            <loc>https://docs.equinix.com/metal/getting-started</loc>
            <lastmod>2023-12-01</lastmod>
            <changefreq>weekly</changefreq>
            <priority>0.8</priority>
        </url>
        <url>
            <loc>https://docs.equinix.com/fabric/overview</loc>
            <lastmod>2023-12-02</lastmod>
        </url>
    </urlset>"""

    await docs_manager._parse_sitemap(sample_sitemap)

    assert len(docs_manager.sitemap_cache) == 2

    first_doc = docs_manager.sitemap_cache[0]
    assert first_doc["url"] == "https://docs.equinix.com/metal/getting-started"
    assert first_doc["title"] == "Getting Started"
    assert first_doc["category"] == "Metal"
    assert first_doc["lastmod"] == "2023-12-01"

    second_doc = docs_manager.sitemap_cache[1]
    assert second_doc["url"] == "https://docs.equinix.com/fabric/overview"
    assert second_doc["category"] == "Fabric"


@pytest.mark.asyncio
async def test_list_docs_with_filter(docs_manager):
    """Test listing docs with filtering."""
    # Setup sample data
    docs_manager.sitemap_cache = [
        {
            "url": "https://docs.equinix.com/metal/getting-started",
            "title": "Getting Started",
            "category": "Metal",
            "lastmod": "2023-12-01",
        },
        {
            "url": "https://docs.equinix.com/fabric/overview",
            "title": "Fabric Overview",
            "category": "Fabric",
            "lastmod": "2023-12-02",
        },
    ]

    # Test without filter
    result = await docs_manager.list_docs()
    assert "Metal" in result
    assert "Fabric" in result
    assert "Getting Started" in result

    # Test with filter
    result = await docs_manager.list_docs("metal")
    assert "Getting Started" in result
    assert "Fabric Overview" not in result


@pytest.mark.asyncio
async def test_find_docs(docs_manager):
    """Test documentation find (filename-based search)."""
    # Setup sample data
    docs_manager.sitemap_cache = [
        {
            "url": "https://docs.equinix.com/metal/getting-started",
            "title": "Getting Started with Metal",
            "category": "Metal",
            "lastmod": "2023-12-01",
        },
        {
            "url": "https://docs.equinix.com/fabric/overview",
            "title": "Fabric Overview",
            "category": "Fabric",
            "lastmod": "2023-12-02",
        },
    ]

    # Test find
    result = await docs_manager.find_docs("metal")
    assert "Getting Started with Metal" in result
    assert "Fabric Overview" not in result

    # Test no results
    result = await docs_manager.find_docs("nonexistent")
    assert "No documentation found" in result


@pytest.mark.asyncio
@patch("equinix_mcp_server.docs.httpx.AsyncClient")
@patch("equinix_mcp_server.docs.aiofiles.open")
@patch("equinix_mcp_server.docs.Path.exists")
async def test_search_docs(mock_exists, mock_aiofiles, mock_httpx, docs_manager):
    """Test documentation search using lunr search."""
    # Mock the cache file doesn't exist initially
    mock_exists.return_value = False
    
    # Mock HTTP response
    mock_response = AsyncMock()
    mock_response.raise_for_status = AsyncMock()
    mock_response.text = '[]'  # Empty search index for test
    
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    
    # Mock file operations
    mock_file = AsyncMock()
    mock_aiofiles.return_value.__aenter__ = AsyncMock(return_value=mock_file)
    
    # Test search - should attempt to fetch and cache the index
    result = await docs_manager.search_docs("metal")
    
    # Should have attempted to fetch the search index
    mock_client.get.assert_called_once_with("https://docs.equinix.com/search-index.json")
    
    # Should contain error message about search results
    assert "No search results found" in result or "Error searching documentation" in result


@pytest.mark.asyncio
async def test_get_docs_summary(docs_manager):
    """Test getting documentation summary."""
    # Setup sample data
    docs_manager.sitemap_cache = [
        {"category": "Metal", "url": "url1", "title": "title1", "lastmod": ""},
        {"category": "Metal", "url": "url2", "title": "title2", "lastmod": ""},
        {"category": "Fabric", "url": "url3", "title": "title3", "lastmod": ""},
    ]

    result = await docs_manager.get_docs_summary()

    assert "Total documents: 3" in result
    assert "Metal**: 2" in result
    assert "Fabric**: 1" in result
