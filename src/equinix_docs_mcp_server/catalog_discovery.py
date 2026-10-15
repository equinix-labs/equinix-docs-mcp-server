"""Discover Equinix API specs from the docs.equinix.com API catalog.

docs.equinix.com publishes its API catalog as an RFC 9727 linkset at
https://docs.equinix.com/.well-known/api-catalog. Each API is a link context
anchored at its reference page (e.g. /api-catalog/fabricv4/) whose
``service-desc`` links point at the machine-readable spec: usually
<slug>/openapi.yaml, with rare AsyncAPI-only exceptions such as emgv1, which
publishes <slug>/asyncapi.yaml instead.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx2

from .config import Config

logger = logging.getLogger(__name__)

CATALOG_URL = "https://docs.equinix.com/.well-known/api-catalog"

# docs.equinix.com answers 403 to some non-browser user agents.
BROWSER_USER_AGENT = "Mozilla/5.0 (compatible; Equinix-MCP-Server)"

_SLUG_PATTERN = re.compile(r"api-catalog/([a-z0-9-]+)")


@dataclass
class CatalogEntry:
    """One API discovered in the catalog."""

    slug: str
    spec_url: Optional[str] = None  # URL of the spec, if any
    kind: str = "unknown"  # "openapi", "asyncapi", or "unknown"


def _spec_kind(link: Dict[str, Any]) -> str:
    """Classify a service-desc link as an OpenAPI or AsyncAPI description."""
    href = link.get("href", "")
    hint = f"{href.rsplit('/', 1)[-1]} {link.get('title', '')}".lower()
    if "asyncapi" in hint:
        return "asyncapi"
    if "openapi" in hint:
        return "openapi"
    return "unknown"


def parse_catalog(linkset: Dict[str, Any]) -> List[CatalogEntry]:
    """Extract one entry per API from an RFC 9727 linkset document.

    The catalog's own context (anchored at the well-known URL) only lists
    ``item`` links to the API pages; every API context anchored under
    /api-catalog/<slug>/ becomes an entry, taking its spec from the first
    OpenAPI ``service-desc`` link (falling back to any other description).
    """
    entries: Dict[str, CatalogEntry] = {}
    for context in linkset.get("linkset") or []:
        match = _SLUG_PATTERN.search(str(context.get("anchor", "")))
        if not match:
            continue
        slug = match.group(1)
        links = [
            link
            for link in context.get("service-desc") or []
            if isinstance(link, dict) and link.get("href")
        ]
        kinds = [_spec_kind(link) for link in links]
        index = kinds.index("openapi") if "openapi" in kinds else 0
        if links:
            entries[slug] = CatalogEntry(
                slug=slug, spec_url=links[index]["href"], kind=kinds[index]
            )
        else:
            entries.setdefault(slug, CatalogEntry(slug=slug))
    return [entries[slug] for slug in sorted(entries)]


async def discover_catalog_apis(
    catalog_url: str = CATALOG_URL,
) -> List[CatalogEntry]:
    """Fetch the RFC 9727 API catalog and return every listed API's spec."""
    async with httpx2.AsyncClient(
        follow_redirects=True,
        timeout=30.0,
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/linkset+json, application/json",
        },
    ) as client:
        response = await client.get(catalog_url)
        response.raise_for_status()
        entries = parse_catalog(response.json())
        logger.info(f"Found {len(entries)} APIs in the catalog")
        return entries


def configured_slugs(config: Config) -> Dict[str, str]:
    """Map already-configured catalog slugs to their API family name."""
    slugs: Dict[str, str] = {}
    for family, api_config in config.apis.items():
        for spec_source in api_config.specs:
            match = _SLUG_PATTERN.search(spec_source.url)
            if match:
                slugs[match.group(1)] = family
    return slugs


def uncataloged_families(config: Config, entries: List[CatalogEntry]) -> List[str]:
    """Families with a spec from the api-catalog that the catalog no longer lists.

    Families sourced only from outside the api-catalog are never reported.
    """
    listed = {entry.spec_url for entry in entries if entry.spec_url}
    return sorted(
        {
            family
            for family, api_config in config.apis.items()
            for spec_source in api_config.specs
            if _SLUG_PATTERN.search(spec_source.url) and spec_source.url not in listed
        }
    )


def _classify_entries(
    config: Config, entries: List[CatalogEntry]
) -> tuple[List[str], List[tuple[str, str]]]:
    """Classify catalog entries against the config.

    Returns (report lines, proposals) where each proposal is a
    (family name, apis.yaml block) pair for a newly discovered OpenAPI spec.
    Family names are the slug minus its version suffix, falling back to the
    full slug when that would collide with an existing or proposed family
    (e.g. billingv1 alongside a configured billing family from billingv2).
    """
    known = configured_slugs(config)
    used_families = set(config.apis)
    lines: List[str] = []
    proposals: List[tuple[str, str]] = []

    for entry in entries:
        if entry.slug in known:
            status = f"configured (family: {known[entry.slug]})"
        elif entry.kind == "asyncapi":
            status = "skipped: AsyncAPI only"
        elif entry.kind == "unknown":
            status = "skipped: no spec found"
        else:
            family = re.sub(r"v\d+$", "", entry.slug)
            if family in used_families:
                family = entry.slug
            used_families.add(family)
            status = f"NEW (family: {family})"
            proposals.append(
                (
                    family,
                    f"  {family}:\n"
                    f'    auth_type: "client_credentials"\n'
                    f'    service_name: "{family}"\n'
                    f"    specs:\n"
                    f'      - url: "{entry.spec_url}"\n',
                )
            )
        lines.append(f"  {entry.slug:<24} {status}")

    return lines, proposals


def propose_config_entries(config: Config, entries: List[CatalogEntry]) -> str:
    """Render a report plus ready-to-paste apis.yaml entries for new APIs.

    Only OpenAPI entries are proposed (AsyncAPI cannot back an OpenAPI
    provider). Proposals default to client_credentials auth; adjust per API
    as needed.
    """
    lines, proposals = _classify_entries(config, entries)

    report = "Discovered API catalog entries:\n" + "\n".join(lines)
    if proposals:
        report += (
            "\n\nProposed apis.yaml additions (review auth_type and consider"
            " overlays or include/exclude filters before enabling):\n\n"
            + "\n".join(block for _, block in proposals)
        )
    else:
        report += "\n\nNo new OpenAPI entries to propose."
    return report


def apply_config_entries(config: Config, entries: List[CatalogEntry]) -> List[str]:
    """Append newly discovered API families to the config file in place.

    Inserts each proposed family block at the end of the `apis:` mapping
    (immediately before the first other top-level section). Returns the
    family names that were added.
    """
    _, proposals = _classify_entries(config, entries)
    if not proposals:
        return []
    if not config.config_path:
        raise ValueError("Config has no config_path to write to")

    path = Path(config.config_path)
    text = path.read_text(encoding="utf-8")

    match = re.search(r"^(?:#[^\n]*\n)*(?:auth|docs|arazzo):", text, re.MULTILINE)
    if not match:
        raise ValueError(
            f"Could not find the end of the apis section in {config.config_path}"
        )

    insertion = "".join(f"\n{block}" for _, block in proposals)
    text = (
        text[: match.start()].rstrip("\n")
        + "\n"
        + insertion
        + "\n"
        + text[match.start() :]
    )
    path.write_text(text, encoding="utf-8")

    return [family for family, _ in proposals]
