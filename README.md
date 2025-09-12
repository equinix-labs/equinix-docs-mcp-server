# Equinix MCP Server

This project is an experimental Model Context Protocol (MCP) server, for local use and learning, that provides unified access to Equinix APIs and documentation through docs.equinix.com published API specifications and site indexes. This project is not expected to offer high quality (production-ready) results. This is offered for developers learning about MCP, Equinix APIs and documentation, and their potential integration. An example of this is that the full list of Equinix API operationIds will overwhelm the context windows of local LLMs making this tool impractical for more than learning.

## Features

- **API Access**: Fetches, merges, and caches multiple Equinix API specifications then exposes operationIds as MCP tools.
- **API Authentication**: Supports both OAuth2 Client Credentials used by most API services and Metal API tokens
- Configurable Overlays**: Use overlay specifications to normalize APIs before processing
- **Documentation Integration**: Search and browse Equinix documentation via sitemap and Lunr search
- **Automated Updates**: GitHub Actions workflow for keeping API specs current
- **FastMCP Integration**: Built on the FastMCP framework for rapid development
- **Arazzo Workflows (Experimental)**: Define and execute higher-level workflows chaining multiple API operations

## Supported APIs

Any Equinix API specification can be added to the configuration file but operations may need to be filtered and overlays may be needed for this tool to use the spec in the MCP server.
The `config/apis.yaml` file defines API specifications that have been used during development to test behavior.

## Quick Start

### Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Or replace `.venv/bin/python` with `uvx` (if preferred) and change the commands examples and configuration blocks accordingly.

## Adding the MCP Server to VS Code

After cloning this repository, you can add the Equinix MCP server to VS Code with a single command:

### Using Python Virtual Environment (.venv)

```bash
code --add-mcp '{
 "servers": {
  "equinix": {
   "type": "stdio",
   "command": ".venv/bin/python",
   "args": [
    "-m",
    "equinix_mcp_server.main"
   ],
   "cwd": "'$PWD'",
   "env": {
    "PYTHONPATH": "'$PWD'$'/src",
    "EQUINIX_CLIENT_ID": "$\{input:EQUINIX_CLIENT_ID\}",
    "EQUINIX_CLIENT_SECRET": "$\{input:EQUINIX_CLIENT_SECRET\}",
    "EQUINIX_METAL_TOKEN": "$\{input:EQUINIX_METAL_TOKEN\}"
   },
   "disabled": false
  }
 },
 "inputs": [
  {
   "type": "promptString",
   "id": "EQUINIX_CLIENT_ID",
   "description": "Equinix Client ID",
   "password": true
  },
  {
   "type": "promptString",
   "id": "EQUINIX_CLIENT_SECRET",
   "description": "Equinix Client Secret",
   "password": true
  },
  {
   "type": "promptString",
   "id": "EQUINIX_METAL_TOKEN",
   "description": "Equinix Metal API Token",
   "password": true
  }
 ]
}}'
```

> **Note:** The configuration is stored in `.vscode/mcp.json` per project. You do not need to set `cwd` unless you have a special setup.

