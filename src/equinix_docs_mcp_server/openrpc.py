"""Export the server's MCP tool catalog as an OpenRPC document.

MCP is itself JSON-RPC: every tool is invoked as ``tools/call`` with
``{"name": <tool>, "arguments": {...}}``. The OpenRPC document models each
tool as a method whose by-name params are the tool arguments, so the catalog
can be rendered by OpenRPC tooling and published as human-readable docs.

Tool input/output schemas each carry their own ``$defs``. Those are hoisted
into shared ``components.schemas`` so the thousands of repeated definitions
across the API families are stored once.
"""

import copy
import hashlib
import html
import json
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastmcp import Client
from jinja2 import Environment, select_autoescape

OPENRPC_VERSION = "1.3.2"
COMPONENT_PREFIX = "#/components/schemas/"
_LOCAL_REF = re.compile(r"^#/(\$defs|definitions)/(?P<name>[^/]+)$")
_UNSAFE_KEY_CHARS = re.compile(r"[^a-zA-Z0-9.\-_]")

# Returned when a tool declares no output schema: the MCP CallToolResult.
CALL_TOOL_RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "description": "MCP CallToolResult returned by tools/call.",
    "properties": {
        "content": {
            "type": "array",
            "description": "Unstructured result content blocks (text, images, ...).",
            "items": {"type": "object"},
        },
        "structuredContent": {
            "type": "object",
            "description": "Structured result, when the tool provides one.",
        },
        "isError": {"type": "boolean"},
    },
    "required": ["content"],
}


def _package_version() -> str:
    try:
        return version("equinix-docs-mcp-server")
    except PackageNotFoundError:
        return "0.0.0"


def _local_defs(schema: Dict[str, Any]) -> Dict[str, Any]:
    defs: Dict[str, Any] = {}
    defs.update(schema.get("definitions") or {})
    defs.update(schema.get("$defs") or {})
    return defs


def _walk_refs(node: Any) -> Iterable[str]:
    """Yield the names of every local ``#/$defs/<name>`` ref under ``node``."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            match = _LOCAL_REF.match(ref)
            if match:
                yield match.group("name")
        for value in node.values():
            yield from _walk_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_refs(value)


def _rewrite_refs(node: Any, names: Dict[str, str]) -> Any:
    """Return ``node`` with local refs pointed at component schema keys."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                match = _LOCAL_REF.match(value)
                if match and match.group("name") in names:
                    value = COMPONENT_PREFIX + names[match.group("name")]
            out[key] = _rewrite_refs(value, names)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(value, names) for value in node]
    return node


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha1(_canonical(value).encode("utf-8")).hexdigest()[:8]


def _component_key(name: str) -> str:
    return _UNSAFE_KEY_CHARS.sub("_", name)


