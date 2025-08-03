"""Documentation management using Equinix sitemap."""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiofiles
import httpx

from .config import Config


class DocsManager:
    """Manages Equinix documentation discovery and search."""

    def __init__(self, config: Config):
        """Initialize with configuration."""
        self.config = config
        self.sitemap_cache: List[Dict[str, str]] = []

    async def update_sitemap(self) -> None:
        """Update the sitemap cache from the remote sitemap."""
        sitemap_url = self.config.docs.sitemap_url

        async with httpx.AsyncClient() as client:
            response = await client.get(sitemap_url)
            response.raise_for_status()

            # Save to cache file
            cache_path = Path(self.config.docs.cache_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(cache_path, "w") as f:
                await f.write(response.text)

            # Parse the sitemap
            await self._parse_sitemap(response.text)

    async def _parse_sitemap(self, sitemap_xml: str) -> None:
        """Parse the sitemap XML and extract URL information."""
        root = ET.fromstring(sitemap_xml)

        # Handle namespace
        namespace = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        self.sitemap_cache = []

        for url_elem in root.findall("ns:url", namespace):
            loc = url_elem.find("ns:loc", namespace)
            lastmod = url_elem.find("ns:lastmod", namespace)
            changefreq = url_elem.find("ns:changefreq", namespace)
            priority = url_elem.find("ns:priority", namespace)

            if loc is not None:
                url_info = {
                    "url": loc.text or "",
                    "lastmod": lastmod.text if lastmod is not None else "",
                    "changefreq": changefreq.text if changefreq is not None else "",
                    "priority": priority.text if priority is not None else "",
                    "title": self._extract_title_from_url(loc.text or ""),
                    "category": self._categorize_url(loc.text or ""),
                }
                # Ensure all values are strings for type safety
                url_info_safe = {
                    k: v if v is not None else "" for k, v in url_info.items()
                }
                self.sitemap_cache.append(url_info_safe)

    def _extract_title_from_url(self, url: str) -> str:
        """Extract a human-readable title from a URL."""
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]

        if not path_parts:
            return "Home"

        # Take the last meaningful part and clean it up
        title = path_parts[-1]
        title = title.replace("-", " ").replace("_", " ")
        title = " ".join(word.capitalize() for word in title.split())

        return title

    def _categorize_url(self, url: str) -> str:
        """Categorize a URL based on its path."""
        parsed = urlparse(url)
        path = parsed.path.lower()

        if "/api-catalog/" in path:
            return "API"
        elif "/metal" in path:
            return "Metal"
        elif "/fabric" in path:
            return "Fabric"
        elif "/network-edge" in path:
            return "Network Edge"
        elif "/billing" in path:
            return "Billing"
        elif "/quickstart" in path or "/getting-started" in path:
            return "Getting Started"
        elif "/tutorials" in path or "/guides" in path:
            return "Tutorials"
        elif "/reference" in path:
            return "Reference"
        else:
            return "General"

    async def list_docs(self, filter_term: Optional[str] = None) -> str:
        """List documentation with optional filtering."""
        if not self.sitemap_cache:
            await self._load_cached_sitemap()

        filtered_docs = self.sitemap_cache

        if filter_term:
            filter_term = filter_term.lower()
            filtered_docs = [
                doc
                for doc in self.sitemap_cache
                if (
                    filter_term in doc["title"].lower()
                    or filter_term in doc["category"].lower()
                    or filter_term in doc["url"].lower()
                )
            ]

        # Group by category
        categories: Dict[str, List[Dict[str, str]]] = {}
        for doc in filtered_docs:
            category = doc["category"]
            if category not in categories:
                categories[category] = []
            categories[category].append(doc)

        # Format output
        result = ["# Equinix Documentation\n"]

        for category, docs in sorted(categories.items()):
            result.append(f"## {category}\n")
            for doc in docs[:10]:  # Limit to 10 per category
                result.append(f"- **{doc['title']}**: {doc['url']}")

            if len(docs) > 10:
                result.append(f"  ... and {len(docs) - 10} more")
            result.append("")

        total_shown = sum(min(10, len(docs)) for docs in categories.values())
        total_available = len(filtered_docs)

        if total_shown < total_available:
            result.append(f"Showing {total_shown} of {total_available} documents.")

        return "\n".join(result)

    async def search_docs(self, query: str) -> str:
        """Search documentation by query."""
        if not self.sitemap_cache:
            await self._load_cached_sitemap()

        query = query.lower()

        # Score documents based on relevance
        scored_docs = []

        for doc in self.sitemap_cache:
            score = 0

            # Title matches are most important
            if query in doc["title"].lower():
                score += 10

            # Category matches
            if query in doc["category"].lower():
                score += 5

            # URL matches
            if query in doc["url"].lower():
                score += 3

            # Keyword scoring
            query_words = query.split()
            for word in query_words:
                if word in doc["title"].lower():
                    score += 2
                if word in doc["category"].lower():
                    score += 1

            if score > 0:
                scored_docs.append((score, doc))

        # Sort by score and take top results
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        top_results = scored_docs[:20]

        if not top_results:
            return f"No documentation found for query: '{query}'"

        result = [f"# Search Results for '{query}'\n"]

        for score, doc in top_results:
            result.append(f"**{doc['title']}** ({doc['category']})")
            result.append(f"  {doc['url']}")
            if doc["lastmod"]:
                result.append(f"  *Last modified: {doc['lastmod']}*")
            result.append("")

        return "\n".join(result)

    async def _load_cached_sitemap(self) -> None:
        """Load sitemap from cache file if available."""
        cache_path = Path(self.config.docs.cache_path)

        if cache_path.exists():
            async with aiofiles.open(cache_path, "r") as f:
                content = await f.read()
                await self._parse_sitemap(content)
        else:
            # If no cache, update from remote
            await self.update_sitemap()

    async def get_docs_summary(self) -> str:
        """Get a summary of available documentation."""
        if not self.sitemap_cache:
            await self._load_cached_sitemap()

        # Count by category
        categories: Dict[str, int] = {}
        for doc in self.sitemap_cache:
            category = doc["category"]
            categories[category] = categories.get(category, 0) + 1

        result = ["# Equinix Documentation Summary\n"]
        result.append(f"Total documents: {len(self.sitemap_cache)}\n")
        result.append("## By Category:")

        for category, count in sorted(categories.items()):
            result.append(f"- **{category}**: {count} documents")

        result.append(
            f"\nLast updated: {Path(self.config.docs.cache_path).stat().st_mtime if Path(self.config.docs.cache_path).exists() else 'Never'}"
        )

        return "\n".join(result)