For more details, see [VS Code MCP Server documentation](https://code.visualstudio.com/docs/copilot/chat/mcp-servers).

## Usage

### Configuration

Set your Equinix API credentials as environment variables:

```bash
# Required for most APIs (OAuth2 Client Credentials)
export EQUINIX_CLIENT_ID="your_client_id"
export EQUINIX_CLIENT_SECRET="your_client_secret"

# Optional for Metal API (if you prefer API token over OAuth2)
export EQUINIX_METAL_TOKEN="your_metal_token"
```

#### Start the MCP Server

```bash
python -m equinix_mcp_server.main```

#### Test with MCP Clients

**Claude Desktop, Continue.dev, etc.:**

1. **Copy the MCP configuration** to your client's config file:
   ```bash
   # For Claude Desktop
   cp claude_desktop_config.json ~/Library/Application\ Support/Claude/claude_desktop_config.json
   ```

2. **Start your MCP client** (Claude Desktop, Continue.dev, etc.)

3. **Test Network Edge device listing**:
   Ask your AI: *"Can you list the available Network Edge devices using the Equinix API?"*

**Ollama (via mcp-client-for-ollama bridge):**

1. **Install the bridge**:
   ```bash
   pip install --upgrade ollmcp
   ```

2. **Create MCP config** (see `TESTING_WITH_OLLAMA.md` for details)

3. **Connect Ollama to MCP server**:
   ```bash
   ollmcp --servers-json equinix-mcp-config.json --model qwen2.5:7b
   ```

4. **Expected behavior**:
   - ✅ MCP server connects and tools are available
   - ✅ API call is attempted to Network Edge API
   - ❌ Authentication failure (without credentials) - this proves it's working!

See `TESTING_WITH_OLLAMA.md` for detailed Ollama integration instructions.

#### Test API Spec Fetching

```bash
equinix-mcp-server --update-specs # --config path/to/custom/config.yaml
```

**Note:** The server uses cached API specifications by default for faster startup. Use `--update-specs` to force fetching fresh specs from remote sources.

## Server Configuration

The server is configured via `config/apis.yaml`. This file defines:

- API endpoints and versions
- Authentication methods
- Overlay file locations
- Documentation settings

### Example API Configuration

```yaml
apis:
  metal:
    url: "https://docs.equinix.com/api-catalog/metalv1/openapi.yaml"
    overlay: "overlays/metal.yaml"
    auth_type: "metal_token"
    service_name: "metal"
    version: "v1"
    # include: []
    # exclude: [".*"]
    # enabled: true

  fabric:
    url: "https://docs.equinix.com/api-catalog/fabricv4/openapi.yaml"
    overlay: "overlays/fabric.yaml"
    auth_type: "client_credentials"
    service_name: "fabric"
    version: "v4"
```

## Overlay Files

Overlay files in the `overlays/` directory normalize API specifications before processing:

- Standardize authentication schemes
- Normalize base paths and servers
- Add consistent tagging
- Handle API-specific quirks

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   API Catalog   │───▶│  Spec Manager    │───▶│  Cached OpenAPI │
│   (Remote URLs) │    │  + Overlays      │    │  Specifications │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │
                                ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Documentation │    │   Auth Manager   │    │   FastMCP       │
│   (Sitemap)     │───▶│  (OAuth2/Token)  │───▶│   Server        │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## Available MCP Tools

The server exposes MCP tools for:

1. **API Operations**: Dynamic tools generated from individual API specifications
2. **Documentation**: 
   - `list_docs` - List and filter documentation
   - `find_docs` - Find documentation by filename/title matching
   - `search_docs` - Full-text search documentation using indexed content
3. **Workflows (Arazzo)**:
    - Tools prefixed with `workflow__` represent multi-step orchestrations defined in Arazzo-like YAML files.
    - Example: `workflow__list_metal_metros_then_prices`

### Defining Workflows (Experimental)

Add an `arazzo` section to your `config/apis.yaml`:

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

Planned enhancements:
- Conditional / branching logic
- Parallel execution
- Schema introspection for richer tool signatures
- Error handling policies per step

## Development

### Running Tests

```bash
pip install -e .[dev]
pytest
```

### Code Formatting

```bash
black src tests
isort src tests
flake8 src tests
```

### Type Checking

```bash
mypy src
```

## GitHub Actions

The repository includes automated workflows:

- **CI**: Runs tests, linting, and validation on pull requests
- **Update Specs**: Daily job to fetch latest API specs and create PRs

## Authentication Details

### Client Credentials (OAuth2)

Most Equinix APIs use OAuth2 Client Credentials flow:

1. Get client ID and secret from Equinix portal
2. Exchange for access token at `/oauth2/v1/token`
3. Use Bearer token in Authorization header

### Metal API Token

Metal API can use either OAuth2 or API token:

1. Get API token from Metal console
2. Use token in `X-Auth-Token` header

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Run the test suite
6. Submit a pull request

## License

MIT License. See [LICENSE](LICENSE) for details.

## Links

- [Equinix API Documentation](https://docs.equinix.com/api-catalog/)
- [FastMCP Framework](https://gofastmcp.com/)
- [Model Context Protocol](https://github.com/modelcontextprotocol)
