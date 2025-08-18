# Testing Equinix MCP Server with Ollama

## Overview

This guide shows you how to connect the Equinix MCP Server to Ollama using the `mcp-client-for-ollama` bridge for testing Network Edge device listing functionality.

## Prerequisites

1. **Ollama installed** and running locally ([Installation guide](https://ollama.com/download))
2. **Equinix MCP Server** installed and working (this repository)
3. **mcp-client-for-ollama** - Bridge that connects Ollama to MCP servers
4. **Compatible Ollama model** with tool support (qwen2.5, llama3.1, llama3.2, etc.)
5. **Python 3.13+** for running the bridge
6. **Equinix API credentials** (optional for initial testing - auth failures are expected)

## Step 1: Install and Setup mcp-client-for-ollama

The `mcp-client-for-ollama` project provides a bridge that allows Ollama to use MCP servers:

```bash
# Option 1: Install with pip
pip install --upgrade ollmcp

# Option 2: One-step install and run
uvx ollmcp

# Option 3: Install from source
git clone https://github.com/jonigl/mcp-client-for-ollama.git
cd mcp-client-for-ollama
uv venv && source .venv/bin/activate
uv pip install .
```

## Step 2: Install Compatible Ollama Model

Make sure you have a model with tool support:

```bash
# Install a compatible model (choose one)
ollama pull qwen2.5:7b
ollama pull qwen3:8b
ollama pull llama3.1:8b
```

## Step 3: Create MCP Server Configuration

Create a configuration file for the bridge. The `mcp-client-for-ollama` can use our server in several ways:

### Option A: Direct Python Script (Recommended)

Create a config file `equinix-mcp-config.json`:

```json
{
  "mcpServers": {
    "equinix": {
      "command": "python",
      "args": ["-m", "equinix_mcp_server.main"],
      "cwd": "/path/to/equinix-mcp-server",
      "env": {
        "PYTHONPATH": "/path/to/equinix-mcp-server/src",
        "EQUINIX_CLIENT_ID": "${EQUINIX_CLIENT_ID}",
        "EQUINIX_CLIENT_SECRET": "${EQUINIX_CLIENT_SECRET}"
      },
      "disabled": false
    }
  }
}
```

**Important**: Replace `/path/to/equinix-mcp-server` with your actual path, and make sure your environment variables are exported before starting the bridge.

### Option B: Use Installed Package (if using pip install -e .)

```json
{
  "mcpServers": {
    "equinix": {
      "command": "equinix-mcp-server",
      "disabled": false
    }
  }
}
```

## Step 4: Test MCP Server Standalone

Before connecting to Ollama, verify the MCP server works:

```bash
cd /path/to/equinix-mcp-server
python -m equinix_mcp_server.main --update-specs
```

You should see all APIs load successfully.

## Step 5: Connect Ollama to Equinix MCP Server

Now use the bridge to connect Ollama to our MCP server:

```bash
# Using the JSON config file
ollmcp --servers-json equinix-mcp-config.json --model qwen2.5:7b

# Or directly specify the server
ollmcp --mcp-server /path/to/equinix-mcp-server/src/equinix_mcp_server/main.py --model qwen2.5:7b
```

## Step 6: Test Network Edge Device Listing

Once the bridge starts, you'll see an interactive terminal. Try asking about Network Edge devices:

1. **Basic query**:

   ```
   Can you list the available Network Edge devices using the Equinix API?
   ```

2. **More specific query**:

   ```
   Show me all virtual network functions in the Network Edge API
   ```

3. **Explore available tools**:

   ```
   What Network Edge API operations are available?
   ```

4. **Search documentation**:

   ```
   What docs are available for Fabric providers?
   ```

   Note: The docs search supports flexible matching - "Fabric providers" will find "Fabric Provider Guide", "Provider Management", etc.

## Expected Behavior

### Without API Credentials

You should see:

1. ✅ **MCP Server connects** successfully
2. ✅ **Bridge shows available tools** (441 API operations including Fabric, Metal, Network Edge, Billing)
3. ✅ **Ollama identifies relevant tools** (e.g., `fabric_searchConnections`, `networkEdge_getDevices`)
4. ✅ **API call is attempted** to the Equinix API endpoints
5. ❌ **Authentication failure** (401 Unauthorized - Invalid access token) - this proves it's working!

**Expected Error Message:**

```text
HTTP error 401: Unauthorized - {'errorDomain': 'api-platform', 'errorTitle': 'Invalid access token', 'errorCode': '401', 'developerMessage': 'Please pass a valid access token', 'errorMessage': 'Invalid access token'}
```

This error confirms that:

- The MCP server successfully loaded and exposed all Equinix API tools
- The HTTP requests are reaching Equinix's servers  
- OAuth2 authentication is working (just needs valid credentials)

### With API Credentials

Set your credentials before starting:

```bash
export EQUINIX_CLIENT_ID="your_client_id"
export EQUINIX_CLIENT_SECRET="your_client_secret"

# Then start the bridge
ollmcp --servers-json equinix-mcp-config.json --model qwen2.5:7b
```

Then you should see:

1. ✅ **Successful authentication**
2. ✅ **Actual API response** with Network Edge devices (if any)

## Interactive Commands in mcp-client-for-ollama

The bridge provides useful interactive commands:

- `tools` or `t` - Show/manage available MCP tools
- `model` or `m` - Switch between Ollama models  
- `help` or `h` - Show all available commands
- `human-in-loop` or `hil` - Toggle tool execution confirmations
- `show-tool-execution` or `ste` - Toggle tool execution visibility
- `quit` or `q` - Exit the bridge

## Troubleshooting

### MCP Server Won't Start

```bash
# Check if specs can be downloaded
python -m equinix_mcp_server.main --test-update-specs

# Check for Python path issues
PYTHONPATH=/path/to/equinix-mcp-server/src python -m equinix_mcp_server.main
```

### "Request URL is missing protocol" Error

If you see an error like:

```text
UnsupportedProtocol: Request URL is missing an 'http://' or 'https://' protocol.
```

This indicates a URL construction issue in the HTTP client. The fix is to ensure the base URL is properly set in the AuthenticatedClient. The server has been updated to handle this properly by setting `base_url="https://api.equinix.com"` in the HTTP client.

### Bridge Connection Issues

- Verify the `cwd` path in your JSON config points to the correct directory
- Check that Python can find the equinix_mcp_server module
- Ensure Ollama is running: `ollama serve` (usually runs automatically)
- Check Ollama models: `ollama list`

### Ollama Model Issues

- Install a compatible model: `ollama pull qwen2.5:7b`
- Check model supports tools: [Ollama models with tools](https://ollama.com/search?c=tools)
- Try different models if one doesn't work well

### "401 Unauthorized" with Valid Credentials

If you're getting 401 errors despite having valid `EQUINIX_CLIENT_ID` and `EQUINIX_CLIENT_SECRET` environment variables:

1. **Test authentication directly**:

   ```bash
   cd /path/to/equinix-mcp-server
   python3 test_live_auth.py
   ```

   If this works but the bridge fails, it's an environment variable issue.

2. **Ensure environment variables are in the bridge config**:
   Update your `equinix-mcp-config.json` to explicitly pass the environment variables:

   ```json
   {
     "mcpServers": {
       "equinix": {
         "command": "python",
         "args": ["-m", "equinix_mcp_server.main"],
         "cwd": "/path/to/equinix-mcp-server",
         "env": {
           "PYTHONPATH": "/path/to/equinix-mcp-server/src",
           "EQUINIX_CLIENT_ID": "${EQUINIX_CLIENT_ID}",
           "EQUINIX_CLIENT_SECRET": "${EQUINIX_CLIENT_SECRET}"
         },
         "disabled": false
       }
     }
   }
   ```

3. **Alternative: Use actual values in config** (less secure):

   ```json
   "env": {
     "PYTHONPATH": "/path/to/equinix-mcp-server/src",
     "EQUINIX_CLIENT_ID": "your_actual_client_id",
     "EQUINIX_CLIENT_SECRET": "your_actual_client_secret"
   }
   ```

### "No tools available"

If you see "no tools available" when using the bridge:

1. **Check server startup**: Look for FastMCP initialization messages:

   ```
   Created FastMCP OpenAPI server with 441 routes
   ```

2. **Verify MCP connection**: The bridge should show:

   ```
   Connected to MCP server successfully
   ```

3. **Test tool availability**: You can test the server directly:

   ```bash
   python -c "
   import asyncio
   import sys
   sys.path.insert(0, 'src')
   from equinix_mcp_server.main import EquinixMCPServer
   
   async def test():
       server = EquinixMCPServer('config/apis.yaml')
       await server.initialize()
       tools = await server.mcp.get_tools()
       print(f'Tools available: {len(tools)}')
       # Test specific Network Edge device listing
       if 'network_edge_getVirtualDevicesUsingGET_1' in tools:
           tool = tools['network_edge_getVirtualDevicesUsingGET_1']
           print(f'✅ Network Edge device listing tool found: {tool._route.method} {tool._route.path}')
   
   asyncio.run(test())
   "
   ```

4. **Verify tool exposure**: The server should expose 441 tools including Network Edge device listing:
   - `network_edge_getVirtualDevicesUsingGET_1` - List all Network Edge devices
   - 63 total Network Edge tools
   - 441 total tools across all Equinix APIs

## Next Steps

1. **Test basic connectivity** (bridge connects to MCP server and Ollama)
2. **Attempt Network Edge device listing** (expect auth failure without credentials)
3. **Add real API credentials** and test successful calls
4. **Explore other Equinix APIs** (Metal, Fabric, Billing)
5. **Try different Ollama models** to see which work best with tools

## Key Network Edge Operations Available

When connected, the bridge will expose these key Network Edge operations:

- `GET /network-edge/v1/ne/v1/devices` - List Virtual Devices
- `GET /network-edge/v1/ne/v1/deviceTypes` - Get Device Types
- `GET /network-edge/v1/ne/v1/devices/{uuid}` - Get specific device details
- `POST /network-edge/v1/ne/v1/devices` - Create new virtual device

## Manual Testing Commands

You can also test components separately:

```bash
# Test MCP server alone
python -m equinix_mcp_server.main --test-update-specs

# Test bridge connectivity
python test_mcp.py

# Start bridge with verbose output
ollmcp --servers-json equinix-mcp-config.json --model qwen2.5:7b --help
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
PYTHONPATH=/path/to/equinix-mcp-server/src python -m equinix_mcp_server.main
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
