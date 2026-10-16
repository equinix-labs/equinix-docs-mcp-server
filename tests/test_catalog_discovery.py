"""Tests for API catalog discovery."""

from equinix_docs_mcp_server.catalog_discovery import (
    CatalogEntry,
    configured_slugs,
    parse_catalog,
    propose_config_entries,
    uncataloged_families,
)
from equinix_docs_mcp_server.config import APIConfig, Config, SpecSource


def make_config() -> Config:
    """A minimal config with one family, independent of the packaged file."""
    return Config(
        apis={
            "fabric": APIConfig(
                name="fabric",
                specs=[
                    SpecSource(
                        url="https://docs.equinix.com/api-catalog/fabricv4/openapi.yaml"
                    )
                ],
            )
        }
    )


def api_context(slug: str, *descs: str) -> dict:
    base = f"https://docs.equinix.com/api-catalog/{slug}/"
    return {
        "anchor": base,
        "service-desc": [
            {"href": base + d, "type": "application/yaml", "title": f"{d} of {slug}"}
            for d in descs
        ],
        "service-doc": [{"href": base, "type": "text/html"}],
    }


LINKSET = {
    "linkset": [
        {
            "anchor": "https://docs.equinix.com/.well-known/api-catalog",
            "item": [
                {"href": "https://docs.equinix.com/api-catalog/fabricv4/"},
                {"href": "https://docs.equinix.com/api-catalog/emgv1/"},
            ],
        },
        api_context("fabricv4", "openapi.yaml"),
        api_context("emgv1", "asyncapi.yaml"),
        api_context("bothv1", "asyncapi.yaml", "openapi.yaml"),
        {"anchor": "https://docs.equinix.com/api-catalog/ghostv1/"},
    ]
}


def test_parse_catalog_reads_service_desc_links():
    entries = {entry.slug: entry for entry in parse_catalog(LINKSET)}

    # The catalog's own context is not an API
    assert sorted(entries) == ["bothv1", "emgv1", "fabricv4", "ghostv1"]
    assert entries["fabricv4"].kind == "openapi"
    assert entries["fabricv4"].spec_url == (
        "https://docs.equinix.com/api-catalog/fabricv4/openapi.yaml"
    )
    assert entries["emgv1"].kind == "asyncapi"
    # OpenAPI is preferred when an API publishes several descriptions
    assert entries["bothv1"].spec_url.endswith("/bothv1/openapi.yaml")
    assert entries["ghostv1"].kind == "unknown"
    assert entries["ghostv1"].spec_url is None


def test_uncataloged_families():
    config = make_config()
    config.apis["retired"] = APIConfig(
        name="retired",
        specs=[
            SpecSource(
                url="https://docs.equinix.com/api-catalog/retiredv1/openapi.yaml"
            )
        ],
    )
    config.apis["external"] = APIConfig(
        name="external",
        specs=[SpecSource(url="https://example.com/openapi.yaml")],
    )

    assert uncataloged_families(config, parse_catalog(LINKSET)) == ["retired"]


def test_configured_slugs_maps_to_families():
    config = Config.load()
    slugs = configured_slugs(config)
    assert slugs.get("network-edgev1") == "network-edge"
    assert slugs.get("fabricv4") == "fabric"
    assert slugs.get("workvisitv1") == "workvisit"


def test_propose_config_entries():
    config = make_config()
    entries = [
        CatalogEntry(
            slug="fabricv4",
            spec_url="https://docs.equinix.com/api-catalog/fabricv4/openapi.yaml",
            kind="openapi",
        ),
        CatalogEntry(
            slug="ordersv2",
            spec_url="https://docs.equinix.com/api-catalog/ordersv2/openapi.yaml",
            kind="openapi",
        ),
        CatalogEntry(slug="emgv1", kind="asyncapi"),
        CatalogEntry(slug="ghostv1"),
    ]

    report = propose_config_entries(config, entries)

    # Configured, asyncapi, and missing entries are not proposed
    assert "configured (family: fabric)" in report
    assert "AsyncAPI only" in report
    assert "no spec found" in report
    # The one genuinely new OpenAPI entry is proposed with a versionless family
    assert "  orders:\n" in report
    assert 'url: "https://docs.equinix.com/api-catalog/ordersv2/openapi.yaml"' in report


def test_apply_config_entries(tmp_path):
    """New families are appended into the apis: section with collision-safe names."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "apis.yaml"
    config_file.write_text(
        "apis:\n"
        "  billing:\n"
        '    auth_type: "client_credentials"\n'
        "    specs:\n"
        '      - url: "https://docs.equinix.com/api-catalog/billingv2/openapi.yaml"\n'
        "\n"
        "# Authentication configuration\n"
        "auth:\n"
        "  client_credentials:\n"
        '    token_url: "https://api.equinix.com/oauth2/v1/token"\n',
        encoding="utf-8",
    )
    config = Config.load(str(config_file))

    entries = [
        CatalogEntry(
            slug="billingv1",
            spec_url="https://docs.equinix.com/api-catalog/billingv1/openapi.yaml",
            kind="openapi",
        ),
        CatalogEntry(
            slug="ordersv2",
            spec_url="https://docs.equinix.com/api-catalog/ordersv2/openapi.yaml",
            kind="openapi",
        ),
        CatalogEntry(slug="emgv1", kind="asyncapi"),
    ]

    from equinix_docs_mcp_server.catalog_discovery import apply_config_entries

    added = apply_config_entries(config, entries)

    # billingv1's versionless name collides with the existing billing family
    assert added == ["billingv1", "orders"]

    updated = Config.load(str(config_file))
    assert set(updated.apis) == {"billing", "billingv1", "orders"}
    assert updated.auth.client_credentials["token_url"]

    # Re-running adds nothing
    assert apply_config_entries(updated, entries) == []
