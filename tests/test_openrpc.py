"""Tests for the OpenRPC export of the MCP tool catalog."""

import json

import pytest
from test_main import make_server

from equinix_docs_mcp_server.openrpc import (
    COMPONENT_PREFIX,
    HOIST_MIN_CHARS,
    build_openrpc_document,
    collect_tools,
    render_html,
)


def ref(name: str) -> dict:
    return {"$ref": COMPONENT_PREFIX + name}


def big_object(marker: str) -> dict:
    """An inline schema large enough to be hoisted."""
    return {
        "type": "object",
        "description": marker + " " + "x" * HOIST_MIN_CHARS,
        "properties": {"id": {"type": "string"}},
    }


def tool(name: str, input_schema: dict, **extra) -> dict:
    return {"name": name, "inputSchema": input_schema, **extra}


def methods_by_name(doc: dict) -> dict:
    return {m["name"]: m for m in doc["methods"]}


def test_tools_become_by_name_methods():
    doc = build_openrpc_document(
        [
            tool(
                "metal_findPlans",
                {
                    "type": "object",
                    "properties": {
                        "include": {"type": "string", "description": "Nested"},
                        "id": {"type": "string"},
                    },
                    "required": ["id"],
                },
                description="List plans. Returns every plan.",
                tags=["equinix", "metal", "compute"],
                annotations={"readOnlyHint": True},
            )
        ],
        families=["metal"],
    )

    assert doc["openrpc"] == "1.3.2"
    method = doc["methods"][0]
    assert method["name"] == "metal_findPlans"
    assert method["summary"] == "List plans."
    assert method["paramStructure"] == "by-name"
    # Required params come first; the family tag leads and "equinix" is dropped
    assert [p["name"] for p in method["params"]] == ["id", "include"]
    assert method["params"][0]["required"] is True
    assert method["params"][1]["description"] == "Nested"
    assert [t["name"] for t in method["tags"]] == ["metal", "compute"]
    assert method["x-mcp-annotations"] == {"readOnlyHint": True}
    assert method["result"]["schema"] == ref("CallToolResult")
    assert "CallToolResult" in doc["components"]["schemas"]


def test_methods_group_by_family_namespace_not_shared_tags():
    doc = build_openrpc_document(
        [
            tool("billingv1_getInvoice", {}, tags=["billingv1", "billing"]),
            tool("billing_getAccount", {}, tags=["billing"]),
            tool("workflow__list_metros", {}, tags=["workflows"]),
        ],
        families=["billing", "billingv1"],
    )

    leading = {m["name"]: m["tags"][0]["name"] for m in doc["methods"]}
    assert leading == {
        "billingv1_getInvoice": "billingv1",
        "billing_getAccount": "billing",
        "workflow__list_metros": "workflows",
    }


def test_identical_defs_are_shared_and_conflicts_are_suffixed():
    plan = {"type": "object", "properties": {"slug": {"type": "string"}}}
    other_plan = {"type": "object", "properties": {"name": {"type": "string"}}}

    def schema(plan_def: dict) -> dict:
        return {
            "type": "object",
            "properties": {"plan": {"$ref": "#/$defs/Plan"}},
            "$defs": {"Plan": plan_def, "Wrapper": {"$ref": "#/$defs/Plan"}},
        }

    doc = build_openrpc_document(
        [
            tool("a_one", schema(plan)),
            tool("a_two", schema(plan)),
            tool("b_three", schema(other_plan)),
        ],
        families=[],
    )
    schemas = doc["components"]["schemas"]
    methods = methods_by_name(doc)

    one = methods["a_one"]["params"][0]["schema"]
    two = methods["a_two"]["params"][0]["schema"]
    three = methods["b_three"]["params"][0]["schema"]
    # Same content -> same component; conflicting variants are hash-suffixed
    assert one == two
    assert one != three
    for schema_ref in (one, three):
        name = schema_ref["$ref"][len(COMPONENT_PREFIX) :]
        assert name.startswith("Plan_")
        assert name in schemas
    # Unreferenced defs (Wrapper) are pruned
    assert not any(name.startswith("Wrapper") for name in schemas)


def test_repeated_inline_schemas_are_hoisted_but_examples_are_not():
    shared = big_object("Connection")
    example = {"id": "abc", "padding": "y" * HOIST_MIN_CHARS}
    tools = [
        tool(
            name,
            {"type": "object", "properties": {}},
            outputSchema={
                "type": "object",
                "description": name,
                "properties": {
                    "connection": shared,
                    "note": {"type": "object", "example": example},
                },
            },
        )
        for name in ("fabric_getConnection", "fabric_updateConnection")
    ]

    doc = build_openrpc_document(tools, families=["fabric"])

    schemas = doc["components"]["schemas"]
    assert schemas["Connection"] == shared
    for method in doc["methods"]:
        result = method["result"]["schema"]
        assert result["properties"]["connection"] == ref("Connection")
        assert result["properties"]["note"] == ref("Note")
    # The example stays inline in its schema instead of becoming a component
    assert schemas["Note"]["example"] == example
    assert example not in schemas.values()


def test_output_is_deterministic_regardless_of_tool_order():
    tools = [
        tool("b_two", {"type": "object", "properties": {"x": big_object("X")}}),
        tool("a_one", {"type": "object", "properties": {"x": big_object("X")}}),
    ]

    first = build_openrpc_document(tools, families=[])
    second = build_openrpc_document(list(reversed(tools)), families=[])

    assert json.dumps(first) == json.dumps(second)
    assert [m["name"] for m in first["methods"]] == ["a_one", "b_two"]


def test_render_html_links_refs_and_groups_by_family():
    doc = build_openrpc_document(
        [
            tool(
                "fabric_getConnection",
                {"type": "object", "properties": {"c": big_object("Conn")}},
                tags=["equinix", "fabric"],
            ),
            tool(
                "fabric_listConnections",
                {"type": "object", "properties": {"c": big_object("Conn")}},
                tags=["equinix", "fabric"],
            ),
            tool("search", {"type": "object", "properties": {}}, tags=["docs"]),
        ],
        families=["fabric"],
    )

    page = render_html(doc)

    assert 'id="group-docs"' in page
    assert 'id="group-fabric"' in page
    assert page.index('id="group-docs"') < page.index('id="group-fabric"')
    assert 'href="#schema-C"' in page
    assert 'id="schema-C"' in page


def test_render_html_escapes_descriptions():
    doc = build_openrpc_document(
        [tool("x_evil", {}, description="<script>alert(1)</script>")],
        families=[],
    )

    page = render_html(doc)

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


@pytest.mark.asyncio
async def test_collect_tools_from_server():
    server = make_server(tool_catalog="full")
    await server.initialize()

    tools = await collect_tools(server)
    doc = build_openrpc_document(tools, families=server.config.apis)

    methods = methods_by_name(doc)
    assert "metal_findPlans" in methods
    assert methods["metal_findPlans"]["tags"][0] == {"name": "metal"}
    assert methods["search"]["tags"][0] == {"name": "docs"}
    assert [p["name"] for p in methods["search"]["params"]] == ["query", "limit"]
