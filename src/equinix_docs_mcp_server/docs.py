"""Documentation management using Equinix sitemap and llms.txt."""

import inspect
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import aiofiles
import httpx

from .config import Config
from .lunr_search.search_client import Client as SearchClient

GENERAL_CATEGORY = "General"


class DocsManager:
    """Manages Equinix documentation discovery and search."""

    def __init__(self, config: Config):
        """Initialize with configuration."""
        self.config = config
        self.sitemap_cache: List[Dict[str, str]] = []
        self.llms_cache: List[Dict[str, str]] = []

    async def update_sitemap(self) -> None:
        """Update the sitemap cache from the remote sitemap."""
        sitemap_url = self.config.docs.sitemap_url

        async with httpx.AsyncClient() as client:
            response = await client.get(sitemap_url)
            await self._raise_for_status(response)

            # Save to cache file
            cache_path = Path(self.config.docs.cache_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(cache_path, "w") as f:
                await f.write(response.text)

            # Parse the sitemap
            await self._parse_sitemap(response.text)

    async def update_llms_txt(self) -> None:
        """Update the llms.txt cache from the remote llms.txt file."""
        llms_url = self.config.docs.llms_url

        async with httpx.AsyncClient() as client:
            response = await client.get(llms_url)
            await self._raise_for_status(response)

            cache_path = Path(self.config.docs.llms_cache_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(cache_path, "w") as f:
                await f.write(response.text)

            await self._parse_llms_txt(response.text)

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
                    "url": self._normalize_doc_url(loc.text or ""),
                    "lastmod": lastmod.text if lastmod is not None else "",
                    "changefreq": changefreq.text if changefreq is not None else "",
                    "priority": priority.text if priority is not None else "",
                    "title": self._extract_title_from_url(loc.text or ""),
                    "category": self._categorize_url(loc.text or ""),
                    "description": "",
                    "source": "sitemap",
                }
                # Ensure all values are strings for type safety
                url_info_safe = {
                    k: v if v is not None else "" for k, v in url_info.items()
                }
                self.sitemap_cache.append(url_info_safe)

    async def _parse_llms_txt(self, llms_txt: str) -> None:
        """Parse llms.txt and extract linked documentation entries."""
        current_category = GENERAL_CATEGORY
        parsed_docs: List[Dict[str, str]] = []

        for raw_line in llms_txt.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("#"):
                heading = line.lstrip("#").strip()
                if heading:
                    current_category = heading
                continue

            matches = list(re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", line))
            if matches:
                for match in matches:
                    title = match.group(1).strip()
                    url = self._normalize_doc_url(match.group(2).strip())
                    if not url:
                        continue

                    description = self._clean_description_fragment(line[match.end() :])
                    parsed_docs.append(
                        {
                            "url": url,
                            "lastmod": "",
                            "changefreq": "",
                            "priority": "",
                            "title": title or self._extract_title_from_url(url),
                            "category": current_category,
                            "description": description,
                            "source": "llms",
                        }
                    )
                continue

            url_match = re.search(r"(https?://docs\.equinix\.com/\S+)", line)
            if url_match:
                url = self._normalize_doc_url(url_match.group(1))
                if url:
                    before_url = self._clean_description_fragment(line[: url_match.start()])
                    after_url = self._clean_description_fragment(line[url_match.end() :])
                    parsed_docs.append(
                        {
                            "url": url,
                            "lastmod": "",
                            "changefreq": "",
                            "priority": "",
                            "title": self._extract_title_from_url(url),
                            "category": current_category,
                            "description": after_url or before_url,
                            "source": "llms",
                        }
                    )

        self.llms_cache = self._merge_docs(parsed_docs)

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
        filtered_docs = await self._get_all_docs()

        if filter_term:
            filter_term = filter_term.lower()
            # Split filter term into individual words for more flexible matching
            filter_words = [
                word.strip() for word in filter_term.split() if word.strip()
            ]

            if filter_words:
                # Score documents based on how many filter words they contain
                scored_docs = []
                for doc in filtered_docs:
                    doc_text = self._doc_text(doc)
                    score = 0.0

                    # Count how many filter words appear in the document
                    for word in filter_words:
                        word_lower = word.lower()
                        if word_lower in doc_text:
                            score += 1.0
                        # Also check for common word variations (handle singular/plural)
                        elif word_lower.endswith("s") and word_lower[:-1] in doc_text:
                            score += 0.8  # Slightly lower score for stem matches
                        elif (
                            not word_lower.endswith("s")
                            and f"{word_lower}s" in doc_text
                        ):
                            score += 0.8  # Handle plural forms

                    # Bonus points for exact phrase matches
                    if filter_term in doc_text:
                        score += 0.5

                    if score > 0.0:
                        scored_docs.append((score, doc))

                # Sort by score (highest first) and extract documents
                filtered_docs = [
                    doc
                    for score, doc in sorted(
                        scored_docs, key=lambda x: x[0], reverse=True
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

    async def find_docs(self, query: str) -> str:
        """Find documentation by filename-based search."""
        docs = await self._get_all_docs()

        query = query.lower()

        # Score documents based on relevance
        scored_docs = []

        for doc in docs:
            score = 0.0

            # Title matches are most important
            if query in doc["title"].lower():
                score += 10

            # Category matches
            if query in doc["category"].lower():
                score += 5

            # URL matches
            if query in doc["url"].lower():
                score += 3

            if query in doc.get("description", "").lower():
                score += 4

            # Keyword scoring
            query_words = query.split()
            for word in query_words:
                if word in doc["title"].lower():
                    score += 2
                if word in doc["category"].lower():
                    score += 1
                if word in doc.get("description", "").lower():
                    score += 1.5

            if score > 0:
                scored_docs.append((score, doc))

        # Sort by score and take top results
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        top_results = scored_docs[:20]

        if not top_results:
            return f"No documentation found for query: '{query}'"

        result = [f"# Find Results for '{query}'\n"]

        for score, doc in top_results:
            result.append(f"**{doc['title']}** ({doc['category']})")
            result.append(f"  {doc['url']}")
            if doc["lastmod"]:
                result.append(f"  *Last modified: {doc['lastmod']}*")
            result.append("")

        return "\n".join(result)

    async def search_docs(self, query: str, limit: int = 8) -> str:
        """Search documentation using lunr search against indexed content."""
        docs = await self._get_all_docs()
        llms_results = self._search_doc_metadata(docs, query, limit=limit)

        search_index_url = "https://docs.equinix.com/search-index.json"
        cache_dir = Path("cache/search")
        cache_file = cache_dir / "search-index.json"
        search_results: List[Dict[str, Any]] = []
        search_error: Optional[str] = None

        # Ensure cache directory exists
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Check if we need to fetch the search index
        if not cache_file.exists():
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(search_index_url)
                    await self._raise_for_status(response)

                    # Save to cache
                    async with aiofiles.open(cache_file, "w") as f:
                        await f.write(response.text)
            except Exception as e:
                search_error = f"Error fetching search index: {str(e)}"

        # Initialize search client with cached file
        try:
            search_client = SearchClient(str(cache_file))
            search_client.load()

            # Perform search
            search_results = search_client.search(query, limit=limit)

        except Exception as e:
            search_error = f"Error searching documentation: {str(e)}"

        results = self._merge_search_results(llms_results, search_results, limit=limit)

        if not results:
            if search_error:
                return search_error
            return f"No search results found for query: '{query}'"

        result_lines = [f"# Search Results for '{query}'\n"]

        for result in results:
            title = result.get("title", "No title")
            url = result.get("url", "")
            description = result.get("description", "")
            result_lines.append(f"**{title}**")
            result_lines.append(f"  {url}")
            if description:
                result_lines.append(f"  {description}")
            result_lines.append("")

        if len(results) == limit:
            result_lines.append(
                f"Showing top {limit} results. Refine your query for more specific results."
            )

        return "\n".join(result_lines)

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

    async def _load_cached_llms(self) -> None:
        """Load llms.txt from cache file if available."""
        cache_path = Path(self.config.docs.llms_cache_path)

        if cache_path.exists():
            async with aiofiles.open(cache_path, "r") as f:
                content = await f.read()
                await self._parse_llms_txt(content)
        else:
            try:
                await self.update_llms_txt()
            except Exception:
                self.llms_cache = []

    async def fetch_doc(self, url: str) -> str:
        """Fetch the markdown content of a documentation page.

        Args:
            url: The URL of the documentation page to fetch. Can be a full URL
                 or a path. Will attempt to fetch the markdown version.

        Returns:
            The markdown content of the page, or an error message if fetch fails.
        """
        url = self._to_markdown_url(url)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                await self._raise_for_status(response)

                # Return the markdown content
                return response.text

        except httpx.HTTPStatusError as e:
            return f"Error fetching document: HTTP {e.response.status_code} - {url}\n\nThe document may not be available in markdown format."
        except httpx.RequestError as e:
            return f"Error fetching document: {str(e)}\n\nURL: {url}"
        except Exception as e:
            return f"Unexpected error fetching document: {str(e)}\n\nURL: {url}"

    async def get_docs_summary(self) -> str:
        """Get a summary of available documentation."""
        docs = await self._get_all_docs()

        # Count by category
        categories: Dict[str, int] = {}
        for doc in docs:
            category = doc["category"]
            categories[category] = categories.get(category, 0) + 1

        result = ["# Equinix Documentation Summary\n"]
        result.append(f"Total documents: {len(docs)}\n")
        result.append("## By Category:")

        for category, count in sorted(categories.items()):
            result.append(f"- **{category}**: {count} documents")

        result.append(
            f"\nLast updated: {Path(self.config.docs.cache_path).stat().st_mtime if Path(self.config.docs.cache_path).exists() else 'Never'}"
        )

        return "\n".join(result)

    async def _get_all_docs(self) -> List[Dict[str, str]]:
        """Return the combined documentation catalog from all supported sources."""
        if not self.sitemap_cache:
            try:
                await self._load_cached_sitemap()
            except Exception:
                self.sitemap_cache = []
        if not self.llms_cache:
            try:
                await self._load_cached_llms()
            except Exception:
                self.llms_cache = []
        return self._merge_docs(self.sitemap_cache, self.llms_cache)

    def _merge_docs(self, *doc_lists: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Merge documentation entries from multiple sources by canonical URL."""
        merged: Dict[str, Dict[str, str]] = {}

        for doc_list in doc_lists:
            for doc in doc_list:
                url = self._normalize_doc_url(doc.get("url", ""))
                if not url:
                    continue

                existing = merged.get(url, {}).copy()
                sources = {
                    source
                    for source in (
                        existing.get("source", "").split(",")
                        + doc.get("source", "").split(",")
                    )
                    if source
                }

                merged[url] = {
                    "url": url,
                    "lastmod": existing.get("lastmod") or doc.get("lastmod", ""),
                    "changefreq": existing.get("changefreq")
                    or doc.get("changefreq", ""),
                    "priority": existing.get("priority") or doc.get("priority", ""),
                    "title": doc.get("title")
                    or existing.get("title")
                    or self._extract_title_from_url(url),
                    "category": doc.get("category")
                    if doc.get("category") and doc.get("category") != GENERAL_CATEGORY
                    else existing.get("category")
                    or doc.get("category")
                    or self._categorize_url(url),
                    "description": doc.get("description")
                    or existing.get("description", ""),
                    "source": ",".join(sorted(sources)),
                }

        return list(merged.values())

    def _search_doc_metadata(
        self, docs: List[Dict[str, str]], query: str, limit: int = 8
    ) -> List[Dict[str, str]]:
        """Search combined metadata sources such as sitemap and llms.txt."""
        query_lower = query.lower().strip()
        if not query_lower:
            return []

        query_words = [word for word in query_lower.split() if word]
        scored_docs = []

        for doc in docs:
            score = 0.0
            title = doc.get("title", "").lower()
            category = doc.get("category", "").lower()
            description = doc.get("description", "").lower()
            url = doc.get("url", "").lower()

            if query_lower in title:
                score += 12
            if query_lower in description:
                score += 8
            if query_lower in category:
                score += 5
            if query_lower in url:
                score += 3

            for word in query_words:
                if word in title:
                    score += 3
                if word in description:
                    score += 2
                if word in category:
                    score += 1
                if word in url:
                    score += 0.5

            if "llms" in doc.get("source", ""):
                score += 1

            if score > 0:
                scored_docs.append((score, doc))

        return [
            doc
            for _, doc in sorted(
                scored_docs,
                key=lambda item: (item[0], "llms" in item[1].get("source", "")),
                reverse=True,
            )[:limit]
        ]

    def _merge_search_results(
        self,
        metadata_results: List[Dict[str, str]],
        search_results: List[Dict[str, Any]],
        limit: int = 8,
    ) -> List[Dict[str, str]]:
        """Merge llms/sitemap metadata hits with lunr search hits."""
        merged: Dict[str, Dict[str, str]] = {}

        for doc in metadata_results:
            url = self._normalize_doc_url(doc.get("url", ""))
            if url and url not in merged:
                merged[url] = {
                    "title": doc.get("title", "No title"),
                    "url": url,
                    "description": doc.get("description", ""),
                    "source": doc.get("source", ""),
                }

        for result in search_results:
            url = self._normalize_doc_url(str(result.get("url", "")))
            if not url:
                continue

            if url in merged:
                if not merged[url].get("title") or merged[url]["title"] == "No title":
                    merged[url]["title"] = str(result.get("title", "No title"))
                continue

            merged[url] = {
                "title": str(result.get("title", "No title")),
                "url": url,
                "description": "",
                "source": "search-index",
            }

        return list(merged.values())[:limit]

    def _normalize_doc_url(self, url: str) -> str:
        """Normalize documentation URLs for dedupe and fetch behavior."""
        normalized = (url or "").strip()
        if not normalized:
            return ""

        if not normalized.startswith(("http://", "https://")):
            normalized = f"https://docs.equinix.com/{normalized.lstrip('/')}"

        parsed = urlparse(normalized)
        path = parsed.path or "/"

        if path.endswith(".html"):
            path = path[:-5]
        elif path.endswith(".md"):
            path = path[:-3]

        if path != "/":
            path = path.rstrip("/")
        if not path:
            path = "/"

        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc or "docs.equinix.com",
                path,
                "",
                "",
                "",
            )
        )

    def _to_markdown_url(self, url: str) -> str:
        """Convert a documentation URL or path to its markdown endpoint."""
        normalized = self._normalize_doc_url(url)
        if normalized.endswith("/"):
            return f"{normalized}index.md"
        if normalized == "https://docs.equinix.com/":
            return "https://docs.equinix.com/index.md"
        return f"{normalized}.md"

    def _doc_text(self, doc: Dict[str, str]) -> str:
        """Return combined searchable text for a documentation entry."""
        return (
            f"{doc.get('title', '')} {doc.get('category', '')} "
            f"{doc.get('description', '')} {doc.get('url', '')}"
        ).lower()

    def _clean_description_fragment(self, text: str) -> str:
        """Normalize llms.txt description fragments."""
        cleaned = re.sub(r"^[\s\-–—:|>•]+", "", text or "")
        cleaned = re.sub(r"[\s\-–—:|>•]+$", "", cleaned)
        return cleaned.strip()

    async def _raise_for_status(self, response: httpx.Response) -> None:
        """Raise HTTP errors for both sync and async-compatible mocks."""
        result = response.raise_for_status()
        if inspect.isawaitable(result):
            await result
