"""Search over the API tool catalog with compact results and a schema lookup.

FastMCP's search transform returns every match as a full tool definition,
output schema included. For the Equinix catalog that is up to ~900K
characters per search, most of it response schemas the agent does not need
to choose a tool. Here search returns a short entry per match, and
``get_tool_schema`` returns one tool's full definition once the agent has
picked it.
"""

import re
from collections.abc import Sequence
from typing import Annotated, Any

import yaml
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec
from mcp_types import TextContent, ToolAnnotations
from pydantic import Field

from .tool_ranking import ToolIndex, is_deprecated, summary_paragraph

SEARCH_TOOL_NAME = "search_tools"
SCHEMA_TOOL_NAME = "get_tool_schema"
CALL_TOOL_NAME = "call_tool"

# A summary longer than this is cut at a sentence or word boundary
_SUMMARY_LIMIT = 300
# Optional parameters are listed by name only, up to this many
_OPTIONAL_LIMIT = 12
_HTML_TAG = re.compile(r"<[^>]+>")


def tool_title(tool: Tool) -> str:
    """The tool's display title, from the tool or its annotations."""
    return tool.title or (tool.annotations.title if tool.annotations else None) or ""


def tool_summary(tool: Tool) -> str:
    """The paragraph describing the operation, shortened.

    Generated API tools end with a paragraph describing the whole API family,
    which is the same for every tool in that family, so it is left out.
    Deprecated operations start with a generic notice; the summary shows
    "Deprecated." and the operation's own description instead.
    """
    text = summary_paragraph(tool.description or "")
    if is_deprecated(tool):
        text = f"Deprecated. {text}".strip()
    # Some specs mark betas with inline HTML such as <font color="red">
    text = " ".join(_HTML_TAG.sub("", text).split())
    if len(text) <= _SUMMARY_LIMIT:
        return text
    cut = text[:_SUMMARY_LIMIT]
    end = cut.rfind(". ")
    if end > _SUMMARY_LIMIT // 2:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def tool_access(tool: Tool) -> str:
    """Read-only, write or destructive, from the tool's annotations."""
    annotations = tool.annotations
    if annotations is None:
        return "unknown"
    if annotations.read_only_hint:
        return "read-only"
    if annotations.destructive_hint:
        return "destructive"
    return "write"


def _resolve(schema: Any, defs: dict[str, Any]) -> Any:
    if isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        prefix = "#/$defs/"
        ref = schema["$ref"]
        if ref.startswith(prefix):
            return defs.get(ref[len(prefix) :], schema)
    return schema


def _type_label(schema: Any, defs: dict[str, Any]) -> str:
    schema = _resolve(schema, defs)
    if not isinstance(schema, dict):
        return "any"
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(str(k) for k in kind if k != "null") or "null"
    if kind == "array":
        return f"{_type_label(schema.get('items'), defs)}[]"
    if isinstance(kind, str) and kind:
        return kind
    for key in ("anyOf", "oneOf"):
        branches = schema.get(key)
        if isinstance(branches, list):
            labels = [_type_label(b, defs) for b in branches]
            labels = [label for label in dict.fromkeys(labels) if label != "null"]
            return " | ".join(labels) or "any"
    if "properties" in schema or "allOf" in schema:
        return "object"
    return "any"


def _enum_label(schema: Any, defs: dict[str, Any]) -> str:
    schema = _resolve(schema, defs)
    if not isinstance(schema, dict):
        return ""
    values = schema.get("enum")
    if not isinstance(values, list) or not values:
        return ""
    shown = "/".join(str(v) for v in values[:6])
    more = f"/… ({len(values)} values)" if len(values) > 6 else ""
    return f", one of {shown}{more}"


def parameter_summary(tool: Tool) -> list[str]:
    """Lines naming the tool's parameters: required with types, then optional."""
    schema = tool.parameters or {}
    props = schema.get("properties") or {}
    if not props:
        return ["Parameters: none"]
    defs = schema.get("$defs") or {}
    required = [n for n in schema.get("required") or [] if n in props]
    optional = [n for n in props if n not in required]

    lines = []
    if required:
        parts = [
            f"`{n}` ({_type_label(props[n], defs)}{_enum_label(props[n], defs)})"
            for n in required
        ]
        lines.append("Required: " + ", ".join(parts))
    if optional:
        shown = ", ".join(f"`{n}`" for n in optional[:_OPTIONAL_LIMIT])
        rest = len(optional) - _OPTIONAL_LIMIT
        more = f", and {rest} more" if rest > 0 else ""
        lines.append(f"Optional: {shown}{more}")
    return lines


