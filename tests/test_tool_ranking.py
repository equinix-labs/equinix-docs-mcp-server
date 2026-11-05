"""Tests for the API tool search ranking."""

from fastmcp.tools.base import Tool

from equinix_docs_mcp_server.tool_ranking import (
    ToolIndex,
    searchable_text,
    split_identifier,
    tokenize,
)

FAMILY = (
    "Part of the Equinix Network Edge API family (network-edge). Network Edge "
    "is a platform for deploying routers, firewalls and load balancers."
)


def make_tool(name: str, description: str = "", **properties) -> Tool:
    def fn() -> None:
        return None

    tool = Tool.from_function(fn, name=name)
    return tool.model_copy(
        update={
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    key: {"type": "string", "description": text}
                    for key, text in properties.items()
                },
            },
        }
    )


class TestTokenize:
    def test_splits_identifiers(self):
        assert split_identifier("network-edge_createVirtualDevice") == (
            "network edge create Virtual Device"
        )
        assert split_identifier("getHTTPServerByUuid") == "get HTTP Server By Uuid"

    def test_folds_plurals(self):
        assert tokenize("Ports Devices Policies Addresses Boxes status") == [
            "port",
            "device",
            "policy",
            "address",
            "box",
            "status",
        ]

    def test_matches_name_words(self):
        assert tokenize("fabric_searchConnections") == [
            "fabric",
            "search",
            "connection",
        ]


class TestSearchableText:
    def test_keeps_summary_and_family_name(self):
        tool = make_tool(
            "network-edge_createVirtualDevice",
            f"Creates a virtual device.\n\n{FAMILY}",
            metroCode="Metro code for billing and invoices",
        )
        text = searchable_text(tool)
        assert "Creates a virtual device." in text
        assert "Network Edge" in text
        # The rest of the family paragraph and the parameters are left out
        assert "firewalls" not in text
        assert "metroCode" not in text
        assert "invoices" not in text


class TestToolIndex:
    def test_large_create_tool_ranks_first(self):
        """Many documented parameters no longer push a tool down."""
        fields = {f"field{i}": "Device setting for the account" for i in range(60)}
        tools = [
            make_tool("network-edge_getVirtualDeviceByUuid", "Get a device."),
            make_tool("network-edge_createDeviceBackup", "Create a device backup."),
            make_tool(
                "network-edge_createVirtualDevice", "Creates a device.", **fields
            ),
            make_tool("network-edge_pingDeviceByUuid", "Ping a virtual device."),
        ]
        index = ToolIndex(tools)
        best = index.query("create network edge virtual device", 3)
        assert tools[best[0]].name == "network-edge_createVirtualDevice"

    def test_parameter_text_does_not_match(self):
        tools = [
            make_tool("billing_getInvoice", "Get an invoice."),
            make_tool("ne_createDevice", "Create a device.", note="invoice details"),
        ]
        index = ToolIndex(tools)
        assert index.query("invoice", 5) == [0]

    def test_no_matches(self):
        index = ToolIndex([make_tool("fabric_getPorts", "Get ports.")])
        assert index.query("zzz", 5) == []
        assert ToolIndex([]).query("ports", 5) == []