class _SchemaPool:
    """Assigns shared component names to per-tool ``$defs``.

    Each definition is identified by the content of its transitive closure
    (itself plus every def it references), so identical definitions from
    different tools collapse into one component. A name that maps to a
    single closure keeps its bare name; a name with conflicting variants is
    suffixed with a short content hash. Names therefore depend only on
    content: adding a tool never renames another tool's schemas unless it
    introduces a conflicting variant of the same name.
    """

    def __init__(self, owners: List[Tuple[str, Dict[str, Any]]]):
        digests: Dict[str, Dict[str, str]] = {}  # owner -> def name -> digest
        variants: Dict[str, Set[str]] = {}
        defs_by_owner: Dict[str, Dict[str, Any]] = {}
        for owner, schema in owners:
            defs = _local_defs(schema)
            if not defs:
                continue
            defs_by_owner[owner] = defs
            digests[owner] = {}
            for name in defs:
                closure = self._closure(name, defs)
                digest = _digest({n: defs[n] for n in sorted(closure)})
                digests[owner][name] = digest
                variants.setdefault(name, set()).add(digest)

        self.components: Dict[str, Any] = {}
        self._names: Dict[str, Dict[str, str]] = {}
        for owner, defs in defs_by_owner.items():
            names = {
                name: _component_key(
                    name
                    if len(variants[name]) == 1
                    else f"{name}_{digests[owner][name]}"
                )
                for name in defs
            }
            self._names[owner] = names
            for name, body in defs.items():
                self.components.setdefault(names[name], _rewrite_refs(body, names))

    @staticmethod
    def _closure(name: str, defs: Dict[str, Any]) -> Set[str]:
        seen: Set[str] = set()
        stack = [name]
        while stack:
            current = stack.pop()
            if current in seen or current not in defs:
                continue
            seen.add(current)
            stack.extend(_walk_refs(defs[current]))
        return seen

    def resolve(self, owner: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Strip ``$defs`` from ``schema`` and point its refs at components."""
        schema = {k: v for k, v in schema.items() if k not in ("$defs", "definitions")}
        return _rewrite_refs(schema, self._names.get(owner, {}))


# Inline subschemas at least this large (compact JSON chars) that occur more
# than once are hoisted into components.
HOIST_MIN_CHARS = 300

# Keywords whose values are instance data, not subschemas; never hoisted.
DATA_KEYWORDS = frozenset({"example", "examples", "default", "enum", "const"})


class _Hoister:
    """Moves repeated inline subschemas into shared components.

    FastMCP inlines output schemas, so large response models (e.g. a Fabric
    connection) would otherwise repeat in full for every operation that
    returns them. Hoisted schemas are named after their title or the
    property they first appear under, with a content hash suffix only when
    that name is ambiguous.
    """

    def __init__(self, components: Dict[str, Any]):
        self.components = components
        self._counts: Dict[str, int] = {}
        self._hints: Dict[str, str] = {}
        self._names: Dict[str, str] = {}
        for name, body in components.items():
            key = _canonical(body)
            self._names.setdefault(key, name)
            self._counts[key] = self._counts.get(key, 0) + 1

    def count(self, node: Any, hint: str, top: bool = False) -> None:
        """Record every large subschema of ``node``; call for all roots first."""
        if isinstance(node, dict):
            key = _canonical(node)
            if not top and len(key) >= HOIST_MIN_CHARS:
                self._counts[key] = self._counts.get(key, 0) + 1
                title = node.get("title")
                self._hints.setdefault(key, title if isinstance(title, str) else hint)
            for child, child_hint in self._children(node, hint):
                self.count(child, child_hint)
        elif isinstance(node, list):
            for item in node:
                self.count(item, hint)

    @staticmethod
    def _children(node: Dict[str, Any], hint: str) -> Iterable[Tuple[Any, str]]:
        for key, value in node.items():
            if key in DATA_KEYWORDS:
                continue
            if key == "properties" and isinstance(value, dict):
                for prop, schema in value.items():
                    yield schema, prop
            elif key == "items":
                yield value, f"{hint}Item"
            else:
                yield value, hint

    def assign_names(self) -> None:
        """Name every repeated subschema that is not already a component."""
        by_hint: Dict[str, List[str]] = {}
        for key, n in self._counts.items():
            if n > 1 and key not in self._names:
                hint = self._hints[key]
                hint = _component_key(hint[:1].upper() + hint[1:]) or "Schema"
                by_hint.setdefault(hint, []).append(key)
        for hint, keys in by_hint.items():
            for key in keys:
                unique = len(keys) == 1 and hint not in self.components
                self._names[key] = (
                    hint if unique else f"{hint}_{_digest(json.loads(key))}"
                )

    def replace(self, node: Any, top: bool = False) -> Any:
        """Return ``node`` with repeated subschemas replaced by refs."""
        if isinstance(node, dict):
            key = _canonical(node)
            if not top and self._counts.get(key, 0) > 1 and key in self._names:
                name = self._names[key]
                if name not in self.components:
                    self.components[name] = self.replace(node, top=True)
                return {"$ref": COMPONENT_PREFIX + name}
            return {
                k: v if k in DATA_KEYWORDS else self.replace(v) for k, v in node.items()
            }
        if isinstance(node, list):
            return [self.replace(item) for item in node]
        return node


def _prune_unreferenced(components: Dict[str, Any], roots: List[Any]) -> Dict[str, Any]:
    """Keep only the components reachable from ``roots``."""
    prefix = len(COMPONENT_PREFIX)
    keep: Set[str] = set()
    stack = [ref[prefix:] for root in roots for ref in _walk_component_refs(root)]
    while stack:
        name = stack.pop()
        if name in keep or name not in components:
            continue
        keep.add(name)
        stack.extend(ref[prefix:] for ref in _walk_component_refs(components[name]))
    return {name: body for name, body in components.items() if name in keep}


def _walk_component_refs(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(COMPONENT_PREFIX):
            yield ref
        for value in node.values():
            yield from _walk_component_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_component_refs(value)


def _summary(description: str, limit: int = 120) -> str:
    first = description.strip().split("\n", 1)[0].strip()
    sentence = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0]
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rstrip() + "…"
    return sentence


GROUP_TAGS = ("docs", "workflows")


def _ordered_tags(name: str, tags: Iterable[str], families: Iterable[str]) -> List[str]:
    """Order tags with the grouping tag first.

    API tools group by their family namespace (the ``<family>_`` name prefix;
    family *tags* such as ``billing`` can be shared by several families),
    other tools by their docs/workflows tag.
    """
    tags = set(tags) - {"equinix"}
    matches = [f for f in families if name.startswith(f"{f}_")]
    group = max(matches, key=len) if matches else None
    if group is None:
        group = next((t for t in GROUP_TAGS if t in tags), None)
    if group is None:
        return sorted(tags)
    return [group, *sorted(tags - {group})]


def build_openrpc_document(
    tools: List[Dict[str, Any]],
    families: Iterable[str],
    title: str = "Equinix Docs MCP Server",
) -> Dict[str, Any]:
    """Build an OpenRPC document from MCP tool definitions.

    Args:
        tools: Tools as MCP wire dicts (``name``, ``description``,
            ``inputSchema``, optional ``outputSchema``/``annotations``/
            ``title``) plus a ``tags`` list.
        families: API family names, used to pick each method's leading tag.
        title: The document title.
    """
    families = list(families)
    tools = sorted(tools, key=lambda t: t["name"])

    owners: List[Tuple[str, Dict[str, Any]]] = []
    for tool in tools:
        owners.append((tool["name"], tool.get("inputSchema") or {}))
        if tool.get("outputSchema"):
            owners.append((f"{tool['name']}.result", tool["outputSchema"]))
    pool = _SchemaPool(owners)

    methods = []
    for tool in tools:
        name = tool["name"]
        description = tool.get("description") or ""
        input_schema = pool.resolve(name, tool.get("inputSchema") or {})
        required = set(input_schema.get("required") or [])

        params = []
        for param_name, param_schema in (input_schema.get("properties") or {}).items():
            param: Dict[str, Any] = {"name": param_name}
            if isinstance(param_schema, dict) and param_schema.get("description"):
                param["description"] = param_schema["description"]
            param["required"] = param_name in required
            param["schema"] = param_schema
            params.append(param)
        # OpenRPC expects required params ahead of optional ones.
        params.sort(key=lambda p: not p["required"])

        if tool.get("outputSchema"):
            result = {
                "name": f"{name}Result",
                "description": "structuredContent of the tool's CallToolResult.",
                "schema": pool.resolve(f"{name}.result", tool["outputSchema"]),
            }
        else:
            result = {
                "name": f"{name}Result",
                "schema": {"$ref": COMPONENT_PREFIX + "CallToolResult"},
            }

        method: Dict[str, Any] = {"name": name}
        summary = tool.get("title") or _summary(description)
        if summary:
            method["summary"] = summary
        if description:
            method["description"] = description
        tags = _ordered_tags(name, tool.get("tags") or [], families)
        if tags:
            method["tags"] = [{"name": tag} for tag in tags]
        method["paramStructure"] = "by-name"
        method["params"] = params
        method["result"] = result
        if tool.get("annotations"):
            method["x-mcp-annotations"] = tool["annotations"]
        methods.append(method)

    hoister = _Hoister(pool.components)
    for name, body in pool.components.items():
        hoister.count(body, name, top=True)
    for method in methods:
        for param in method["params"]:
            hoister.count(param["schema"], param["name"])
        hoister.count(method["result"]["schema"], method["result"]["name"])
    hoister.assign_names()
    for name in list(pool.components):
        pool.components[name] = hoister.replace(pool.components[name], top=True)
    for method in methods:
        for param in method["params"]:
            param["schema"] = hoister.replace(param["schema"])
        method["result"]["schema"] = hoister.replace(method["result"]["schema"])

    components = _prune_unreferenced(
        dict(sorted(hoister.components.items())),
        [[m["params"], m["result"]] for m in methods],
    )
    components["CallToolResult"] = copy.deepcopy(CALL_TOOL_RESULT_SCHEMA)

    return {
        "openrpc": OPENRPC_VERSION,
        "info": {
            "title": title,
            "version": _package_version(),
            "description": (
                "MCP tools exposed by equinix-docs-mcp-server, described as "
                "OpenRPC methods. Each method is an MCP tool: call it with the "
                'JSON-RPC request `tools/call` and params `{"name": '
                '"<method>", "arguments": {<params>}}`. API tools are '
                "generated from the Equinix OpenAPI specs and named "
                "`<family>_<operationId>`; by default the server lists only "
                "the docs tools plus `search_tools`/`call_tool`, and the API "
                "tools documented here are reached through them."
            ),
            "license": {
                "name": "MIT",
                "url": "https://github.com/equinix-labs/equinix-docs-mcp-server/blob/main/LICENSE",
            },
        },
        "externalDocs": {
            "description": "equinix-docs-mcp-server on GitHub",
            "url": "https://github.com/equinix-labs/equinix-docs-mcp-server",
        },
        "methods": methods,
        "components": {"schemas": components},
    }


async def collect_tools(server: Any) -> List[Dict[str, Any]]:
    """List every tool of an initialized ``EquinixMCPServer`` as MCP dicts.

    The server should be built with ``tool_catalog="full"`` so the API tools
    are listed directly instead of behind the search transform.
    """
    tags_by_name = {tool.name: tool.tags for tool in await server.mcp.list_tools()}
    async with Client(server.mcp) as client:
        wire_tools = await client.list_tools()

    tools = []
    for tool in wire_tools:
        data = tool.model_dump(by_alias=True, exclude_none=True, mode="json")
        data.pop("_meta", None)
        data["tags"] = sorted(tags_by_name.get(tool.name, ()))
        tools.append(data)
    return tools


# --- HTML rendering -------------------------------------------------------

_REF_HTML = re.compile(
    r"(&quot;\$ref&quot;: &quot;)"
    + re.escape(html.escape(COMPONENT_PREFIX))
    + r"([^&]+)(&quot;)"
)


def _schema_html(schema: Any) -> str:
    """Pretty-print a schema as escaped JSON with refs turned into links."""
    text = html.escape(json.dumps(schema, indent=2, ensure_ascii=False))
    return _REF_HTML.sub(
        lambda m: f'{m.group(1)}<a href="#schema-{m.group(2)}">'
        f"{html.escape(COMPONENT_PREFIX)}{m.group(2)}</a>{m.group(3)}",
        text,
    )


def _type_label(schema: Any) -> str:
    if not isinstance(schema, dict):
        return ""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            labels = [_type_label(s) for s in schema[key]]
            return " | ".join(label for label in labels if label)
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(kind)
    if kind == "array":
        return f"{_type_label(schema.get('items')) or 'any'}[]"
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"][:6]) + (
            " | …" if len(schema["enum"]) > 6 else ""
        )
    return kind or ""


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ doc.info.title }} · MCP tools</title>
<style>
:root { --bg:#fff; --fg:#1b1f24; --muted:#59636e; --line:#d1d9e0; --card:#f6f8fa;
  --accent:#0969da; --ro:#1a7f37; --warn:#9a6700; --bad:#cf222e; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3;
  --muted:#9198a1; --line:#3d444d; --card:#151b23; --accent:#4493f8;
  --ro:#3fb950; --warn:#d29922; --bad:#f85149; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
a { color:var(--accent); }
code, pre { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:13px; }
pre { background:var(--card); border:1px solid var(--line); border-radius:6px;
  padding:10px; overflow:auto; max-height:480px; }
.layout { display:grid; grid-template-columns:280px minmax(0,1fr); }
nav { position:sticky; top:0; height:100vh; overflow:auto; padding:16px;
  border-right:1px solid var(--line); }
nav input { width:100%; padding:6px 8px; margin-bottom:12px; font:inherit;
  border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); }
nav ul { list-style:none; margin:0; padding:0; }
nav li a { display:flex; justify-content:space-between; padding:2px 0; text-decoration:none; }
nav li span { color:var(--muted); }
main { padding:24px 32px; max-width:1100px; }
header p { color:var(--muted); }
section.group > h2 { border-bottom:1px solid var(--line); padding-bottom:4px; margin-top:40px; }
details.method { border:1px solid var(--line); border-radius:6px; margin:8px 0; }
details.method > summary { cursor:pointer; padding:8px 12px; list-style:none; }
details.method > summary::-webkit-details-marker { display:none; }
details.method[open] > summary { border-bottom:1px solid var(--line); background:var(--card); }
.body { padding:4px 16px 12px; }
.name { font-weight:600; font-family:ui-monospace,Consolas,monospace; }
.sum { color:var(--muted); margin-left:8px; }
.pill { display:inline-block; font-size:11px; padding:0 6px; border-radius:10px;
  border:1px solid currentColor; margin-left:6px; vertical-align:middle; }
.ro { color:var(--ro); } .idem { color:var(--warn); } .destr { color:var(--bad); }
table { border-collapse:collapse; width:100%; margin:8px 0; }
th, td { text-align:left; border-bottom:1px solid var(--line); padding:4px 8px; vertical-align:top; }
td.desc { color:var(--muted); }
.desc-block { white-space:pre-wrap; }
@media (max-width:800px) { .layout { display:block; }
  nav { position:static; height:auto; border-right:0; border-bottom:1px solid var(--line); }
  main { padding:16px; } }
</style>
</head>
<body>
<div class="layout">
<nav>
  <input id="filter" type="search" placeholder="Filter {{ doc.methods|length }} tools…" aria-label="Filter tools">
  <ul>
  {% for group, methods in groups %}
    <li><a href="#group-{{ group }}">{{ group }} <span>{{ methods|length }}</span></a></li>
  {% endfor %}
    <li><a href="#schemas">schemas <span>{{ doc.components.schemas|length }}</span></a></li>
  </ul>
</nav>
<main>
<header>
  <h1>{{ doc.info.title }} <small>v{{ doc.info.version }}</small></h1>
  <p>{{ doc.info.description }}</p>
  <p>Generated from <a href="openrpc.json">openrpc.json</a> (OpenRPC {{ doc.openrpc }}).</p>
</header>
{% for group, methods in groups %}
<section class="group" id="group-{{ group }}">
<h2>{{ group }}</h2>
{% for m in methods %}
<details class="method" id="{{ m.name }}" data-search="{{ (m.name ~ ' ' ~ (m.summary or ''))|lower }}">
<summary><span class="name">{{ m.name }}</span>
{%- set a = m.get('x-mcp-annotations', {}) %}
{%- if a.readOnlyHint %}<span class="pill ro">read-only</span>{% endif %}
{%- if a.destructiveHint and not a.readOnlyHint %}<span class="pill destr">destructive</span>{% endif %}
{%- if a.idempotentHint and not a.readOnlyHint %}<span class="pill idem">idempotent</span>{% endif %}
<span class="sum">{{ m.summary or '' }}</span></summary>
<div class="body">
{% if m.description and m.description != m.summary %}<p class="desc-block">{{ m.description }}</p>{% endif %}
{% if m.tags %}<p>Tags: {% for t in m.tags %}<code>{{ t.name }}</code> {% endfor %}</p>{% endif %}
{% if m.params %}
<table><thead><tr><th>Param</th><th>Type</th><th>Required</th><th>Description</th></tr></thead><tbody>
{% for p in m.params %}<tr><td><code>{{ p.name }}</code></td><td><code>{{ type_label(p.schema) }}</code></td>
<td>{{ 'yes' if p.required else '' }}</td><td class="desc">{{ p.description or '' }}</td></tr>
{% endfor %}</tbody></table>
<details><summary>Params schema</summary><pre>{{ schema_html(params_schema(m))|safe }}</pre></details>
{% else %}<p>No params.</p>{% endif %}
<details><summary>Result: <code>{{ type_label(m.result.schema) or 'object' }}</code></summary>
<pre>{{ schema_html(m.result.schema)|safe }}</pre></details>
</div>
</details>
{% endfor %}
</section>
{% endfor %}
<section class="group" id="schemas">
<h2>schemas</h2>
{% for name, schema in doc.components.schemas.items() %}
<details class="method" id="schema-{{ name }}"><summary><span class="name">{{ name }}</span></summary>
<div class="body"><pre>{{ schema_html(schema)|safe }}</pre></div></details>
{% endfor %}
</section>
</main>
</div>
<script>
// Open a linked method or schema when navigating to its anchor.
function openTarget() {
  var el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (el && el.tagName === "DETAILS") { el.open = true; el.scrollIntoView(); }
}
window.addEventListener("hashchange", openTarget);
openTarget();
document.getElementById("filter").addEventListener("input", function (e) {
  var q = e.target.value.trim().toLowerCase();
  document.querySelectorAll("details.method[data-search]").forEach(function (d) {
    d.style.display = !q || d.dataset.search.indexOf(q) !== -1 ? "" : "none";
  });
});
</script>
</body>
</html>
"""


def render_html(doc: Dict[str, Any]) -> str:
    """Render an OpenRPC document as a single self-contained HTML page."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for method in doc["methods"]:
        tags = method.get("tags") or []
        group = tags[0]["name"] if tags else "other"
        groups.setdefault(group, []).append(method)
    # Docs tools first, then API families alphabetically.
    ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "docs", kv[0]))

    def params_schema(method: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {p["name"]: p["schema"] for p in method["params"]},
            "required": [p["name"] for p in method["params"] if p.get("required")],
        }

    env = Environment(autoescape=select_autoescape(default=True))
    template = env.from_string(_TEMPLATE)
    return template.render(
        doc=doc,
        groups=ordered,
        schema_html=_schema_html,
        type_label=_type_label,
        params_schema=params_schema,
    )


async def export_openrpc(
    config_path: Optional[str], output_dir: str
) -> Tuple[str, str, int]:
    """Build the full server catalog and write openrpc.json + index.html.

    Specs are always refreshed so the output does not depend on the state
    of a local spec cache.

    Returns:
        The JSON path, the HTML path, and the number of methods written.
    """

    from .main import EquinixMCPServer

    server = EquinixMCPServer(config_path, tool_catalog="full")
    await server.initialize(force_update_specs=True)
    tools = await collect_tools(server)
    doc = build_openrpc_document(tools, families=server.config.apis)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "openrpc.json"
    html_path = out / "index.html"
    # Fixed LF line endings keep the committed output identical across OSes.
    json_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    html_path.write_text(render_html(doc), encoding="utf-8", newline="\n")
    return str(json_path), str(html_path), len(doc["methods"])