def summarize_tools(tools: Sequence[Tool]) -> str:
    """Render search matches as short markdown entries."""
    if not tools:
        return (
            "No tools matched the query. Try other words, such as the "
            "product name and the action (list, get, create, update, delete)."
        )
    blocks = []
    for tool in tools:
        heading = f"### {tool.name}"
        title = tool_title(tool)
        if title:
            heading += f" — {title}"
        lines = [heading, f"Access: {tool_access(tool)}"]
        summary = tool_summary(tool)
        if summary:
            lines.append(summary)
        lines.extend(parameter_summary(tool))
        blocks.append("\n".join(lines))
    footer = (
        f"Call {SCHEMA_TOOL_NAME} with a tool's name for its full parameter "
        f"schema, then run it with {CALL_TOOL_NAME}."
    )
    return "\n\n".join([*blocks, footer])


def tool_definition(tool: Tool, *, output_schema: bool) -> dict[str, Any]:
    """A tool's MCP definition, optionally without its output schema."""
    definition = tool.to_mcp_tool().model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    if not output_schema:
        definition.pop("outputSchema", None)
    # Not useful to the model, and can be large
    definition.pop("_meta", None)
    definition.pop("icons", None)
    return definition


class EquinixSearchTransform(BM25SearchTransform):
    """Tool search that returns compact entries, plus a schema lookup.

    Ranking comes from ``tool_ranking.ToolIndex`` rather than FastMCP's index.

    ``tools/list`` shows the pinned tools, ``search_tools``,
    ``get_tool_schema`` and ``call_tool``.
    """

    def __init__(
        self,
        *,
        max_results: int = 15,
        always_visible: list[str] | None = None,
    ) -> None:
        super().__init__(
            max_results=max_results,
            always_visible=always_visible,
            search_tool_name=SEARCH_TOOL_NAME,
            call_tool_name=CALL_TOOL_NAME,
            search_result_serializer=summarize_tools,
        )
        self._tool_index: ToolIndex | None = None

    async def _search(self, tools: Sequence[Tool], query: str) -> Sequence[Tool]:
        names = tuple(tool.name for tool in tools)
        if self._tool_index is None or self._tool_index.names != names:
            self._tool_index = ToolIndex(tools)
        indices = self._tool_index.query(query, self._max_results)
        return [tools[i] for i in indices]

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        pinned = [t for t in tools if t.name in self._always_visible]
        return [
            *pinned,
            self._make_search_tool(),
            self._make_schema_tool(),
            self._make_call_tool(),
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name == SCHEMA_TOOL_NAME:
            return self._make_schema_tool()
        return await super().get_tool(name, call_next, version=version)

    def _make_search_tool(self) -> Tool:
        # The stock tool declares an output schema for its str | list return,
        # which wraps the text as {"result": "..."} structured content
        return (
            super()
            ._make_search_tool()
            .model_copy(
                update={
                    "output_schema": None,
                    "description": (
                        "Search the Equinix API tools in natural language, for "
                        'example "create network edge device" or "list fabric '
                        'ports". Returns a short entry for each match: name, '
                        "title, access (read-only, write or destructive), "
                        "summary, and parameter names. Use "
                        f"{SCHEMA_TOOL_NAME} for a tool's full schema."
                    ),
                }
            )
        )

    def _make_schema_tool(self) -> Tool:
        transform = self

        async def get_tool_schema(
            name: Annotated[
                str,
                Field(
                    description=(
                        "Name of the API tool, as returned by "
                        f'{SEARCH_TOOL_NAME}, e.g. "fabric_getMetros"'
                    )
                ),
            ],
            output_schema: Annotated[
                bool,
                Field(
                    description=(
                        "Also return the response schema, for example to "
                        "write a call_api_filtered query. It can be large."
                    )
                ),
            ] = False,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            # Only tools the model may discover through search are described
            tools = await transform._get_visible_tools(ctx)
            tool = next((t for t in tools if t.name == name), None)
            if tool is None:
                raise ToolError(
                    f"Unknown tool: {name!r}. Use {SEARCH_TOOL_NAME} to find "
                    "API tool names."
                )
            definition = tool_definition(tool, output_schema=output_schema)
            text = yaml.dump(
                definition,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            )
            return ToolResult(content=[TextContent(type="text", text=text)])

        return Tool.from_function(
            fn=get_tool_schema,
            name=SCHEMA_TOOL_NAME,
            title="Get API Tool Schema",
            description=(
                "Return the full definition of one API tool: description, "
                "input schema with every parameter's description and examples, "
                "and annotations. Call it after search_tools and before "
                "call_tool, to build valid arguments."
            ),
            annotations=ToolAnnotations(
                read_only_hint=True, destructive_hint=False, idempotent_hint=True
            ),
            output_schema=None,
        )
