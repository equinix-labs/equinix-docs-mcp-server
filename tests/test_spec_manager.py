"""Test spec manager functionality."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from equinix_docs_mcp_server.config import APIConfig, Config
from equinix_docs_mcp_server.spec_manager import SpecManager


@pytest.fixture
def config():
    """Load test configuration."""
    return Config.load()


@pytest.fixture
def spec_manager(config):
    """Create spec manager instance."""
    return SpecManager(config)


def test_spec_manager_init(spec_manager):
    """Test SpecManager initialization."""
    assert spec_manager is not None
    assert spec_manager.config is not None
    assert spec_manager.overlay_manager is not None


@pytest.mark.asyncio
async def test_create_overlay_template(spec_manager, tmp_path):
    """Test overlay template creation."""
    overlay_path = tmp_path / "test_overlay.yaml"

    await spec_manager.overlay_manager.create_overlay_template(
        str(overlay_path), "metal", "metal"
    )

    assert overlay_path.exists()

    # Check content
    with open(overlay_path) as f:
        content = f.read()
        assert "Metal" in content
        assert "overlay: 1.0.0" in content


def test_apply_simple_overlay(spec_manager):
    """Test applying a simple overlay."""
    spec = {
        "info": {"title": "Original Title"},
        "servers": [{"url": "https://old.example.com"}],
    }

    overlay = {
        "actions": [
            {"target": "$.info.title", "update": "New Title"},
            {"target": "$.servers", "update": [{"url": "https://new.example.com"}]},
        ]
    }

    result = spec_manager.overlay_manager.apply(spec, "test", overlay)

    assert result["info"]["title"] == "New Title"
    assert result["servers"] == [{"url": "https://new.example.com"}]


def test_overlay_files_exist(config):
    """Test that overlay files exist for all configured APIs."""
    for api_name in config.get_api_names():
        api_config = config.get_api_config(api_name)
        if not api_config.specs:
            continue
        for spec in api_config.specs:
            if spec.overlay:
                overlay_path = config.resolve_path(spec.overlay)
                assert (
                    overlay_path.exists()
                ), f"Overlay file missing for {api_name}: {overlay_path}"


def test_apply_autogen_operation_ids(spec_manager):
    """Operations lacking operationIds get deterministic generated names."""
    spec = {
        "paths": {
            "/workvisits": {
                "get": {"responses": {}},
                "post": {"responses": {}},
            },
            "/workvisits/{id}": {
                "get": {"responses": {}},
                "delete": {"responses": {}},
            },
            "/keep": {
                "get": {"operationId": "customName", "responses": {}},
            },
        }
    }

    spec_manager._apply_autogen_operation_ids(spec)

    paths = spec["paths"]
    assert paths["/workvisits"]["get"]["operationId"] == "listWorkvisits"
    assert paths["/workvisits"]["post"]["operationId"] == "postWorkvisits"
    assert paths["/workvisits/{id}"]["get"]["operationId"] == "getWorkvisits"
    assert paths["/workvisits/{id}"]["delete"]["operationId"] == "deleteWorkvisits"
    # Existing operationIds are untouched
    assert paths["/keep"]["get"]["operationId"] == "customName"


def test_apply_autogen_operation_ids_uniqueness(spec_manager):
    """Generated names that collide with existing ones get numeric suffixes."""
    spec = {
        "paths": {
            "/things": {"get": {"operationId": "listThings", "responses": {}}},
            "/other/things": {"get": {"responses": {}}},
        }
    }

    spec_manager._apply_autogen_operation_ids(spec)

    assert spec["paths"]["/other/things"]["get"]["operationId"] == "listThings2"


def test_sanitize_schema_quirks(spec_manager):
    """Recurring authoring bugs found by scripts/vet_specs.py are repaired."""
    spec = {
        "paths": {
            "/download": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/octet-stream": {"schema": {"type": "file"}}
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Ern": {
                    "type": "string",
                    "pattern": "^ern:(?<cloud>[^:]+):(?<lookbehind>x)$",
                },
                "Lookbehind": {"type": "string", "pattern": "(?<=a)b(?<!c)"},
                "Bounded": {
                    "type": "integer",
                    "maximum": 10,
                    "exclusiveMaximum": True,
                },
                "BoundedFalse": {
                    "type": "integer",
                    "minimum": 1,
                    "exclusiveMinimum": False,
                },
                "SortList": {
                    "type": "array",
                    "items": {"type": "string"},
                    "collectionFormat": "csv",
                },
                "Settings": {
                    "type": "object",
                    "properties": {"collectionFormat": {"type": "string"}},
                },
            }
        },
    }

    spec_manager._sanitize_schema_quirks(spec)

    schemas = spec["components"]["schemas"]
    file_schema = spec["paths"]["/download"]["get"]["responses"]["200"]["content"][
        "application/octet-stream"
    ]["schema"]
    assert file_schema == {"type": "string", "format": "binary"}
    assert schemas["Ern"]["pattern"] == "^ern:(?P<cloud>[^:]+):(?P<lookbehind>x)$"
    # Lookbehind/lookahead syntax is untouched
    assert schemas["Lookbehind"]["pattern"] == "(?<=a)b(?<!c)"
    assert schemas["Bounded"] == {"type": "integer", "maximum": 10}
    assert schemas["BoundedFalse"] == {"type": "integer", "minimum": 1}
    assert schemas["SortList"] == {"type": "array", "items": {"type": "string"}}
    # A property that happens to be named collectionFormat is kept
    assert "collectionFormat" in schemas["Settings"]["properties"]


def test_inject_family_context(spec_manager):
    """Family context from the info block reaches every operation description."""
    spec = {
        "info": {"title": "Digital LOA API", "description": "Digital LOA API"},
        "paths": {
            "/orgs": {
                "get": {"summary": "Marketplace organizations selection"},
                "post": {"description": "Create an organization."},
            },
            "/bare": {"get": {}},
        },
    }
    api_config = APIConfig(name="diloa")

    spec_manager._inject_family_context(spec, "diloa", api_config)

    context = "Part of the Equinix Digital LOA API family (diloa)."
    ops = spec["paths"]
    # Summary-only operations get a description built from the summary
    assert ops["/orgs"]["get"]["description"] == (
        f"Marketplace organizations selection\n\n{context}"
    )
    assert ops["/orgs"]["post"]["description"] == (
        f"Create an organization.\n\n{context}"
    )
    assert ops["/bare"]["get"]["description"] == context

    # Idempotent: a second pass adds nothing
    spec_manager._inject_family_context(spec, "diloa", api_config)
    assert ops["/bare"]["get"]["description"] == context


def test_inject_family_context_config_override(spec_manager):
    """An apis.yaml family description overrides the spec's info.description."""
    spec = {
        "info": {"title": "Digital LOA API", "description": "Digital LOA API"},
        "paths": {"/orgs": {"get": {"summary": "List organizations"}}},
    }
    api_config = APIConfig(
        name="diloa",
        description="Digital Letter of Authorization (LOA) for cross connects.",
    )

    spec_manager._inject_family_context(spec, "diloa", api_config)

    desc = spec["paths"]["/orgs"]["get"]["description"]
    assert "Digital Letter of Authorization (LOA) for cross connects." in desc
    assert desc.startswith("List organizations")


def test_summarize_info_description():
    """Long multi-line info descriptions collapse to a bounded single line."""
    text = "First line\nof a very long description. " * 30
    summary = SpecManager._summarize_info_description(text)
    assert "\n" not in summary
    assert len(summary) <= 301
    assert summary.endswith("…")
    # Short descriptions pass through collapsed but untruncated
    assert SpecManager._summarize_info_description("a  b\nc") == "a b c"


def test_config_family_search_metadata(config):
    """The bundled config carries description/tags for opaque family slugs."""
    diloa = config.get_api_config("diloa")
    assert "Letter of Authorization" in (diloa.description or "")
    assert "interconnection" in diloa.tags

    sts = config.get_api_config("sts")
    assert "Security Token Service" in (sts.description or "")

    # Every family carries at least one grouping tag
    for api_name in config.get_api_names():
        assert config.get_api_config(api_name).tags, f"{api_name} has no tags"
