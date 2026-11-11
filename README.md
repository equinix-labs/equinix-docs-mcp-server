# Equinix Docs and API Specifications MCP Server

This project is an experimental Model Context Protocol (MCP) server, for local use and learning, that provides access to Equinix APIs and documentation. This project is not expected to offer high quality (production-ready) results. This is offered for developers learning about MCP, Equinix APIs and documentation, and their potential integration. 

> [!NOTE]
> By default the API tool catalog is collapsed behind `search_tools`, `get_tool_schema` and `call_tool`, so large API catalogs no longer overwhelm the model's context window. Use `--tool-catalog full` to list every generated tool directly.

## Features

- **API Access**: Fetches and caches Equinix API specifications then exposes operationIds as MCP tools.
   - **Per-family providers**: Each API family (Fabric, Network Edge, Billing, Smart View, …) is served by its own OpenAPI provider; tools are namespaced `<family>_<operationId>` (e.g. `fabric_searchConnections`)
   - **Searchable catalog**: API tools are hidden from `tools/list` and discovered on demand via `search_tools`, following the MCP 2026-07-28 progressive-discovery guidance; `--tool-catalog code-mode` enables FastMCP's experimental sandboxed code execution instead
   - **Tool annotations**: API tools carry MCP behavior hints derived from their HTTP method — `GET` is `readOnlyHint`, `PUT`/`DELETE` are `destructiveHint` + `idempotentHint`, `POST`/`PATCH` are neither read-only nor idempotent — and all are `openWorldHint`; an operation's `x-mcp-annotations` extension (e.g. via an overlay) overrides these
   - **Cache hints**: `tools/list` and related results carry `ttlMs`/`cacheScope` hints per the MCP 2026-07-28 caching utility
   - **API Authentication**: OAuth2 Client Credentials
   - **Configurable Overlays**: Use overlay specifications to normalize API specs before tools are generated from them
   - **Arazzo Workflows (Experimental)**: Define and execute higher-level workflows chaining multiple API operations
- **Documentation Integration**: Search Equinix documentation via sitemap and Lunr search, fetch full markdown content
   - OpenAI MCP compatible `search` and `fetch` tools for ChatGPT Connectors and deep research

## Supported APIs

Any Equinix API specification can be added to the configuration file but operations may need to be filtered and overlays may be needed for this tool to use the spec in the MCP server.
The bundled `apis.yaml` (at `src/equinix_docs_mcp_server/data/config/apis.yaml`) defines API specifications that have been used during development to test behavior; pass `--config` to use your own.

To find catalog APIs not yet configured, run:

```bash
equinix-docs-mcp-server --discover-apis
```

