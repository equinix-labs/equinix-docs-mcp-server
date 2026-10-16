# Search and Fetch Integration Example

This document provides a practical example of how the `search` and `fetch` tools work together to enable deep research capabilities.

## Workflow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     User Query                              │
│   "How do I authenticate with the Fabric API?"              │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   Step 1: SEARCH                            │
│  Tool: search(query="fabric api authentication", limit=5)   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  Search Results                             │
│  1. API User Authentication                                 │
│     https://docs.equinix.com/equinix-api/api-authentication │
│  2. The Equinix API                                         │
│     https://docs.equinix.com/equinix-api                    │
│  3. Developer Settings                                      │
│     https://docs.equinix.com/equinix-api/developer-settings │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   Step 2: FETCH                             │
│  Tool: fetch(url="https://docs.equinix.com/equinix-api/     │
│                    api-authentication")                     │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│               Full Markdown Content                         │
│  # API User Authentication                                  │
│                                                             │
│  Equinix APIs use OAuth 2.0 to authenticate requests...     │
│                                                             │
│  ## Getting a Bearer Token                                  │
│  1. Generate Client ID and Client Secret                    │
│  2. Request an Access Token                                 │
│  ...                                                        │
│  [Complete documentation content]                           │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│            AI Processing & Response                         │
│  Synthesizes information and provides answer with citations │
└─────────────────────────────────────────────────────────────┘
```

## Code Example

### Python Implementation

```python
import asyncio
from equinix_docs_mcp_server.config import Config
from equinix_docs_mcp_server.docs import DocsManager

async def research_fabric_authentication():
    """Research Fabric API authentication using search and fetch."""
    
    # Initialize
    config = Config.load("config/apis.yaml")
    docs_manager = DocsManager(config)
    
    # Step 1: Search for relevant documentation
    print("🔍 Searching for Fabric API authentication...")
    search_results = await docs_manager.search_docs(
        query="fabric api authentication",
        limit=5
    )
    print(search_results)
    print("\n" + "="*60 + "\n")
    
    # Step 2: Fetch the most relevant document
    print("📄 Fetching detailed documentation...")
    content = await docs_manager.fetch_doc(
        url="https://docs.equinix.com/equinix-api/api-authentication"
    )
    
    # Display first 1000 characters
    print(content[:1000])
    print("\n... [content continues] ...\n")
    
    # Step 3: Fetch related documentation
    print("📄 Fetching related documentation...")
    dev_settings_content = await docs_manager.fetch_doc(
        url="https://docs.equinix.com/equinix-api/developer-settings"
    )
    
    print(dev_settings_content[:500])
    print("\n... [content continues] ...\n")
    
    return {
        "search_results": search_results,
        "authentication_guide": content,
        "developer_settings_guide": dev_settings_content
    }

# Run the example
if __name__ == "__main__":
    results = asyncio.run(research_fabric_authentication())
    print(f"\n✅ Retrieved {len(results)} documentation resources")
```

### MCP Tool Invocation (JSON-RPC)

When using with MCP protocol, the tools are invoked via JSON-RPC:

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "search",
    "arguments": {
      "query": "fabric api authentication",
      "limit": 5
    }
  },
  "id": 1
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "# Search Results for 'fabric api authentication'\n\n**API User Authentication**\n  https://docs.equinix.com/equinix-api/api-authentication\n\n..."
      }
    ]
  },
  "id": 1
}
```

Then fetch:
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "fetch",
    "arguments": {
      "url": "https://docs.equinix.com/equinix-api/api-authentication"
    }
  },
  "id": 2
}
```

## ChatGPT Integration Flow

When integrated with ChatGPT:

1. **User asks**: "How do I authenticate with the Fabric API?"

2. **ChatGPT calls search**:
   ```
   search(query="fabric api authentication")
   ```

3. **System returns**: List of relevant documentation URLs

4. **ChatGPT analyzes** search results and identifies key pages

5. **ChatGPT calls fetch** for the most relevant pages:
   ```
   fetch(url="https://docs.equinix.com/equinix-api/api-authentication")
   fetch(url="https://docs.equinix.com/equinix-api/developer-settings")
   ```

6. **System returns**: Full markdown content for each page

7. **ChatGPT synthesizes**: Information from multiple sources

8. **ChatGPT responds**: 
   > "The Fabric API uses OAuth 2.0 client credentials, in two steps:
   > 
   > 1. **Generate a Client ID and Client Secret**: Register an app under Developer Settings in the Customer Portal...
   > 2. **Request an access token**: POST your Client ID and Client Secret to the OAuth2 token endpoint, then send the token as a `Bearer` header...
   > 
   > Source: [API User Authentication](https://docs.equinix.com/equinix-api/api-authentication)"

## URL Normalization Examples

The fetch tool handles various URL formats:

| Input URL | Normalized URL (fetched) |
|-----------|-------------------------|
| `https://docs.equinix.com/fabric/fabric-faqs` | `https://docs.equinix.com/fabric/fabric-faqs.md` |
| `https://docs.equinix.com/fabric/fabric-faqs/` | `https://docs.equinix.com/fabric/fabric-faqs.md` |
| `https://docs.equinix.com/fabric/fabric-faqs.md` | `https://docs.equinix.com/fabric/fabric-faqs.md` |
| `fabric/fabric-faqs` | `https://docs.equinix.com/fabric/fabric-faqs.md` |
| `/fabric/fabric-faqs` | `https://docs.equinix.com/fabric/fabric-faqs.md` |

