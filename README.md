# Equinix Docs and API Specifications MCP Server

This project is an experimental Model Context Protocol (MCP) server, for local use and learning, that provides access to Equinix APIs and documentation. This project is not expected to offer high quality (production-ready) results. This is offered for developers learning about MCP, Equinix APIs and documentation, and their potential integration. 

> [!NOTE]
> By default the API tool catalog is collapsed behind a `search_tools`/`call_tool` discovery pair (FastMCP's BM25 search transform), so large API catalogs no longer overwhelm the model's context window. Use `--tool-catalog full` to list every generated tool directly.

## Features

- **API Access**: Fetches and caches Equinix API specifications then exposes operationIds as MCP tools.
   - **Per-family providers**: Each API family (Metal, Fabric, Network Edge, Billing, Smart View) is served by its own OpenAPI provider; tools are namespaced `<family>_<operationId>` (e.g. `metal_findPlans`)
   - **Searchable catalog**: API tools are hidden from `tools/list` and discovered on demand via `search_tools`, following the MCP 2026-07-28 progressive-discovery guidance; `--tool-catalog code-mode` enables FastMCP's experimental sandboxed code execution instead
   - **Cache hints**: `tools/list` and related results carry `ttlMs`/`cacheScope` hints per the MCP 2026-07-28 caching utility
   - **API Authentication**: Supports both OAuth2 Client Credentials used by most API services and Metal API tokens
   - **Configurable Overlays**: Use overlay specifications to normalize API responses before LLM processing
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

No clone or checkout is required. With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, run the server directly from GitHub (uv fetches a suitable Python automatically):

```bash
uvx --system-certs --from git+https://github.com/equinix-labs/equinix-docs-mcp-server equinix-docs-mcp-server
```

The bundled API/docs configuration ships inside the package, and caches are written to your user cache directory (override with the `EQUINIX_MCP_CACHE_DIR` environment variable). Pass `--config path/to/apis.yaml` to use a custom configuration.

Equivalent alternatives: `pipx run --spec git+https://github.com/equinix-labs/equinix-docs-mcp-server equinix-docs-mcp-server`, or `pip install git+https://github.com/equinix-labs/equinix-docs-mcp-server` (Python 3.13+) followed by `equinix-docs-mcp-server`.

### Developer installation

To work on this project, clone it and install editable:

```bash
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .[dev]
```

The packaged configuration lives at `src/equinix_docs_mcp_server/data/config/apis.yaml` (with overlays alongside in `data/overlays/`), so with an editable install your edits there take effect directly.

## Adding the MCP Server to Claude Code

```bash
claude mcp add --env EQUINIX_CLIENT_ID=your_client_id \
  --env EQUINIX_CLIENT_SECRET=your_client_secret \
  --env EQUINIX_METAL_TOKEN=your_metal_token \
  --transport stdio equinix \
  -- uvx --system-certs --from git+https://github.com/equinix-labs/equinix-docs-mcp-server equinix-docs-mcp-server
```

> [!TIP]
> The `--env` flags are optional if you already have credentials configured via the [Equinix CLI](https://github.com/equinix/cli) config file (`~/.config/equinix/equinix.yaml`). The server reads `equinix_client_id`, `equinix_client_secret`, and `metal_auth_token` from that file automatically, and falls back to `token` in `~/.config/equinix/metal.yaml` (the [Metal CLI](https://github.com/equinix/metal-cli) format) for the Metal token.

Add `--scope project` to share the configuration with the team via a `.mcp.json` file (omit the `--env` flags in that case and export the credentials in your shell instead, so secrets stay out of the committed file). Note the flag ordering: another option (here `--transport stdio`) must sit between the last `--env` and the server name, or the CLI parses the name as another KEY=value pair.

For more details, see the [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

## Adding the MCP Server to VS Code

Add the server with a single command (the JSON is one server object, with the name inline):

```bash
code --add-mcp '{"name":"equinix","type":"stdio","command":"uvx","args":["--system-certs","--from","git+https://github.com/equinix-labs/equinix-docs-mcp-server","equinix-docs-mcp-server"]}'
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
        "--from",
        "git+https://github.com/equinix-labs/equinix-docs-mcp-server",
        "equinix-docs-mcp-server"
      ],
      "env": {
        "EQUINIX_CLIENT_ID": "${input:equinix-client-id}",
        "EQUINIX_CLIENT_SECRET": "${input:equinix-client-secret}",
        "EQUINIX_METAL_TOKEN": "${input:equinix-metal-token}"
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
    },
    {
      "type": "promptString",
      "id": "equinix-metal-token",
      "description": "Equinix Metal API Token",
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
# OAuth2 Client Credentials (required for most APIs)
export EQUINIX_CLIENT_ID="your_client_id"
export EQUINIX_CLIENT_SECRET="your_client_secret"

# Metal API token (optional — falls back to Metal CLI config if omitted)
export EQUINIX_METAL_TOKEN="your_metal_token"
```

If you have already run `equinix init` or `metal init`, credentials are stored in `~/.config/equinix/equinix.yaml` (keys: `equinix_client_id`, `equinix_client_secret`, `metal_auth_token`) or `~/.config/equinix/metal.yaml` (key: `token`). No environment variables are needed in that case.

#### API Spec Fetching

The server uses cached API specifications by default for faster startup. Use `--update-specs` to force fetching fresh specs from remote sources.

```bash
equinix-docs-mcp-server --update-specs # --config path/to/custom/config.yaml
```

## Server Configuration

The server is configured via the packaged `apis.yaml` (or a file passed with `--config`). This file defines:

- API endpoints and versions
- Authentication methods
- Overlay file locations
- Documentation settings

### Example API Configuration

```yaml
apis:
  metal:
    auth_type: "metal_token"
    service_name: "metal"
    # include: []      # regexes of operationIds to expose (all when omitted)
    # exclude: []      # regexes of operationIds to hide
    # enabled: true
    specs:
      - url: "https://docs.equinix.com/api-catalog/metalv1/openapi.yaml"
        overlay: "overlays/metal.yaml"

  fabric:
    auth_type: "client_credentials"
    service_name: "fabric"
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

1. **API Operations**: Dynamic tools generated per API family from its OpenAPI specification, named `<family>_<operationId>` (e.g. `metal_findPlans`, `network-edge_getMetros`). With the default `--tool-catalog search`, these are hidden from `tools/list` and reached through:
   - `search_tools` - Find API tools by natural-language query (returns full schemas)
   - `call_tool` - Execute a discovered tool by name
2. **Documentation**: 
   - `search` - Full-text search documentation using indexed content (OpenAI MCP compatible)
   - `fetch` - Fetch full markdown content of a documentation page by URL (OpenAI MCP compatible)
   - `list_docs` - List and filter documentation
   - `find_docs` - Find documentation by filename/title matching
3. **Workflows (Arazzo)**:
    - Tools prefixed with `workflow__` represent multi-step orchestrations defined in Arazzo-like YAML files.
    - Example: `workflow__list_metal_metros_then_prices`

### Defining Arazzo Workflows (Experimental)

Add an `arazzo` section to your `apis.yaml` (spec paths resolve relative to the config file's parent directory):

```yaml
arazzo:
   specs:
      - examples/workflows.yaml
```

Example workflow spec (`examples/workflows.yaml`):

```yaml
workflows:
   list_metal_metros_then_prices:
      description: Retrieve Metal metros then spot market prices.
      inputs:
         metro: "SV"
      steps:
         - id: get_metros
            operation: metal_findMetros
            saveAs: metros
         - id: get_prices
            operation: metal_findMetroSpotMarketPrices
            params:
               metro: "{{ metro }}"
            saveAs: prices
```

Run the server and invoke: `workflow__list_metal_metros_then_prices`.

Currently supported features:
- Sequential steps referencing existing API tools
- Simple variable capture via `saveAs`
- Jinja2 templated parameter rendering (falls back to Python `str.format`)
  
## Development

### Running Tests

```bash
pip install -e .[dev]
pytest
```

## License

MIT License. See [LICENSE](LICENSE) for details.

## Links

- [Equinix Documentation](https://docs.equinix.com/)
- [Equinix API Documentation](https://docs.equinix.com/equinix-api)
- [FastMCP Framework](https://gofastmcp.com/)
- [Model Context Protocol](https://github.com/modelcontextprotocol)
