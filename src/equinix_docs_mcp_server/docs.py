"""Documentation discovery for docs.equinix.com.

The page index comes from the site's llms.txt (titles, descriptions and
product sections for every English page), falling back to sitemap.xml.
Search uses the site's prebuilt lunr index. All three are cached on disk and
refreshed after ``docs.cache_max_age_hours``.
"""

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiofiles
import httpx2
from lunr import lunr
from lunr.exceptions import BaseLunrException
from lunr.index import Index

from .config import Config, cache_root
from .lunr_search.search_client import Client as SearchClient
from .lunr_search.search_client import query_text

logger = logging.getLogger(__name__)

DOCS_BASE_URL = "https://docs.equinix.com"

# llms.txt entries: "- [Title](/path.md): Description"
_LLMS_ENTRY = re.compile(
    r"^-\s*\[(?P<title>[^\]]+)\]\((?P<url>[^)\s]+)\)\s*(?::\s*(?P<desc>.*))?$"
)

# Page index fields for find_docs and list_docs filtering, and their boosts.
# A title match outweighs a description that mentions the term in passing.
_PAGE_FIELDS = [
    {"field_name": "title", "boost": 10},
    {"field_name": "category", "boost": 3},
    {"field_name": "path", "boost": 2},
    {"field_name": "description", "boost": 1},
]

# list_docs keeps filtered pages scoring at least this fraction of the best.
_LIST_RELATIVE_CUTOFF = 0.3

# Added to a page's score when the whole query appears in its title.
_TITLE_PHRASE_BONUS = 10.0

_CATEGORY_ACRONYMS = {"api": "API", "iam": "IAM", "ibx": "IBX", "loa": "LOA"}


def _category_name(heading: str) -> str:
    """Turn an llms.txt section slug (``fabric-cloud-router``) into a name."""
    words = [w for w in re.split(r"[-_\s]+", heading.strip()) if w]
    return " ".join(
        _CATEGORY_ACRONYMS.get(w.lower(), w if w.isupper() else w.capitalize())
        for w in words
    )


def _page_url(link: str) -> str:
    """Page URL for an llms.txt link, which points at the page's .md version.

    Agents should cite page URLs, not .md URLs (docs.equinix.com/AGENTS.md);
    fetch adds .md back.
    """
    url = urljoin(DOCS_BASE_URL + "/", link)
    if url.endswith(".md"):
        url = url[: -len(".md")]
    if url.endswith("/index"):
        url = url[: -len("index")]
    return url


