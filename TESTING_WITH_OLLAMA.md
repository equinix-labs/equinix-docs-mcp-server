# Testing Equinix MCP Server with Ollama

## Overview

This guide shows you how to connect the Equinix MCP Server to Ollama for testing Network Edge device listing functionality.

## Prerequisites

1. **Ollama installed** with MCP support
2. **Equinix MCP Server** installed and working (this repository)
3. **Equinix API credentials** (optional for initial testing - auth failures are expected)

## Step 1: Configure Ollama for MCP

Ollama needs to know about our MCP server. There are several ways to do this:

### Option A: Using Claude Desktop (Recommended for Testing)

Claude Desktop has excellent MCP support. Create or edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "equinix": {
      "command": "python",
      "args": ["-m", "equinix_mcp_server.main"],
      "cwd": "/Users/mjohansson/dev/equinix-mcp-server",
      "env": {
        "PYTHONPATH": "/Users/mjohansson/dev/equinix-mcp-server/src"
      }
    }
  }
}
```

### Option B: Using Continue.dev VSCode Extension

Continue.dev also supports MCP. Add to your Continue config (update the `cwd` path as needed):

```json
{
  "mcpServers": [
    {
      "name": "equinix",
      "command": "python",
      "args": ["-m", "equinix_mcp_server.main"],
      "cwd": "/path/to/dev/equinix-mcp-server"
    }
  ]
}
```

### Option C: Direct Ollama Integration (If Supported)

If your Ollama version supports MCP directly, you can configure it in Ollama's config.

## Step 2: Start the MCP Server

Start the server manually first to test:

```bash
cd /Users/mjohansson/dev/equinix-mcp-server
python -m equinix_mcp_server.main
```

You should see the server initialize and load all API specs.

## Step 3: Test with Claude Desktop

1. **Start Claude Desktop** with the MCP configuration
2. **Verify MCP Connection**: You should see an indicator that the Equinix MCP server is connected
3. **Test Network Edge Query**: Ask Claude:

```
"Can you list the available Network Edge devices using the Equinix API?"
```

## Step 4: Expected Behavior

### Without API Credentials

You should see:
1. ✅ **MCP Server connects** successfully
2. ✅ **API tools are available** (Network Edge operations)
3. ✅ **API call is attempted** to list Network Edge devices
4. ❌ **Authentication failure** (401/403 error) - this is expected!

### With API Credentials

Set your credentials first:
```bash
export EQUINIX_CLIENT_ID="your_client_id"
export EQUINIX_CLIENT_SECRET="your_client_secret"
```

Then you should see:
1. ✅ **Successful authentication**
2. ✅ **Actual API response** with Network Edge devices (if any)

## Step 5: What to Look For

### MCP Tools Available

The server should expose tools like:
- `get_networkedge_v1_ves` - List virtual network functions
- `get_networkedge_v1_ves_ve_id` - Get specific VNF details
- `post_networkedge_v1_ves` - Create new VNF
- And many more from the Network Edge API

### Network Edge Device Listing

The specific operation for listing Network Edge devices would be something like:

- **Tool**: `get_networkedge_v1_ves`
- **Description**: "List Virtual Network Functions (VNFs)"
- **Expected Response**: JSON array of network edge devices

## Troubleshooting

### Server Won't Start

```bash
# Check if specs can be downloaded
python -m equinix_mcp_server.main --test-update-specs

# Check for Python path issues
PYTHONPATH=/Users/mjohansson/dev/equinix-mcp-server/src python -m equinix_mcp_server.main
```

### MCP Connection Issues

- Verify the `cwd` path in your MCP config
- Check that Python can find the module
- Look at Claude Desktop/Continue.dev logs

### API Authentication

- Set environment variables before starting the MCP server
- Check Equinix console for correct client credentials
- Verify OAuth2 scope requirements

## Next Steps

1. **Test basic MCP connectivity** (server starts, tools are available)
2. **Attempt Network Edge device listing** (expect auth failure without credentials)
3. **Add real API credentials** and test successful calls
4. **Explore other Equinix APIs** (Metal, Fabric, Billing)

## Manual Testing Commands

You can also test the server directly:

```bash
# Test API spec fetching and validation
python -m equinix_mcp_server.main --test-update-specs

# Start server (will run until killed)
python -m equinix_mcp_server.main

# In another terminal, test MCP connectivity
python test_mcp.py
```