## Error Handling Example

```python
# Fetch with error handling
try:
    content = await docs_manager.fetch_doc(
        url="https://docs.equinix.com/nonexistent-page"
    )
    
    if "Error fetching document" in content:
        print("❌ Document not available")
        print(content)
    else:
        print("✅ Document retrieved successfully")
        print(content[:500])
        
except Exception as e:
    print(f"❌ Unexpected error: {e}")
```

Output for missing page:
```
❌ Document not available
Error fetching document: HTTP 404 - https://docs.equinix.com/nonexistent-page.md

The document may not be available in markdown format.
```

## Performance Considerations

### Caching Strategy

1. **Search Index**: Cached locally for fast searches
   - Located at: `cache/search/search-index.json`
   - Updated on first use or when cache is missing

2. **Fetched Documents**: Not cached (always fresh)
   - Ensures up-to-date content
   - Each fetch makes a new HTTP request

### Best Practices

1. **Limit Search Results**: Use the `limit` parameter
   ```python
   search(query="fabric", limit=3)  # Only top 3 results
   ```

2. **Selective Fetching**: Don't fetch all search results
   ```python
   # ✅ Good: Fetch only the most relevant
   results = await search(query="fabric api", limit=5)
   content = await fetch(url=most_relevant_url)
   
   # ❌ Bad: Fetch everything
   for url in all_urls:
       await fetch(url)  # Too many requests
   ```

3. **Error Handling**: Always check for errors
   ```python
   content = await fetch(url=url)
   if "Error fetching document" in content:
       # Handle error
       pass
   ```

## Testing the Integration

### Unit Tests

```python
# Test search
async def test_search():
    results = await docs_manager.search_docs("fabric", limit=3)
    assert "Search Results" in results
    assert "https://docs.equinix.com/" in results

# Test fetch
async def test_fetch():
    content = await docs_manager.fetch_doc(
        "https://docs.equinix.com/fabric"
    )
    assert "Error" not in content
    assert len(content) > 0

# Test integration
async def test_search_then_fetch():
    # Search
    results = await docs_manager.search_docs("fabric", limit=1)
    
    # Extract first URL from results
    url = extract_first_url(results)
    
    # Fetch
    content = await docs_manager.fetch_doc(url)
    assert len(content) > 100
```

### Manual Testing

```bash
# Start the MCP server
python -m equinix_docs_mcp_server.main

# In another terminal, test with MCP inspector
# or connect through VS Code / ChatGPT
```

## Conclusion

The `search` and `fetch` tools work together to provide:

✅ **Fast Discovery**: Quick search across all documentation
✅ **Complete Content**: Full markdown content retrieval
✅ **Flexible Integration**: Works with ChatGPT, APIs, and custom tools
✅ **Error Resilience**: Graceful handling of missing or unavailable content
✅ **OpenAI MCP Compliant**: Standard interface for AI systems

This enables powerful documentation research capabilities for AI assistants and automated systems.