class DocsManager:
    """Manages Equinix documentation discovery and search."""

    def __init__(self, config: Config):
        """Initialize with configuration."""
        self.config = config
        self.sitemap_cache: List[Dict[str, str]] = []
        self._index_loaded_at: Optional[float] = None
        self._index_source: Optional[Path] = None
        self._page_index: Optional[Index] = None
        self._page_index_docs: Optional[List[Dict[str, str]]] = None
        self._search_client: Optional[SearchClient] = None
        self._search_loaded_at: Optional[float] = None

    def _index_cache_path(self) -> Path:
        """Filesystem location of the docs index cache.

        Relative config values resolve under the user cache directory so the
        server does not depend on the working directory.
        """
        p = Path(self.config.docs.cache_path)
        return p if p.is_absolute() else cache_root() / p

    def _llms_cache_path(self) -> Path:
        """Cache file for llms.txt, alongside the sitemap cache."""
        return self._index_cache_path().with_suffix(".txt")

    def _max_age_seconds(self) -> Optional[float]:
        """Cache lifetime, or None when caches never expire."""
        hours = self.config.docs.cache_max_age_hours
        return hours * 3600 if hours > 0 else None

    def _is_fresh_time(self, timestamp: Optional[float]) -> bool:
        if timestamp is None:
            return False
        max_age = self._max_age_seconds()
        return max_age is None or time.time() - timestamp < max_age

    def _is_fresh(self, path: Path) -> bool:
        """Whether a cache file exists and is younger than the max age."""
        return path.exists() and self._is_fresh_time(path.stat().st_mtime)

    async def _ensure_index(self) -> None:
        """Load the page index, refreshing it from the site when stale.

        If the refresh fails, a stale cached copy is used rather than none.
        """
        if self.sitemap_cache and self._is_fresh_time(self._index_loaded_at):
            return

        llms_cache = self._llms_cache_path()
        sitemap_cache = self._index_cache_path()
        if not (self._is_fresh(llms_cache) or self._is_fresh(sitemap_cache)):
            try:
                await self.update_index()
                return
            except Exception as e:
                if not (llms_cache.exists() or sitemap_cache.exists()):
                    raise
                logger.warning(f"Could not refresh the docs index, using cache: {e}")

        await self._load_cached_index()

    async def update_sitemap(self) -> None:
        """Update the sitemap cache from the remote sitemap."""
        sitemap_url = self.config.docs.sitemap_url

        async with httpx2.AsyncClient() as client:
            response = await client.get(sitemap_url)
            response.raise_for_status()

            # Save to cache file
            cache_path = self._index_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(cache_path, "w") as f:
                await f.write(response.text)

            # Parse the sitemap
            await self._parse_sitemap(response.text)
            self._loaded(cache_path)

    async def update_index(self) -> None:
        """Refresh the page index from llms.txt, falling back to the sitemap."""
        llms_txt_url = self.config.docs.llms_txt_url
        try:
            async with httpx2.AsyncClient() as client:
                response = await client.get(llms_txt_url, timeout=30.0)
                response.raise_for_status()
            await self._parse_llms_txt(response.text)
            if not self.sitemap_cache:
                raise ValueError("no page entries found")

            cache_path = self._llms_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(cache_path, "w") as f:
                await f.write(response.text)
            self._loaded(cache_path)
            return
        except Exception as e:
            logger.warning(f"Could not use {llms_txt_url}, trying the sitemap: {e}")

        await self.update_sitemap()

    def _loaded(self, source: Path) -> None:
        self._index_loaded_at = time.time()
        self._index_source = source

    async def _parse_llms_txt(self, content: str) -> None:
        """Parse llms.txt: "## section" headings and "- [Title](url): Description".

        A description may continue on the lines after its entry.
        """
        self.sitemap_cache = []
        current_category = "General"
        current: Optional[Dict[str, str]] = None

        for raw in content.splitlines():
            line = raw.strip()
            if not line:
                current = None
                continue

            if line.startswith("#"):
                current = None
                # The single "#" heading is the site title, not a section.
                if line.startswith("##"):
                    current_category = _category_name(line.lstrip("#"))
                continue

            match = _LLMS_ENTRY.match(line)
            if match:
                current = {
                    "url": _page_url(match["url"]),
                    "title": match["title"].strip(),
                    "category": current_category,
                    "description": (match["desc"] or "").strip(),
                    "lastmod": "",
                    "changefreq": "",
                    "priority": "",
                }
                self.sitemap_cache.append(current)
            elif current is not None:
                current["description"] = f"{current['description']} {line}".strip()

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
        await self._ensure_index()

        filtered_docs = self.sitemap_cache

        ranked = bool(filter_term and query_text(filter_term))
        if ranked:
            hits = self._rank_pages(filter_term or "")
            # Any one common word ("fabric") matches hundreds of pages; keep
            # the pages that match about as well as the best one.
            cutoff = hits[0][0] * _LIST_RELATIVE_CUTOFF if hits else 0.0
            filtered_docs = [doc for score, doc in hits if score >= cutoff]

        # Group by category
        categories: Dict[str, List[Dict[str, str]]] = {}
        for doc in filtered_docs:
            category = doc["category"]
            if category not in categories:
                categories[category] = []
            categories[category].append(doc)

        # Format output
        result = ["# Equinix Documentation\n"]

        # Filtered: categories in order of their best match. Otherwise A-Z.
        groups = categories.items() if ranked else sorted(categories.items())
        for category, docs in groups:
            result.append(f"## {category}\n")
            for doc in docs[:10]:  # Limit to 10 per category
                desc = f" - {doc['description']}" if doc.get("description") else ""
                result.append(f"- **{doc['title']}**: {doc['url']}{desc}")

            if len(docs) > 10:
                result.append(f"  ... and {len(docs) - 10} more")
            result.append("")

        total_shown = sum(min(10, len(docs)) for docs in categories.values())
        total_available = len(filtered_docs)

        if total_shown < total_available:
            result.append(f"Showing {total_shown} of {total_available} documents.")

        return "\n".join(result)

    def _page_index_for_cache(self) -> Index:
        """BM25 index over the page list, rebuilt when the list is replaced."""
        if self._page_index is None or self._page_index_docs is not self.sitemap_cache:
            documents = [
                {
                    "ref": str(n),
                    "title": doc.get("title", ""),
                    "category": doc.get("category", ""),
                    "path": " ".join(re.split(r"[/_.-]+", urlparse(doc["url"]).path)),
                    "description": doc.get("description", ""),
                }
                for n, doc in enumerate(self.sitemap_cache)
            ]
            self._page_index = lunr(ref="ref", fields=_PAGE_FIELDS, documents=documents)
            self._page_index_docs = self.sitemap_cache
        return self._page_index

    def _rank_pages(self, query: str) -> List[Tuple[float, Dict[str, str]]]:
        """Pages matching any query word, best first.

        lunr matches whole, stemmed words ("ports" finds "Port" but "port"
        does not find "Portal") and ranks them with BM25 across _PAGE_FIELDS.
        """
        text = query_text(query)
        if not text or not self.sitemap_cache:
            return []
        try:
            hits = self._page_index_for_cache().search(text)
        except BaseLunrException:
            return []

        phrase = " ".join(text.lower().split())
        ranked = []
        for hit in hits:
            doc = self.sitemap_cache[int(hit["ref"])]
            score = hit["score"]
            if phrase in doc.get("title", "").lower():
                score += _TITLE_PHRASE_BONUS
            ranked.append((score, doc))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return ranked

    async def find_docs(self, query: str) -> str:
        """Find documentation pages by title, section, URL and description."""
        await self._ensure_index()

        top_results = self._rank_pages(query)[:20]

        if not top_results:
            return f"No documentation found for query: '{query}'"

        result = [f"# Find Results for '{query}'\n"]

        for _, doc in top_results:
            result.append(f"**{doc['title']}** ({doc['category']})")
            result.append(f"  {doc['url']}")
            if doc.get("description"):
                result.append(f"  {doc['description']}")
            if doc["lastmod"]:
                result.append(f"  *Last modified: {doc['lastmod']}*")
            result.append("")

        return "\n".join(result)

    async def search_docs(self, query: str, limit: int = 8) -> str:
        """Search documentation using lunr search against indexed content."""
        search_index_url = "https://docs.equinix.com/search-index.json"
        cache_dir = cache_root() / "search"
        cache_file = cache_dir / "search-index.json"

        # Ensure cache directory exists
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Fetch the search index when missing or stale; keep a stale copy if
        # the refresh fails
        if not self._is_fresh(cache_file):
            try:
                async with httpx2.AsyncClient() as client:
                    response = await client.get(search_index_url, timeout=30.0)
                    response.raise_for_status()

                    # Save to cache
                    async with aiofiles.open(cache_file, "w") as f:
                        await f.write(response.text)
                self._search_client = None
            except Exception as e:
                if not cache_file.exists():
                    return f"Error fetching search index: {str(e)}"
                logger.warning(f"Could not refresh the search index, using cache: {e}")

        try:
            if self._search_client is None or not self._is_fresh_time(
                self._search_loaded_at
            ):
                search_client = SearchClient(str(cache_file))
                search_client.load()
                self._search_client = search_client
                self._search_loaded_at = time.time()

            results = self._search_client.search(query, limit=limit)

            if not results:
                return f"No search results found for query: '{query}'"

            result_lines = [f"# Search Results for '{query}'\n"]

            for result in results:
                title = result.get("title", "No title")
                url = f"https://docs.equinix.com{result.get('url', '')}"
                if result.get("section"):
                    title = f"{title} — {result['section']}"
                result_lines.append(f"**{title}**")
                result_lines.append(f"  {url}{result.get('anchor', '')}")
                result_lines.append("")

            if len(results) == limit:
                result_lines.append(
                    f"Showing top {limit} results. Refine your query for more specific results."
                )

            return "\n".join(result_lines)

        except Exception as e:
            return f"Error searching documentation: {str(e)}"

    async def _load_cached_index(self) -> None:
        """Load the newest cached page index (llms.txt or sitemap)."""
        candidates = [
            p for p in (self._llms_cache_path(), self._index_cache_path()) if p.exists()
        ]
        if not candidates:
            await self.update_index()
            return

        source = max(candidates, key=lambda p: p.stat().st_mtime)
        async with aiofiles.open(source, "r") as f:
            content = await f.read()
        if source.suffix == ".txt":
            await self._parse_llms_txt(content)
        else:
            await self._parse_sitemap(content)
        self._loaded(source)

    async def fetch_doc(self, url: str) -> str:
        """Fetch the markdown content of a documentation page.

        Args:
            url: The URL of the documentation page to fetch. Can be a full URL
                 or a path. Will attempt to fetch the markdown version.

        Returns:
            The markdown content of the page, or an error message if fetch fails.
        """
        # Drop any #anchor (search results point at sections), then make
        # sure the URL ends with .md
        url = url.split("#", 1)[0]
        if not url.endswith(".md"):
            # Remove trailing slash if present
            url = url.rstrip("/")
            # Add .md extension
            url = f"{url}.md"

        # Ensure we have a full URL
        if not url.startswith("http"):
            url = f"https://docs.equinix.com/{url.lstrip('/')}"

        try:
            async with httpx2.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                response.raise_for_status()

                # Return the markdown content
                return response.text

        except httpx2.HTTPStatusError as e:
            return f"Error fetching document: HTTP {e.response.status_code} - {url}\n\nThe document may not be available in markdown format."
        except httpx2.RequestError as e:
            return f"Error fetching document: {str(e)}\n\nURL: {url}"
        except Exception as e:
            return f"Unexpected error fetching document: {str(e)}\n\nURL: {url}"

    async def get_docs_summary(self) -> str:
        """Get a summary of available documentation."""
        await self._ensure_index()

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

        source = self._index_source
        if source is not None and source.exists():
            updated = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
            result.append(
                f"\nSource: {source.name}, last updated "
                f"{updated.isoformat(timespec='seconds')}"
            )
        else:
            result.append("\nLast updated: Never")

        return "\n".join(result)
