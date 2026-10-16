# Spec vetting

`scripts/vet_specs.py` exercises every generated API tool through the real MCP
harness without touching the network:

1. Sample arguments are generated from each tool's input schema (patterns are
   satisfied via `exrex`, `$ref`/`allOf`/`oneOf`/enums/formats are handled).
2. The family's HTTP client is swapped for an `httpx2.MockTransport` that
   returns a dummy body generated from the tool's output schema.
3. Each tool is called through an in-memory FastMCP client, so input
   validation, request building, response parsing, output-schema validation,
   and the formatting middleware all run for real.
4. A second pass verifies each tool is discoverable through `search_tools`.

Run it (from a checkout with cached specs):

```bash
EQUINIX_MCP_CACHE_DIR=$PWD/.cache python scripts/vet_specs.py --report vet-report.md
# or a subset:
python scripts/vet_specs.py --families fabric,network-edge
```

## Findings (2026-08-05, 33 families, 690 tools)

689/690 tools pass. The sweep surfaced three recurring spec authoring bugs,
now repaired generically in `SpecManager._sanitize_schema_quirks` (no
per-family overlays were needed):

| Defect | Affected specs | Fix |
|---|---|---|
| `type: file` (Swagger 2.0 syntax in specs declaring openapi 3.0) | billingv1, reports | rewritten to `type: string, format: binary` |
| ECMAScript regex named groups `(?<name>...)` in `pattern` | access | rewritten to Python-style `(?P<name>...)` (lookbehinds untouched) |
| Boolean draft-4 `exclusiveMaximum`/`exclusiveMinimum` | attachments | dropped, keeping the inclusive bound — converting to the numeric 2020-12 form is rejected by the OpenAPI 3.0 document parser (dialect catch-22) |

Provider construction is also resilient now: a family whose spec fails to
parse is skipped and recorded in `EquinixMCPServer.failed_providers` instead
of aborting startup.

## Known issues

- FastMCP's request builder does not serialize primitive (non-object) JSON
  request bodies and fails with `Unexpected type for 'content', <class 'bool'>`.
  The only affected tool in the 2026-08-05 sweep, `metal_updateBgpSession`,
  went away with the Metal API; candidate for an upstream FastMCP issue if
  another spec declares a bare primitive body.
- Exact-tool-name search misses (6 tools in the 2026-08-05 sweep, e.g.
  `smartview_getAlerts`): these are precisely the largest-schema tools. The
  BM25 index includes all parameter descriptions, so doc-length
  normalization outranks them for short queries that also match many small
  tools. Searching the bare operation name ranks them #1.
  An upstream tuning question for FastMCP's search transform, not a spec
  defect.