This scans [docs.equinix.com/api-catalog](https://docs.equinix.com/api-catalog) for `openapi.yaml` specs and prints ready-to-paste `apis.yaml` entries for anything new (AsyncAPI-only entries such as `emgv1` are reported but skipped). Add `--write` to append them to the configuration file directly — the *Discover New APIs* GitHub workflow does exactly that on a weekly schedule (or on demand via workflow dispatch) and opens a PR with the additions. Operations missing an `operationId` get one synthesized automatically during spec processing.

## Quick Start

### Installation

The server is published on [PyPI](https://pypi.org/project/equinix-docs-mcp-server/). With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, run it directly (uv fetches a suitable Python automatically):

```bash
uvx --system-certs equinix-docs-mcp-server
```

`--system-certs` makes uv trust your operating system's certificate store, which you need behind a proxy that inspects TLS. If your package index holds back or blocks recently published releases, uv may pick a version it then can't download. In that case, also pass `--exclude-newer` so uv only considers releases older than a cutoff, for example `uvx --system-certs --exclude-newer "3 days" equinix-docs-mcp-server`, or set `exclude-newer` in your [uv configuration](https://docs.astral.sh/uv/reference/settings/#exclude-newer). The same flags work in the MCP client configurations below.

To run the latest unreleased code from `main` instead, use `uvx --system-certs --from git+https://github.com/equinix-labs/equinix-docs-mcp-server equinix-docs-mcp-server`.

The bundled API/docs configuration ships inside the package, and caches are written to your user cache directory (override with the `EQUINIX_MCP_CACHE_DIR` environment variable). Pass `--config path/to/apis.yaml` to use a custom configuration.

Equivalent alternatives: `pipx run equinix-docs-mcp-server`, or `pip install equinix-docs-mcp-server` (Python 3.13+) followed by `equinix-docs-mcp-server`.

### Developer installation

To work on this project, clone it and install editable:

```bash
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
```

The packaged configuration lives at `src/equinix_docs_mcp_server/data/config/apis.yaml` (with overlays alongside in `data/overlays/`), so with an editable install your edits there take effect directly.

## Adding the MCP Server to Claude Code

```bash
claude mcp add --env EQUINIX_API_CLIENTID=your_client_id \
  --env EQUINIX_API_CLIENTSECRET=your_client_secret \
  --transport stdio equinix \
  -- uvx --system-certs equinix-docs-mcp-server
```

> [!TIP]
> The `--env` flags are optional if you already have credentials configured via the [Equinix CLI](https://github.com/equinix/cli) config file (`~/.config/equinix/equinix.yaml`). The server reads `equinix_client_id` and `equinix_client_secret` from that file automatically.

Add `--scope project` to share the configuration with the team via a `.mcp.json` file (omit the `--env` flags in that case and export the credentials in your shell instead, so secrets stay out of the committed file). Note the flag ordering: another option (here `--transport stdio`) must sit between the last `--env` and the server name, or the CLI parses the name as another KEY=value pair.

For more details, see the [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

## Adding the MCP Server to VS Code

Add the server with a single command (the JSON is one server object, with the name inline):

```bash
code --add-mcp '{"name":"equinix","type":"stdio","command":"uvx","args":["--system-certs","equinix-docs-mcp-server"]}'
```

Or create a `.vscode/mcp.json` in your workspace to prompt for credentials on first use:

```json
{
  "servers": {
    "equinix": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--system-certs",
        "equinix-docs-mcp-server"
      ],
      "env": {
        "EQUINIX_API_CLIENTID": "${input:equinix-client-id}",
        "EQUINIX_API_CLIENTSECRET": "${input:equinix-client-secret}"
      }
    }
  },
  "inputs": [
    {
      "type": "promptString",
      "id": "equinix-client-id",
      "description": "Equinix Client ID",
      "password": true
    },
    {
      "type": "promptString",
      "id": "equinix-client-secret",
      "description": "Equinix Client Secret",
      "password": true
    }
  ]
}
```

For more details, see the [VS Code MCP Server documentation](https://code.visualstudio.com/docs/agent-customization/mcp-servers).

## Usage

### Configuration

Set your Equinix API credentials as environment variables, or configure them once via the [Equinix CLI](https://github.com/equinix/cli) (`~/.config/equinix/equinix.yaml`) — the server reads the same file automatically:

```bash
# OAuth2 Client Credentials
export EQUINIX_API_CLIENTID="your_client_id"
export EQUINIX_API_CLIENTSECRET="your_client_secret"
```

These are the names the [Equinix Terraform provider](https://github.com/equinix/terraform-provider-equinix) and the Equinix SDKs use. The earlier `EQUINIX_CLIENT_ID` and `EQUINIX_CLIENT_SECRET` still work but are deprecated, and the server logs a warning when it falls back to them.

If you have already run `equinix init`, credentials are stored in `~/.config/equinix/equinix.yaml` (keys: `equinix_client_id`, `equinix_client_secret`). No environment variables are needed in that case. Environment variables take priority over that file.

#### API Endpoint

Requests and token requests go to `https://api.equinix.com`. To use another environment, such as a test environment or a mock server, set `EQUINIX_API_ENDPOINT`, as with the Terraform provider:

```bash
export EQUINIX_API_ENDPOINT="https://mockapi.example.com"
```

This replaces the `api.equinix.com` host for every API family and for the token URL (`$EQUINIX_API_ENDPOINT/oauth2/v1/token`). Families served from their own hosts, such as `access.eqix.equinix.com`, keep them. The generated tool catalog and `--export-openrpc` output still document the production URLs.

#### API Spec Fetching

The server uses cached API specifications by default for faster startup. Use `--update-specs` to force fetching fresh specs from remote sources.

```bash
equinix-docs-mcp-server --update-specs # --config path/to/custom/config.yaml
```

#### Rate Limits, Deprecations and Sunsets

API requests that get HTTP 429 (or 408) are sent again after the wait the `Retry-After` header asks for, or a short backoff without one. A 502, 503 or 504 is retried only for `GET`, `HEAD`, `OPTIONS`, `PUT` and `DELETE`, since a `POST` or `PATCH` may already have been applied. All waits for one request share a small budget, so a tool call doesn't hold up the agent for long. When the budget runs out, the tool error says whether the request was applied and when to retry.

| Variable | Default | Meaning |
|---|---|---|
| `EQUINIX_MCP_RETRY_MAX` | `5` | Retries per request; `0` turns retrying off |
| `EQUINIX_MCP_RETRY_WAIT` | `15` | Seconds all waits for one request may add up to |

When an API response carries `Deprecation` or `Sunset` headers, the tool result gets a notice with the dates and any successor `Link`. The notice is given in full once per API family, and in one line on later calls. An error from an endpoint past its sunset says it appears to be retired. Operations marked `deprecated` in their OpenAPI spec say so in their tool description and have a `deprecated` tag.

#### Workflow Runs

Workflow tools (see [Defining Arazzo Workflows](#defining-arazzo-workflows-experimental)) run in a worker thread, so a long workflow doesn't hold up other tool calls. Their requests behave like the API tools': each gets the server's OAuth token, a 401 fetches a new token and resends once, 429/408 and gateway errors are retried under the same rules and `EQUINIX_MCP_RETRY_*` budget, deprecation notices are added to the result, and each request times out after 30 seconds.

A step that fails, by not meeting its `successCriteria` or by its request failing, makes the tool call an error that names the step. A `retry` failure action waits `retryAfter` seconds and reruns the step up to `retryLimit` times. A whole run has a time limit. Once it passes, no further requests are sent and the error names the step the run stopped at. Requests from earlier steps may already have been applied.

Only read-only workflows are registered by default: every step, including those in `forEach` loops, must call a `GET`, `HEAD` or `OPTIONS` operation, or a `POST` that the API tools also treat as a read (an operationId starting with a verb such as `search` or `get`, or a path ending in `/search` or `/validate`). A workflow with any other step, or with a step whose operation can't be found in its `sourceDescriptions`, is skipped with a warning in the log. Read-only workflow tools are annotated `readOnlyHint: true`.

| Variable | Default | Meaning |
|---|---|---|
| `EQUINIX_MCP_WORKFLOW_TIMEOUT` | `60` | Seconds one workflow run may take; `0` turns the limit off |
| `EQUINIX_MCP_ALLOW_WRITE_WORKFLOWS` | unset | Set to `1` to also register workflows that change resources |

## Server Configuration

The server is configured via the packaged `apis.yaml` (or a file passed with `--config`). This file defines:

- API endpoints and versions
- Authentication methods
- Overlay file locations
- Documentation settings

### Example API Configuration

```yaml
apis:
  fabric:
    auth_type: "client_credentials"
    service_name: "fabric"
    # include: []      # regexes of operationIds to expose (all when omitted)
    # exclude: []      # regexes of operationIds to hide
    # enabled: true
    specs:
      - url: "https://docs.equinix.com/api-catalog/fabricv4/openapi.yaml"
        overlay: "overlays/fabric.yaml"
```

## Overlay Files

Overlay files in the `overlays/` directory normalize API specifications before processing:

- Standardize authentication schemes
- Normalize base paths and servers
- Add consistent tagging
- Handle API-specific quirks

## Available MCP Tools

The server exposes MCP tools for:

1. **API Operations**: Dynamic tools generated per API family from its OpenAPI specification, named `<family>_<operationId>` (e.g. `fabric_searchConnections`, `network-edge_getMetros`). With the default `--tool-catalog search`, these are hidden from `tools/list` and reached through:
   - `search_tools` - Find API tools by natural-language query. Each match is a short entry: name, title, access (read-only, write or destructive), a one-paragraph summary, required parameters with types, and optional parameter names. Results run about 4-6K characters for 15 matches.
   - `get_tool_schema` - Return one tool's full definition, with every parameter's description and examples. Pass `output_schema: true` to include the response schema, for example to write a `call_api_filtered` query.
   - `call_tool` - Execute a discovered tool by name
   - `call_api_filtered` - Run an API tool and return only what a [JMESPath](https://jmespath.org/specification.html) query selects from its response, rendered as YAML. Use it when a full response would be too large, for example `data[].{code: code, name: name}` on `fabric_getMetros`.
2. **Documentation**: 
   - `search` - Full-text search documentation using indexed content (OpenAI MCP compatible)
   - `fetch` - Fetch full markdown content of a documentation page by URL (OpenAI MCP compatible)
   - `list_docs` - List documentation pages by product section, optionally filtered by keywords
   - `find_docs` - Find documentation pages by title, section and description, ranked with BM25
3. **Workflows (Arazzo)**:
   - Tools prefixed with `workflow__` run multi-step workflows defined in [Arazzo](https://spec.openapis.org/arazzo/latest.html) files, for example `workflow__list_ne_metros`.
   - `arazzo_list_workflows` and `arazzo_describe_workflow` list the loaded workflows and describe one.

### Tool Catalog Reference (OpenRPC)

The complete tool catalog, including every generated API tool, is published at <https://equinix-labs.github.io/equinix-docs-mcp-server/> as a browsable single-page reference, with the [OpenRPC](https://open-rpc.org/) document it is rendered from at [`openrpc.json`](https://equinix-labs.github.io/equinix-docs-mcp-server/openrpc.json). Each OpenRPC method is one MCP tool: its by-name params are the tool arguments, sent over MCP as `tools/call` with `{"name": "<method>", "arguments": {...}}`.

The *OpenRPC Docs* workflow generates and lints the catalog on every pull request and uploads it as the `openrpc-docs` artifact. Pushes to `main`, a weekly run (to pick up upstream API spec changes) and manual runs also deploy it to GitHub Pages. The generated files are not committed.

To build the site locally (specs are always refreshed, so the output does not depend on your local cache):

```bash
equinix-docs-mcp-server --export-openrpc site   # writes site/openrpc.json and site/index.html
npx @redocly/cli lint site/openrpc.json         # optional validation
```

Then open `site/index.html` in a browser. `site/` is git-ignored.

### Defining Arazzo Workflows (Experimental)

Workflows are [Arazzo 1.0](https://spec.openapis.org/arazzo/latest.html) documents, run with [arazzo-runner](https://pypi.org/project/arazzo-runner/). List them in the `arazzo` section of `apis.yaml`. Paths resolve relative to the config file's parent directory, and the bundled configuration loads `config/workflows.yaml`:

```yaml
arazzo:
  specs:
    - config/workflows.yaml
```

Each workflow becomes a `workflow__<workflowId>` tool. Its parameters come from the workflow's `inputs` schema, and an `inputs_json` parameter accepts the inputs as one JSON or YAML object. The bundled workflow:

```yaml
arazzo: 1.0.1
info:
  title: Equinix Workflows to get metros
  version: 2.0.0
sourceDescriptions:
  - name: ne
    url: https://docs.equinix.com/api-catalog/network-edgev1/openapi.yaml
    type: openapi
workflows:
  - workflowId: list_ne_metros
    summary: Retrieve Network Edge metros
    steps:
      - stepId: get_metros
        operationId: getMetros
        successCriteria:
          - condition: $statusCode == 200
        outputs:
          metros: $response.body#/data
    outputs:
      metros: $steps.get_metros.outputs.metros
```

Steps call operations from the `sourceDescriptions` specs, not the server's MCP tools. Values pass between steps with runtime expressions such as `$inputs.<name>` and `$steps.<stepId>.outputs.<name>`. When a workflow declares a `bearerToken` input and the caller doesn't supply one, the server fills it in with its own OAuth token.

The server also supports `forEach` on a step, which isn't part of Arazzo 1.0. It runs the step's nested `steps` once per item of a collection, with `$item.<field>` substituted. Only one level of nesting is supported. `data/config/workflows-dcim.yaml` is a larger example that uses it. It isn't loaded by default.

## Development

### Running Tests

```bash
pip install -e '.[dev]'
pytest
```

See [`docs/SPEC_VETTING.md`](docs/SPEC_VETTING.md) for exercising every generated tool against mock responses, [`docs/SEARCH_FETCH_USAGE.md`](docs/SEARCH_FETCH_USAGE.md) for the documentation `search` and `fetch` tools, and [`docs/INTEGRATION_EXAMPLE.md`](docs/INTEGRATION_EXAMPLE.md) for an example of using them together.

## License

MIT License. See [LICENSE](LICENSE) for details.

## Links

- [Equinix Documentation](https://docs.equinix.com/)
- [Equinix API Documentation](https://docs.equinix.com/equinix-api)
- [FastMCP Framework](https://gofastmcp.com/)
- [Model Context Protocol](https://github.com/modelcontextprotocol)
