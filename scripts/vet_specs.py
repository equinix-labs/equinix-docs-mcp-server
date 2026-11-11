"""Vet every generated API tool through the MCP harness.

For each tool generated from the configured OpenAPI specs:
1. Generate sample arguments from the tool's input schema.
2. Point the family's HTTP client at a MockTransport that returns a dummy
   response generated from the tool's output schema.
3. Call the tool through an in-memory MCP client and record the outcome.
4. Separately, verify each tool is discoverable through search_tools.

No generic package exists for this, so the schema sampler below covers the
JSON Schema constructs the Equinix specs actually use ($ref/$defs, allOf,
oneOf/anyOf, enums, formats, type unions).

Usage:  python scripts/vet_specs.py [--families a,b,c] [--report vet-report.md]
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import exrex  # noqa: E402
import httpx2  # noqa: E402
from fastmcp import Client  # noqa: E402
from fastmcp.utilities.openapi import parse_openapi_to_http_routes  # noqa: E402

from equinix_docs_mcp_server.main import (  # noqa: E402
    DOCS_TOOL_NAMES,
    EquinixMCPServer,
)
from equinix_docs_mcp_server.openapi_annotations import is_read_by_name  # noqa: E402

MAX_DEPTH = 8

SAMPLE_FORMATS = {
    "date": "2026-01-01",
    "date-time": "2026-01-01T00:00:00Z",
    "uuid": "00000000-0000-4000-8000-000000000000",
    "email": "user@example.com",
    "uri": "https://example.com/x",
    "url": "https://example.com/x",
    "hostname": "example.com",
    "ipv4": "192.0.2.1",
    "ipv6": "2001:db8::1",
}


def resolve_ref(ref: str, root: dict):
    if not ref.startswith("#/"):
        return None
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def merge_all_of(schema: dict, root: dict, depth: int = 0) -> dict:
    """Flatten a schema's allOf chain (recursively) into one object schema.

    Keeps the outer schema's own properties/required/type and merges every
    branch, resolving $refs and nested allOfs along the way.
    """
    merged = {
        "properties": dict(schema.get("properties", {})),
        "required": list(schema.get("required", [])),
    }
    if "type" in schema:
        merged["type"] = schema["type"]

    if depth > MAX_DEPTH:
        return merged

    for sub in schema.get("allOf", []):
        if isinstance(sub, dict) and "$ref" in sub:
            sub = resolve_ref(sub["$ref"], root) or {}
        if not isinstance(sub, dict):
            continue
        sub_merged = merge_all_of(sub, root, depth + 1)
        merged["properties"].update(sub_merged["properties"])
        merged["required"].extend(sub_merged["required"])
        if "type" in sub_merged and "type" not in merged:
            merged["type"] = sub_merged["type"]
    return merged


def sample_from_schema(schema, root=None, depth=0):
    """Produce a value satisfying (a pragmatic subset of) a JSON Schema."""
    if not isinstance(schema, dict):
        return None
    if depth > MAX_DEPTH:
        # Depth cap: return the cheapest value of roughly the right shape
        # instead of None, which fails non-nullable schemas.
        stype = schema.get("type")
        if stype == "array" or "items" in schema:
            return []
        if stype in (None, "object") or "properties" in schema:
            return {}
        return sample_from_schema({**schema, "properties": {}}, root, 0)
    root = root if root is not None else schema

    if "$ref" in schema:
        target = resolve_ref(schema["$ref"], root)
        if target is None:
            raise ValueError(f"unresolvable $ref {schema['$ref']}")
        return sample_from_schema(target, root, depth + 1)

    if "const" in schema:
        return schema["const"]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]

    if "allOf" in schema:
        merged = merge_all_of(schema, root)
        if not merged.get("properties") and merged.get("type") not in (None, "object"):
            return sample_from_schema(
                {k: v for k, v in merged.items() if k != "allOf"}, root, depth + 1
            )
        return sample_from_schema({"type": "object", **merged}, root, depth + 1)

    for key in ("oneOf", "anyOf"):
        if key in schema and schema[key]:
            return sample_from_schema(schema[key][0], root, depth + 1)

    stype = schema.get("type")
    if isinstance(stype, list):
        non_null = [t for t in stype if t != "null"]
        stype = non_null[0] if non_null else "null"
    if stype is None:
        if "properties" in schema:
            stype = "object"
        elif "items" in schema:
            stype = "array"
        else:
            return "sample"

    if stype == "null":
        return None
    if stype == "boolean":
        return True
    if stype == "integer":
        return max(schema.get("minimum", 1), 1)
    if stype == "number":
        return float(schema.get("minimum", 1))
    if stype == "string":
        if schema.get("format") in SAMPLE_FORMATS:
            return SAMPLE_FORMATS[schema["format"]]
        if "pattern" in schema:
            try:
                return exrex.getone(schema["pattern"])
            except Exception:
                pass
        value = "sample"
        min_len = schema.get("minLength", 0)
        if min_len > len(value):
            value = value.ljust(min_len, "x")
        max_len = schema.get("maxLength")
        if max_len is not None and len(value) > max_len:
            value = value[:max_len] or "s"[:max_len]
        return value
    if stype == "array":
        min_items = schema.get("minItems", 0)
        count = max(min_items, 1)
        item = sample_from_schema(schema.get("items", {}), root, depth + 1)
        return [item] * count
    if stype == "object":
        result = {}
        props = schema.get("properties", {})
        required = schema.get("required", [])
        keys = required if required else list(props)[:2]
        for key in keys:
            if key in props:
                result[key] = sample_from_schema(props[key], root, depth + 1)
            else:
                result[key] = "sample"
        return result
    return "sample"


class VettingServer(EquinixMCPServer):
    """EquinixMCPServer with all API traffic answered by a mock transport."""

    def __init__(self, *args, **kwargs):
        self.holder = {"body": None}
        super().__init__(*args, **kwargs)

    def _build_api_client(self, api_config, spec):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json=self.holder["body"], request=request)

        client = httpx2.AsyncClient(
            base_url="https://api.equinix.com",
            transport=httpx2.MockTransport(handler),
        )
        self._api_clients.append(client)
        return client


def categorize(error_text: str) -> str:
    lowered = error_text.lower()
    if "unresolvable $ref" in lowered:
        return "unresolvable-ref-in-schema"
    if "output" in lowered and ("valid" in lowered or "schema" in lowered):
        return "output-validation"
    if "input" in lowered and "valid" in lowered:
        return "input-validation"
    if "required" in lowered:
        return "missing-required"
    if "path" in lowered and "param" in lowered:
        return "path-parameter"
    return "other"


async def vet(families_filter=None, report_path="vet-report.md"):
    logging.disable(logging.ERROR)

    server = VettingServer(tool_catalog="full")
    await server.initialize()

    families = sorted(name for name, api in server.config.apis.items() if api.enabled)
    if families_filter:
        families = [f for f in families if f in families_filter]
    prefixes = tuple(f"{f}_" for f in families)

    ok = Counter()
    failures = defaultdict(list)  # family -> [(tool, category, message)]
    for family, error in getattr(server, "failed_providers", {}).items():
        if not families_filter or family in families_filter:
            failures[family].append(("<provider>", "provider-build", error[:300]))

    async with Client(server.mcp) as client:
        tools = [
            t
            for t in await client.list_tools()
            if t.name.startswith(prefixes) and t.name not in DOCS_TOOL_NAMES
        ]
        print(f"vetting {len(tools)} tools across {len(families)} families")

        for i, tool in enumerate(tools):
            family = tool.name.split("_", 1)[0]
            try:
                input_schema = tool.input_schema or {}
                args = sample_from_schema(input_schema) or {}
                if not isinstance(args, dict):
                    args = {}

                output_schema = tool.output_schema
                if output_schema:
                    body_schema = output_schema
                    if output_schema.get("x-fastmcp-wrap-result"):
                        body_schema = output_schema.get("properties", {}).get(
                            "result", {}
                        )
                        body_schema = {
                            **body_schema,
                            "$defs": output_schema.get("$defs", {}),
                        }
                    server.holder["body"] = sample_from_schema(body_schema)
                else:
                    server.holder["body"] = {}

                result = await client.call_tool(tool.name, args)
                if result.is_error:
                    text = result.content[0].text if result.content else "unknown"
                    failures[family].append((tool.name, categorize(text), text[:300]))
                else:
                    ok[family] += 1
            except Exception as e:
                failures[family].append((tool.name, categorize(str(e)), str(e)[:300]))

            if (i + 1) % 250 == 0:
                print(f"  {i + 1}/{len(tools)} vetted")

    # POST tools the name heuristic marks read-only, and whether an overlay
    # (x-mcp-annotations) turned them back into writes.
    read_only = {
        t.name: bool(t.annotations and t.annotations.readOnlyHint) for t in tools
    }
    read_by_name = []
    for family in families:
        spec = server.spec_manager.get_provider_spec(family)
        for route in parse_openapi_to_http_routes(spec) if spec else []:
            name = f"{family}_{route.operation_id}"
            if is_read_by_name(route) and name in read_only:
                read_by_name.append((name, route.path, read_only[name]))

    # Search discoverability pass
    search_server = VettingServer(tool_catalog="search")
    await search_server.initialize()
    not_searchable = []
    async with Client(search_server.mcp) as client:
        for tool in tools:
            r = await client.call_tool("search_tools", {"query": tool.name})
            # Each match is a "### <name> — <title>" heading
            names = {
                line[4:].split(" — ", 1)[0]
                for line in r.content[0].text.splitlines()
                if line.startswith("### ")
            }
            if tool.name not in names:
                not_searchable.append(tool.name)

    # Report
    lines = ["# Spec vetting report", ""]
    total_fail = sum(len(v) for v in failures.values())
    lines.append(
        f"{sum(ok.values())} tools OK, {total_fail} failures, "
        f"{len(not_searchable)} not discoverable by exact-name search."
    )
    lines.append("")
    lines.append("| family | ok | failed |")
    lines.append("|---|---|---|")
    for family in families:
        lines.append(f"| {family} | {ok[family]} | {len(failures[family])} |")
    lines.append("")

    category_counts = Counter(cat for fails in failures.values() for _, cat, _ in fails)
    if category_counts:
        lines.append("## Failure categories")
        for cat, count in category_counts.most_common():
            lines.append(f"- {cat}: {count}")
        lines.append("")
        lines.append("## Failures by family")
        for family in families:
            if not failures[family]:
                continue
            lines.append(f"### {family}")
            for name, cat, msg in failures[family][:20]:
                lines.append(f"- `{name}` [{cat}]: {msg}")
            if len(failures[family]) > 20:
                lines.append(f"- ... and {len(failures[family]) - 20} more")
            lines.append("")

    if not_searchable:
        lines.append("## Not discoverable via search_tools (exact name query)")
        for name in not_searchable[:50]:
            lines.append(f"- `{name}`")
        lines.append("")

    if read_by_name:
        lines.append("## POST tools marked read-only by name")
        lines.append("")
        lines.append(
            "These POST operations have a read verb in their operationId or a "
            "`/search` or `/validate` path. Tools an overlay marks as writes "
            "are labelled *write (overlay)*."
        )
        lines.append("")
        for name, path, is_read in read_by_name:
            label = "" if is_read else " — write (overlay)"
            lines.append(f"- `{name}` `{path}`{label}")
        lines.append("")

    Path(report_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"report written to {report_path}")
    print(lines[2])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--families", help="comma-separated family filter")
    parser.add_argument("--report", default="vet-report.md")
    args = parser.parse_args()
    families = set(args.families.split(",")) if args.families else None
    asyncio.run(vet(families, args.report))
