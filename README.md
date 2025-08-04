# Equinix MCP Server

A Model Context Protocol (MCP) server that provides unified access to Equinix APIs through a single, merged OpenAPI specification. This server enables AI agents to interact with Equinix's Metal, Fabric, Network Edge, and Billing APIs seamlessly.

## Features

- **Unified API Access**: Merges multiple Equinix APIs into a single MCP server
- **Multiple Authentication**: Supports both OAuth2 Client Credentials and Metal API tokens
- **Configurable Overlays**: Use overlay specifications to normalize APIs before merging
- **Documentation Integration**: Search and browse Equinix documentation via sitemap
- **Automated Updates**: GitHub Actions workflow for keeping API specs current
- **FastMCP Integration**: Built on the FastMCP framework for rapid development

## Supported APIs

- **Metal API** (v1) - Hardware-as-a-Service platform
- **Fabric API** (v4) - Network-as-a-Service interconnections
- **Network Edge API** (v1) - Virtual network functions
- **Billing API** (v2) - Account and billing management

## Quick Start

### Installation

```bash
pip install -e .
```

### Configuration

Set your Equinix API credentials as environment variables:

```bash
# Required for most APIs (OAuth2 Client Credentials)
export EQUINIX_CLIENT_ID="your_client_id"
export EQUINIX_CLIENT_SECRET="your_client_secret"

# Optional for Metal API (if you prefer API token over OAuth2)
export EQUINIX_METAL_TOKEN="your_metal_token"
```

### Usage

#### Start the MCP Server

```bash
equinix-mcp-server
```

#### Test with MCP Clients (Claude Desktop, Continue.dev, etc.)

1. **Copy the MCP configuration** to your client's config file:
   ```bash
   # For Claude Desktop
   cp claude_desktop_config.json ~/Library/Application\ Support/Claude/claude_desktop_config.json
   ```

2. **Start your MCP client** (Claude Desktop, Continue.dev, etc.)

3. **Test Network Edge device listing**:
   Ask your AI: *"Can you list the available Network Edge devices using the Equinix API?"*

4. **Expected behavior**:
   - ✅ MCP server connects and tools are available
   - ✅ API call is attempted to Network Edge API
   - ❌ Authentication failure (without credentials) - this proves it's working!

See `TESTING_WITH_OLLAMA.md` for detailed testing instructions.

#### Test API Spec Fetching

```bash
equinix-mcp-server --test-update-specs
```

#### Use Custom Configuration

```bash
equinix-mcp-server --config path/to/custom/config.yaml
```

## Configuration

The server is configured via `config/apis.yaml`. This file defines:

- API endpoints and versions
- Authentication methods
- Overlay file locations
- Output settings

### Example Configuration

```yaml
apis:
  metal:
    url: "https://docs.equinix.com/api-catalog/metalv1/openapi.yaml"
    overlay: "overlays/metal.yaml"
    auth_type: "metal_token"
    service_name: "metal"
    version: "v1"
    
  fabric:
    url: "https://docs.equinix.com/api-catalog/fabricv4/openapi.yaml"
    overlay: "overlays/fabric.yaml"
    auth_type: "client_credentials"
    service_name: "fabric"
    version: "v4"
```

## Overlay Files

Overlay files in the `overlays/` directory normalize API specifications before merging:

- Standardize authentication schemes
- Normalize base paths and servers
- Add consistent tagging
- Handle API-specific quirks

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   API Catalog   │───▶│  Spec Manager    │───▶│  Merged OpenAPI │
│   (Remote URLs) │    │  + Overlays      │    │  Specification  │
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

1. **API Operations**: Dynamic tools generated from merged OpenAPI spec
2. **Documentation**: 
   - `list_docs` - List and filter documentation
   - `search_docs` - Search documentation by query

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
